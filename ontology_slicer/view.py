"""切片装配：请求校验 → 匹配 → 闭包 → 截断 → 权限标注 → 指纹。

四步全确定性，零 LLM。这里没有任何行为开关：
没有 strict=False、没有 guess=True、没有 default_domain。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .errors import BAD_LIMIT, BAD_PRINCIPAL, BAD_REQUEST, UNKNOWN_PARAM, SliceError
from .match import (
    AMBIGUOUS,
    MATCHED,
    METRIC,
    PROPERTY,
    TermResult,
    ensure_domain,
    resolve,
)
from .ontology import MetricDef, Ontology, PropertyDef, norm

MAX_METRICS_CEILING = 50
MAX_PROPERTIES_CEILING = 200
DEFAULT_MAX_METRICS = 8
DEFAULT_MAX_PROPERTIES = 30

ALLOWED_FIELDS = (
    "metric_terms",
    "dim_terms",
    "domain",
    "principal",
    "max_metrics",
    "max_properties",
    "include_relations",
)


@dataclass(frozen=True)
class Request:
    metric_terms: tuple[str, ...]
    dim_terms: tuple[str, ...]
    domain: str | None
    subject: str | None
    max_metrics: int
    max_properties: int
    include_relations: bool


def _term_list(raw: object, field: str, required: bool) -> tuple[str, ...]:
    if raw is None:
        if required:
            raise SliceError(BAD_REQUEST, f"缺少 {field}（可以是空数组，但必须写出来）",
                             path=f"request.{field}")
        return ()
    if not isinstance(raw, list):
        raise SliceError(BAD_REQUEST, f"{field} 必须是字符串数组", path=f"request.{field}")
    out: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise SliceError(BAD_REQUEST, f"{field}[{i}] 不是字符串",
                             path=f"request.{field}[{i}]")
        key = norm(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(out)


def _limit(raw: object, field: str, default: int, ceiling: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SliceError(BAD_LIMIT, f"{field} 必须是正整数", path=f"request.{field}")
    if raw <= 0 or raw > ceiling:
        raise SliceError(BAD_LIMIT, f"{field} 必须在 1..{ceiling} 之间，实际 {raw}",
                         path=f"request.{field}")
    return raw


def parse_request(raw: object) -> Request:
    if not isinstance(raw, dict):
        raise SliceError(BAD_REQUEST, "请求顶层必须是对象")

    unknown = sorted(set(raw) - set(ALLOWED_FIELDS))
    if unknown:
        raise SliceError(
            UNKNOWN_PARAM,
            f"请求里有未定义的字段 {unknown}",
            path="request",
            candidates=list(ALLOWED_FIELDS),
        )

    metric_terms = _term_list(raw.get("metric_terms"), "metric_terms", required=True)
    dim_terms = _term_list(raw.get("dim_terms"), "dim_terms", required=False)

    domain = raw.get("domain")
    if domain is not None and (not isinstance(domain, str) or not domain.strip()):
        raise SliceError(BAD_REQUEST, "domain 必须是非空字符串或 null", path="request.domain")
    domain = domain.strip() if isinstance(domain, str) else None

    subject = _subject(raw.get("principal"))

    max_metrics = _limit(raw.get("max_metrics"), "max_metrics",
                         DEFAULT_MAX_METRICS, MAX_METRICS_CEILING)
    max_properties = _limit(raw.get("max_properties"), "max_properties",
                            DEFAULT_MAX_PROPERTIES, MAX_PROPERTIES_CEILING)

    include_relations = raw.get("include_relations", True)
    if not isinstance(include_relations, bool):
        raise SliceError(BAD_REQUEST, "include_relations 必须是布尔值",
                         path="request.include_relations")

    return Request(metric_terms, dim_terms, domain, subject,
                   max_metrics, max_properties, include_relations)


def _subject(raw: object) -> str | None:
    """principal 只收角色。

    这条看起来很硬，但它挡的是三样东西被揉成一团：
    人（身份，网关的事）、角色（指标可见性，这里的事）、
    数据行范围（行级权限，编译器的事）。

    一旦有人把 region_ids 递进来，本模块就得开始想"要不要用它过滤"，
    那时候它已经在往编译器的地盘上越界，而它根本不知道 region 挂在哪张表。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SliceError(BAD_PRINCIPAL, 'principal 必须是 {"subject": "role:..."} 形态',
                         path="request.principal")
    extra = sorted(set(raw) - {"subject"})
    if extra:
        raise SliceError(
            BAD_PRINCIPAL,
            f"principal 里多出 {extra}——只收 subject（角色）。"
            "区域/门店这类行级条件属于 mqlc 的 permissions，不属于切片器",
            path="request.principal",
            candidates=["subject"],
        )
    subject = raw.get("subject")
    if not isinstance(subject, str) or not subject.startswith("role:"):
        raise SliceError(
            BAD_PRINCIPAL,
            'principal.subject 必须以 "role:" 开头（例如 role:hq）',
            path="request.principal.subject",
        )
    return subject


