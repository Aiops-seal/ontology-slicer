"""本体加载与索引。

只读，只校验本模块读的字段（SPEC §3.5）。

物理层的错——`expr` 里的列名、`{f}.` 前缀、`grain` 是否齐备、`scoped_filters`
引用的表是否越界——全部归 mqlc。这里不碰，碰了就变成半个编译器。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import BAD_ONTOLOGY, SliceError

ONTOLOGY_VERSION = "1.1"

COMPARE_TYPES = ("yoy", "mom", "wow")
GRANULARITIES = ("day", "week", "month", "quarter", "year")
DIM_MODES = ("all", "only", "except")
PROPERTY_KINDS = ("enum", "string", "number", "time")
WILDCARD = "*"


def _bad(message: str, path: str = "ontology") -> SliceError:
    return SliceError(BAD_ONTOLOGY, message, path=path)


def norm(term: str) -> str:
    """只做两件事：去首尾空白、ASCII 转小写。

    不做全半角、不做繁简、不去连字符——那些都是"猜"。一旦做了，
    就无法解释为什么 `ＧＭＶ` 能对上而别的写法对不上。
    """
    return term.strip().lower()


@dataclass(frozen=True)
class TableDef:
    name: str
    kind: str  # fact | dimension


@dataclass(frozen=True)
class RelationDef:
    src: str
    dst: str
    cardinality: str


@dataclass(frozen=True)
class DimensionPolicy:
    mode: str
    dimensions: tuple[str, ...] = ()

    def allows(self, prop: str) -> bool:
        if self.mode == "all":
            return True
        if self.mode == "only":
            return prop in self.dimensions
        return prop not in self.dimensions

    def to_dict(self) -> dict:
        if self.mode == "all":
            return {"mode": "all"}
        return {"mode": self.mode, "dimensions": list(self.dimensions)}


@dataclass(frozen=True)
class MetricDef:
    name: str
    display: str
    domain: str
    aliases: tuple[str, ...]
    do_not: tuple[str, ...]
    visibility_scope: tuple[str, ...]
    policy: DimensionPolicy
    supports_compare: bool
    group: str | None
    default_in_group: bool
    formula: str
    fact_tables: tuple[str, ...]
    scoped_tables: tuple[str, ...]

    def visible_to(self, subject: str) -> bool:
        return WILDCARD in self.visibility_scope or subject in self.visibility_scope


@dataclass(frozen=True)
class PropertyDef:
    name: str
    display: str
    kind: str
    domain: str
    aliases: tuple[str, ...]
    do_not: tuple[str, ...]
    tables: tuple[str, ...]


class Ontology:
    """启动时一次性建好索引，之后只读。"""

    def __init__(
        self,
        version: str,
        capabilities: dict,
        glossary: dict[str, str],
        tables: dict[str, TableDef],
        relations: tuple[RelationDef, ...],
        metrics: dict[str, MetricDef],
        properties: dict[str, PropertyDef],
    ) -> None:
        self.version = version
        self.capabilities = capabilities
        self.glossary = glossary
        self.tables = tables
        self.relations = relations
        self.metrics = metrics
        self.properties = properties

        self.metric_by_name: dict[str, str] = {}
        self.metric_by_alias: dict[str, list[str]] = {}
        for name, m in metrics.items():
            self.metric_by_name[norm(name)] = name
            for alias in m.aliases:
                self.metric_by_alias.setdefault(norm(alias), []).append(name)

        self.property_by_name: dict[str, str] = {}
        self.property_by_alias: dict[str, list[str]] = {}
        for name, p in properties.items():
            self.property_by_name[norm(name)] = name
            for alias in p.aliases:
                self.property_by_alias.setdefault(norm(alias), []).append(name)

        self.groups: dict[str, list[str]] = {}
        for name, m in metrics.items():
            if m.group:
                self.groups.setdefault(m.group, []).append(name)
        for members in self.groups.values():
            members.sort()

        self._adj: dict[str, list[str]] = {name: [] for name in tables}
        for rel in relations:
            self._adj[rel.src].append(rel.dst)
            self._adj[rel.dst].append(rel.src)

        self.domains = tuple(sorted({m.domain for m in metrics.values()}
                                    | {p.domain for p in properties.values()}))

    def neighbors(self, table: str) -> list[str]:
        return self._adj.get(table, [])

    def group_members(self, group: str | None) -> list[str]:
        return self.groups.get(group, []) if group else []

    # ---- 加载 -------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Ontology":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise _bad(f"本体文件不存在：{path}") from None
        except json.JSONDecodeError as exc:
            raise _bad(f"本体不是合法 JSON：{exc}") from None
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: object) -> "Ontology":
        if not isinstance(raw, dict):
            raise _bad("本体顶层必须是对象")

        version = raw.get("version")
        if version != ONTOLOGY_VERSION:
            raise _bad(
                f"本体 version 必须是 {ONTOLOGY_VERSION!r}，实际是 {version!r}；"
                "不做向后兼容——兼容层会掩盖两边看到的本体不一样这件事",
                "version",
            )

        capabilities = cls._load_capabilities(raw.get("capabilities"))
        tables = cls._load_tables(raw.get("tables"))
        relations = cls._load_relations(raw.get("relations"), tables)
        properties = cls._load_properties(raw.get("properties"), tables)
        metrics = cls._load_metrics(raw.get("metrics"), tables, properties)
        glossary = cls._load_glossary(raw.get("glossary"), metrics, properties)

        return cls(version, capabilities, glossary, tables, relations, metrics, properties)

    @staticmethod
    def _load_capabilities(raw: object) -> dict:
        if not isinstance(raw, dict):
            raise _bad("本体缺少 capabilities（本体级能力声明必填）", "capabilities")
        path = "capabilities"

        compare = raw.get("supports_compare")
        if not isinstance(compare, list):
            raise _bad("capabilities.supports_compare 必须是数组", f"{path}.supports_compare")
        for item in compare:
            if item not in COMPARE_TYPES:
                raise _bad(f"supports_compare 出现非法值 {item!r}，合法值 {list(COMPARE_TYPES)}",
                           f"{path}.supports_compare")

        gran = raw.get("min_granularity")
        if gran not in GRANULARITIES:
            raise _bad(f"min_granularity 非法：{gran!r}，合法值 {list(GRANULARITIES)}",
                       f"{path}.min_granularity")

        days = raw.get("max_scan_days")
        if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
            raise _bad("max_scan_days 必须是正整数", f"{path}.max_scan_days")

        dialects = raw.get("dialects_verified")
        if not isinstance(dialects, list) or not all(isinstance(d, str) for d in dialects):
            raise _bad("dialects_verified 必须是字符串数组", f"{path}.dialects_verified")

        return {
            "supports_compare": list(compare),
            "min_granularity": gran,
            "max_scan_days": days,
            "dialects_verified": list(dialects),
        }

    @staticmethod
    def _load_tables(raw: object) -> dict[str, TableDef]:
        if not isinstance(raw, list) or not raw:
            raise _bad("本体缺少 tables", "tables")
        out: dict[str, TableDef] = {}
        for i, t in enumerate(raw):
            path = f"tables[{i}]"
            if not isinstance(t, dict) or not t.get("name"):
                raise _bad("表定义必须是带 name 的对象", path)
            name = str(t["name"])
            if name in out:
                raise _bad(f"表 {name} 重复声明", path)
            kind = t.get("kind")
            if kind not in ("fact", "dimension"):
                raise _bad(f"表 {name} 的 kind 必须是 fact 或 dimension", path)
            out[name] = TableDef(name, kind)
        return out

    @staticmethod
    def _load_relations(raw: object, tables: dict[str, TableDef]) -> tuple[RelationDef, ...]:
        out: list[RelationDef] = []
        for i, r in enumerate(raw or []):
            path = f"relations[{i}]"
            if not isinstance(r, dict):
                raise _bad("关系必须是对象", path)
            src, dst = str(r.get("from", "")), str(r.get("to", ""))
            if src not in tables or dst not in tables:
                raise _bad(f"关系两端必须都是已声明的表：{src} -> {dst}", path)
            card = r.get("cardinality")
            if card not in ("many_to_one", "one_to_many", "one_to_one"):
                raise _bad(f"cardinality 非法：{card!r}", path)
            out.append(RelationDef(src, dst, str(card)))
        return tuple(out)

    @staticmethod
    def _load_properties(raw: object, tables: dict[str, TableDef]) -> dict[str, PropertyDef]:
        if not isinstance(raw, list):
            raise _bad("本体缺少 properties", "properties")
        out: dict[str, PropertyDef] = {}
        for i, p in enumerate(raw):
            path = f"properties[{i}]"
            if not isinstance(p, dict) or not p.get("name"):
                raise _bad("属性必须是带 name 的对象", path)
            name = str(p["name"])
            if name in out:
                raise _bad(f"属性 {name} 重复声明", path)

            domain = p.get("domain")
            if not isinstance(domain, str) or not domain:
                raise _bad(f"属性 {name} 缺少 domain（缺域就无法消歧）", f"{path}.domain")
            aliases = p.get("aliases")
            if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
                raise _bad(f"属性 {name} 的 aliases 必须是字符串数组（可空，但必须写出来）",
                           f"{path}.aliases")
            kind = p.get("kind", "string")
            if kind not in PROPERTY_KINDS:
                raise _bad(f"属性 {name} 的 kind 非法：{kind!r}", path)

            bindings = p.get("bindings")
            if not isinstance(bindings, list) or not bindings:
                raise _bad(f"属性 {name} 缺少 bindings（闭包要用）", f"{path}.bindings")
            bound: list[str] = []
            for j, b in enumerate(bindings):
                tname = str((b or {}).get("table", "")) if isinstance(b, dict) else ""
                if tname not in tables:
                    raise _bad(f"属性 {name} 的 binding 指向不存在的表 {tname!r}",
                               f"{path}.bindings[{j}]")
                bound.append(tname)

            do_not = p.get("do_not") or []
            if not isinstance(do_not, list):
                raise _bad(f"属性 {name} 的 do_not 必须是数组", f"{path}.do_not")

            out[name] = PropertyDef(
                name=name,
                display=str(p.get("display", name)),
                kind=str(kind),
                domain=domain,
                aliases=tuple(aliases),
                do_not=tuple(str(x) for x in do_not),
                tables=tuple(bound),
            )
        return out

    @staticmethod
    def _load_metrics(
        raw: object,
        tables: dict[str, TableDef],
        properties: dict[str, PropertyDef],
    ) -> dict[str, MetricDef]:
        if not isinstance(raw, list):
            raise _bad("本体缺少 metrics", "metrics")
        out: dict[str, MetricDef] = {}
        for i, m in enumerate(raw):
            path = f"metrics[{i}]"
            if not isinstance(m, dict) or not m.get("name"):
                raise _bad("指标必须是带 name 的对象", path)
            name = str(m["name"])
            if name in out:
                raise _bad(f"指标 {name} 重复声明", path)

            domain = m.get("domain")
            if not isinstance(domain, str) or not domain:
                raise _bad(f"指标 {name} 缺少 domain（缺域就无法消歧）", f"{path}.domain")

            aliases = m.get("aliases")
            if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
                raise _bad(
                    f"指标 {name} 的 aliases 必须是字符串数组——可空，但必须显式写出来，"
                    "好让『这个指标没有别名』成为一个声明而不是一个省略",
                    f"{path}.aliases",
                )

            scope = m.get("visibility_scope")
            if not isinstance(scope, list) or not scope or not all(isinstance(s, str) for s in scope):
                raise _bad(
                    f"指标 {name} 的 visibility_scope 必须是非空字符串数组，公开也要写成 [\"*\"]. "
                    "代码里没有默认值可填——判断归本体，不归代码",
                    f"{path}.visibility_scope",
                )

            policy = _load_policy(m.get("dimension_policy"), properties, name, path)

            supports = m.get("supports_compare")
            if not isinstance(supports, bool):
                raise _bad(f"指标 {name} 的 supports_compare 必须是布尔值", f"{path}.supports_compare")

            group = m.get("ambiguity_group")
            if group is not None and not isinstance(group, str):
                raise _bad(f"指标 {name} 的 ambiguity_group 必须是字符串", f"{path}.ambiguity_group")
            default = m.get("default_in_group", False)
            if not isinstance(default, bool):
                raise _bad(f"指标 {name} 的 default_in_group 必须是布尔值",
                           f"{path}.default_in_group")
            if default and not group:
                raise _bad(f"指标 {name} 声明了 default_in_group 但不在任何 ambiguity_group 里",
                           f"{path}.default_in_group")

            bindings = m.get("bindings")
            if not isinstance(bindings, list) or not bindings:
                raise _bad(f"指标 {name} 缺少 bindings（闭包要用）", f"{path}.bindings")
            fact_tables: list[str] = []
            formula = ""
            for j, b in enumerate(bindings):
                bpath = f"{path}.bindings[{j}]"
                if not isinstance(b, dict):
                    raise _bad("binding 必须是对象", bpath)
                tname = str(b.get("table", ""))
                if tname not in tables:
                    raise _bad(f"指标 {name} 的 binding 指向不存在的表 {tname!r}", bpath)
                fact_tables.append(tname)
                if not formula:
                    formula = str(b.get("expr", ""))

            scoped = m.get("scoped_filters") or []
            scoped_tables: list[str] = []
            for j, sf in enumerate(scoped):
                spath = f"{path}.scoped_filters[{j}]"
                if not isinstance(sf, dict):
                    raise _bad("scoped_filter 必须是对象", spath)
                tname = str(sf.get("table", ""))
                if tname not in tables:
                    raise _bad(f"指标 {name} 的 scoped_filter 指向不存在的表 {tname!r}", spath)
                scoped_tables.append(tname)

            do_not = m.get("do_not") or []
            if not isinstance(do_not, list):
                raise _bad(f"指标 {name} 的 do_not 必须是数组", f"{path}.do_not")

            out[name] = MetricDef(
                name=name,
                display=str(m.get("display", name)),
                domain=domain,
                aliases=tuple(aliases),
                do_not=tuple(str(x) for x in do_not),
                visibility_scope=tuple(scope),
                policy=policy,
                supports_compare=supports,
                group=group,
                default_in_group=default,
                formula=formula,
                fact_tables=tuple(dict.fromkeys(fact_tables)),
                scoped_tables=tuple(dict.fromkeys(scoped_tables)),
            )

        # 同组多个默认 = 本体自相矛盾，加载时就拦，不留到运行时
        defaults: dict[str, list[str]] = {}
        for m in out.values():
            if m.group and m.default_in_group:
                defaults.setdefault(m.group, []).append(m.name)
        for group, names in defaults.items():
            if len(names) > 1:
                raise _bad(
                    f"口径组 {group!r} 里声明了多个默认指标 {sorted(names)}——"
                    "组内只能有一个 default，否则『默认用哪个』又变回代码要判断的事",
                    "metrics",
                )

        return out

    @staticmethod
    def _load_glossary(
        raw: object,
        metrics: dict[str, MetricDef],
        properties: dict[str, PropertyDef],
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for i, g in enumerate(raw or []):
            path = f"glossary[{i}]"
            if not isinstance(g, dict):
                raise _bad("glossary 条目必须是对象", path)
            term = g.get("term")
            target = g.get("maps_to")
            if not isinstance(term, str) or not term.strip():
                raise _bad("glossary 条目缺少 term", path)
            if not isinstance(target, str) or ":" not in target:
                raise _bad(f"glossary 的 maps_to 必须是 'metric:x' 或 'property:x'，实际 {target!r}",
                           path)
            kind, _, name = target.partition(":")
            if kind == "metric":
                if name not in metrics:
                    raise _bad(f"glossary 指向不存在的指标 {name!r}", path)
            elif kind == "property":
                if name not in properties:
                    raise _bad(f"glossary 指向不存在的属性 {name!r}", path)
            else:
                raise _bad(f"glossary 的 maps_to 前缀只能是 metric 或 property，实际 {kind!r}", path)
            out[norm(term)] = target
        return out


def _load_policy(
    raw: object,
    properties: dict[str, PropertyDef],
    metric_name: str,
    path: str,
) -> DimensionPolicy:
    """`dimension_policy` 取代了原来的 allowed_/forbidden_ 两个字段。

    那两个字段的空数组语义是反的（一个空表示不限、一个空表示全禁），
    一定会被写错。
    """
    if not isinstance(raw, dict):
        raise _bad(f"指标 {metric_name} 缺少 dimension_policy", f"{path}.dimension_policy")
    mode = raw.get("mode")
    if mode not in DIM_MODES:
        raise _bad(
            f"指标 {metric_name} 的 dimension_policy.mode 非法：{mode!r}，"
            f"合法值 {list(DIM_MODES)}",
            f"{path}.dimension_policy.mode",
        )
    dims = raw.get("dimensions") or []
    if not isinstance(dims, list) or not all(isinstance(d, str) for d in dims):
        raise _bad(f"指标 {metric_name} 的 dimension_policy.dimensions 必须是字符串数组",
                   f"{path}.dimension_policy.dimensions")
    if mode == "all" and dims:
        raise _bad(f"指标 {metric_name} 的 mode=all 不该带 dimensions，"
                   "空维度和『不限』混在一起是两类语义，不要写在同一行",
                   f"{path}.dimension_policy")
    if mode != "all" and not dims:
        raise _bad(f"指标 {metric_name} 的 mode={mode} 必须列出 dimensions",
                   f"{path}.dimension_policy.dimensions")
    for d in dims:
        if d not in properties:
            raise _bad(f"指标 {metric_name} 的 dimension_policy 引用了不存在的属性 {d!r}——"
                       "拼错的维度名会让限制静默失效",
                       f"{path}.dimension_policy.dimensions")
    return DimensionPolicy(str(mode), tuple(dims))
