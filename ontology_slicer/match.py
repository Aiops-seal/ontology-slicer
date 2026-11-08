"""词 → 指标/属性 的精确对齐。

只做精确匹配。不做同义词扩展、不做拼音纠错、不做 embedding。

理由不是"简单"，是：精确匹配敢说"这三个口径都对，你去问用户"；
检索只会还你 Top-K 让你随便挑，挑错了也没人知道。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import UNKNOWN_DOMAIN, SliceError
from .ontology import Ontology, norm

MATCHED = "matched"
AMBIGUOUS = "ambiguous"
UNMATCHED = "unmatched"

METRIC = "metric"
PROPERTY = "property"


@dataclass
class TermResult:
    term: str
    kind: str
    status: str
    name: str | None = None
    how: str = ""
    candidates: tuple[str, ...] = ()
    reason: str = ""
    hint: str = ""
    glossary_target: str = ""

    def to_dict(self) -> dict:
        if self.status == MATCHED:
            return {"term": self.term, "kind": self.kind, "name": self.name, "how": self.how}
        if self.status == AMBIGUOUS:
            return {
                "term": self.term,
                "kind": self.kind,
                "candidates": list(self.candidates),
                "reason": self.reason,
            }
        return {"term": self.term, "kind": self.kind, "hint": self.hint}


def resolve(
    ont: Ontology,
    term: str,
    kind: str,
    domain: str | None = None,
) -> TermResult:
    """单个词的解析。命中即停，逐层下降。"""
    t = norm(term)
    if not t:
        return TermResult(term, kind, UNMATCHED, hint="本体无此词")

    hits: list[str] = []
    how = ""

    target = ont.glossary.get(t)
    if target is not None:
        tkind, _, tname = target.partition(":")
        if tkind != kind:
            wrong = "属性" if tkind == PROPERTY else "指标"
            mine = "指标" if kind == METRIC else "属性"
            return TermResult(
                term, kind, UNMATCHED,
                hint=f"该词指向{wrong}，位置不对",
                glossary_target=target,
            )
        hits, how = [tname], "glossary"
    else:
        # 名字层、display 层、别名层合并取候选，不做「命中即停」。
        #
        # 分开取会让 GMV 这种词被静默判给 gmv —— 它的名字恰好是 gmv，
        # 而 gmv_settle 也把 GMV 声明成了别名。既然两个指标都声称自己叫这个，
        # 那这个词就是有歧义的，正确输出是让上游去问，不是按层级悄悄选一个。
        #
        # display 一并查：它和 name、alias 一样是本体写下的字段，不是猜的。
        # 脏本体里 display 常没进别名表，不查它「商品件数」就对不上。
        if kind == METRIC:
            name_hit = ont.metric_by_name.get(t)
            display_hits = list(ont.metric_by_display.get(t, ()))
            alias_hits = list(ont.metric_by_alias.get(t, ()))
            hits = ([name_hit] if name_hit else []) + display_hits + alias_hits
            if name_hit:
                how = "name"
            elif display_hits:
                how = "display"
            else:
                how = "alias" if alias_hits else ""
        else:
            name_hit = ont.property_by_name.get(t)
            display_hits = list(ont.property_by_display.get(t, ()))
            alias_hits = list(ont.property_by_alias.get(t, ()))
            hits = ([name_hit] if name_hit else []) + display_hits + alias_hits
            if name_hit:
                how = "name"
            elif display_hits:
                how = "display"
            else:
                how = "alias" if alias_hits else ""

    if not hits:
        return TermResult(term, kind, UNMATCHED, hint="本体无此词")

    if domain is not None:
        if kind == METRIC:
            kept = [h for h in hits if ont.metrics[h].domain == domain]
            elsewhere = sorted({ont.metrics[h].domain for h in hits} - {domain})
        else:
            kept = [h for h in hits if ont.properties[h].domain == domain]
            elsewhere = sorted({ont.properties[h].domain for h in hits} - {domain})
        if not kept:
            return TermResult(
                term, kind, UNMATCHED,
                hint=f"该词只出现在域 {','.join(elsewhere)}",
            )
        hits = kept

    hits = list(dict.fromkeys(hits))

    if kind == METRIC:
        return _decide_metric(ont, term, hits, how)
    return _decide_property(term, hits, how)


def _decide_metric(ont: Ontology, term: str, hits: list[str], how: str) -> TermResult:
    if len(hits) == 1:
        return TermResult(term, METRIC, MATCHED, name=hits[0], how=how,
                          glossary_target=ont.glossary.get(norm(term), ""))

    groups = {ont.metrics[h].group for h in hits}
    if len(groups) == 1 and None not in groups:
        group = next(iter(groups))
        members = ont.group_members(group or "")
        defaults = [n for n in members if ont.metrics[n].default_in_group]
        if len(defaults) == 1:
            return TermResult(
                term, METRIC, MATCHED, name=defaults[0], how="default",
                candidates=tuple(sorted(hits)),
            )
        return TermResult(
            term, METRIC, AMBIGUOUS,
            candidates=tuple(sorted(hits)), reason="family_no_default",
        )

    return TermResult(
        term, METRIC, AMBIGUOUS, candidates=tuple(sorted(hits)), reason="multi_hit",
    )


def _decide_property(term: str, hits: list[str], how: str) -> TermResult:
    if len(hits) == 1:
        return TermResult(term, PROPERTY, MATCHED, name=hits[0], how=how)
    return TermResult(
        term, PROPERTY, AMBIGUOUS, candidates=tuple(sorted(hits)), reason="multi_hit",
    )


def ensure_domain(ont: Ontology, domain: str | None) -> None:
    if domain is not None and domain not in ont.domains:
        raise SliceError(
            UNKNOWN_DOMAIN,
            f"本体没有声明域 {domain!r}",
            path="request.domain",
            candidates=list(ont.domains),
        )