def _ordered_unique(items) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _select_metrics(
    ont: Ontology,
    results: list[TermResult],
    max_metrics: int,
) -> tuple[list[str], list[str]]:
    """三档优先序。前两档永不截断。"""
    tier1: list[str] = []
    tier2: list[str] = []
    for r in results:
        if r.status == MATCHED and r.name:
            tier1.append(r.name)
        elif r.status == AMBIGUOUS:
            tier2.extend(r.candidates)
    tier1 = _ordered_unique(tier1)
    tier2 = [n for n in _ordered_unique(tier2) if n not in tier1]

    siblings: list[str] = []
    for name in tier1 + tier2:
        domain = ont.metrics[name].domain
        for member in ont.group_members(ont.metrics[name].group):
            if member in tier1 or member in tier2:
                continue
            # 同组兄弟只在同域时才带进 view。
            # 否则 domain=ord 的请求会被兄弟关系绕过，把 settle 域的指标塞回来——
            # 那样"域收窄"就只对 matched 生效，对同组失效，等于没收窄。
            if ont.metrics[member].domain != domain:
                continue
            siblings.append(member)
    tier3 = sorted(set(siblings))

    fixed = tier1 + tier2
    if len(fixed) >= max_metrics:
        # 用户明确提到的永不截断，超上限也全留。
        # 反过来做会出现最荒诞的结果：用户问成交额，返回的 view 里没有成交额。
        return fixed, []

    room = max_metrics - len(fixed)
    return fixed + tier3[:room], tier3[room:]


def _select_properties(
    ont: Ontology,
    dim_results: list[TermResult],
    fact_tables: set[str],
    all_tables: set[str],
    max_properties: int,
) -> tuple[list[str], list[str]]:
    tier1 = _ordered_unique(r.name for r in dim_results if r.status == MATCHED)
    tier2 = _ordered_unique(
        c for r in dim_results if r.status == AMBIGUOUS for c in r.candidates
    )
    fixed = tier1 + tier2

    pool = [
        p.name for p in ont.properties.values()
        if any(t in all_tables for t in p.tables)
    ]
    on_fact = sorted(n for n in pool if any(t in fact_tables for t in ont.properties[n].tables))
    on_dim = sorted(n for n in pool if n not in on_fact)

    tail = [n for n in on_fact + on_dim if n not in fixed]

    if len(fixed) >= max_properties:
        return fixed, []
    room = max_properties - len(fixed)
    return fixed + tail[:room], tail[room:]


def _conflicts(
    ont: Ontology,
    dim_results: list[TermResult],
    selected: list[str],
) -> list[dict]:
    """用户要的维度和指标的 dimension_policy 冲突。

    不静默丢弃，单独报出来——信息一旦被静默丢弃，
    上游就永远不知道为什么切片里少了那个维度。
    """
    wanted: list[str] = []
    for r in dim_results:
        if r.status == MATCHED and r.name:
            wanted.append(r.name)
        elif r.status == AMBIGUOUS:
            wanted.extend(r.candidates)

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for prop in _ordered_unique(wanted):
        for name in selected:
            metric = ont.metrics[name]
            if metric.policy.allows(prop):
                continue
            key = (prop, name)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "property": prop,
                "metric": name,
                "reason": "dimension_forbidden",
                "policy": metric.policy.to_dict(),
            })
    return out


def _metric_view(metric: MetricDef, subject: str | None) -> dict:
    if subject is None:
        access = "unknown"
    else:
        access = "granted" if metric.visible_to(subject) else "denied"
    return {
        "name": metric.name,
        "display": metric.display,
        "domain": metric.domain,
        "aliases": list(metric.aliases),
        "formula": metric.formula,
        "do_not": list(metric.do_not),
        "dimension_policy": metric.policy.to_dict(),
        "supports_compare": metric.supports_compare,
        "ambiguity_group": metric.group,
        "default_in_group": metric.default_in_group,
        "access": access,
    }


def _property_view(prop: PropertyDef) -> dict:
    return {
        "name": prop.name,
        "display": prop.display,
        "kind": prop.kind,
        "domain": prop.domain,
        "aliases": list(prop.aliases),
        "do_not": list(prop.do_not),
    }


def _strip_access(obj):
    if isinstance(obj, dict):
        return {k: _strip_access(v) for k, v in obj.items() if k != "access"}
    if isinstance(obj, list):
        return [_strip_access(x) for x in obj]
    return obj


def view_hash(view: dict) -> str:
    canonical = json.dumps(
        _strip_access(view), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def slice_view(ont: Ontology, raw_request: object) -> dict:
    req = parse_request(raw_request)
    ensure_domain(ont, req.domain)

    metric_results = [resolve(ont, t, METRIC, req.domain) for t in req.metric_terms]
    dim_results = [resolve(ont, t, PROPERTY, req.domain) for t in req.dim_terms]

    selected, truncated_metrics = _select_metrics(ont, metric_results, req.max_metrics)

    # ---- 闭包 ------------------------------------------------------
    fact_tables: set[str] = set()
    extra_tables: set[str] = set()
    for name in selected:
        metric = ont.metrics[name]
        fact_tables.update(metric.fact_tables)
        extra_tables.update(metric.scoped_tables)

    all_tables = set(fact_tables) | set(extra_tables)
    if req.include_relations:
        for table in fact_tables:
            for neighbour in ont.neighbors(table):
                if ont.tables[neighbour].kind == "dimension":
                    all_tables.add(neighbour)

    properties, truncated_properties = _select_properties(
        ont, dim_results, fact_tables, all_tables, req.max_properties
    )

    relations = [
        {"from": r.src, "to": r.dst, "cardinality": r.cardinality}
        for r in ont.relations
        if r.src in all_tables and r.dst in all_tables
        and (r.src in fact_tables or r.dst in fact_tables)
    ]
    relations.sort(key=lambda r: (r["from"], r["to"]))

    glossary = []
    for term in req.metric_terms + req.dim_terms:
        target = ont.glossary.get(norm(term))
        if target is not None:
            glossary.append({"term": term, "maps_to": target})
    glossary.sort(key=lambda g: g["term"])

    view = {
        "capabilities": ont.capabilities,
        "metrics": [_metric_view(ont.metrics[n], req.subject) for n in selected],
        "properties": [_property_view(ont.properties[n]) for n in properties],
        "relations": relations,
        "glossary": glossary,
    }

    matched = [r.to_dict() for r in metric_results + dim_results if r.status == MATCHED]
    ambiguous = [r.to_dict() for r in metric_results + dim_results if r.status == AMBIGUOUS]
    unmatched = [r.to_dict() for r in metric_results + dim_results
                 if r.status not in (MATCHED, AMBIGUOUS)]

    return {
        "ok": True,
        "ontology_version": ont.version,
        "view": view,
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "conflicts": _conflicts(ont, dim_results, selected),
        "truncated": {"metrics": truncated_metrics, "properties": truncated_properties},
        "view_hash": view_hash(view),
    }


def slice_terms(
    ont: Ontology,
    metric_terms: list[str],
    dim_terms: list[str] | None = None,
    domain: str | None = None,
    subject: str | None = None,
    max_metrics: int | None = None,
    max_properties: int | None = None,
    include_relations: bool | None = None,
) -> dict:
    """给库里直接调用的便利入口。等价于构造一个请求对象。"""
    request: dict = {"metric_terms": list(metric_terms)}
    if dim_terms is not None:
        request["dim_terms"] = list(dim_terms)
    if domain is not None:
        request["domain"] = domain
    if subject is not None:
        request["principal"] = {"subject": subject}
    if max_metrics is not None:
        request["max_metrics"] = max_metrics
    if max_properties is not None:
        request["max_properties"] = max_properties
    if include_relations is not None:
        request["include_relations"] = include_relations
    return slice_view(ont, request)
