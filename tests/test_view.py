"""切片装配：闭包、截断、冲突。"""

from __future__ import annotations

from ontology_slicer import Ontology
from ontology_slicer.view import slice_terms, slice_view

METRIC_KEYS = {
    "name", "display", "domain", "aliases", "formula", "do_not",
    "dimension_policy", "supports_compare", "ambiguity_group",
    "default_in_group", "access",
}


def test_ambiguous_candidates_all_land_in_view(ont):
    """问『成交额』却只让模型看见一个成交额，等于没问。"""
    out = slice_terms(ont, ["成交额"])
    names = [m["name"] for m in out["view"]["metrics"]]
    assert names == ["gmv", "gmv_settle"]
    assert out["ambiguous"][0]["candidates"] == ["gmv", "gmv_settle"]


def test_group_default_brings_its_sibling_along(ont):
    out = slice_terms(ont, ["订单数"])
    names = [m["name"] for m in out["view"]["metrics"]]
    assert names == ["order_cnt", "order_num"]
    assert out["matched"][0]["how"] == "default"


def test_domain_narrowing_also_narrows_group_siblings(ont):
    """domain=ord 时，settle 域的兄弟不许因为同组关系被塞回来。

    不这样做的话，域收窄只对 matched 生效、对同组失效，等于没收窄。
    """
    out = slice_terms(ont, ["成交额"], domain="ord")
    names = [m["name"] for m in out["view"]["metrics"]]
    assert names == ["gmv"]


def test_sibling_in_another_domain_is_not_pulled_in(ont):
    out = slice_terms(ont, ["jiaoyie"])
    assert [m["name"] for m in out["view"]["metrics"]] == ["gmv"]


def test_unmatched_is_reported_and_not_in_view(ont):
    out = slice_terms(ont, ["毛利"])
    assert out["view"]["metrics"] == []
    assert out["unmatched"] == [{"term": "毛利", "kind": "metric", "hint": "本体无此词"}]


def test_metric_view_carries_no_physical_structure(ont):
    out = slice_terms(ont, ["gmv"])
    metric = out["view"]["metrics"][0]
    assert set(metric) == METRIC_KEYS
    assert "bindings" not in metric
    assert metric["formula"] == "SUM({f}.pay_amount)"


def test_formula_is_given_for_understanding_not_for_output(ont):
    """口径要能看懂，但抄进 MQL 会被 PHYSICAL_LEAK 拦 —— 两把锁。"""
    metric = slice_terms(ont, ["gmv"])["view"]["metrics"][0]
    assert "{f}." in metric["formula"]


def test_capabilities_are_passed_through(ont):
    caps = slice_terms(ont, ["gmv"])["view"]["capabilities"]
    assert caps["supports_compare"] == ["yoy", "mom"]
    assert caps["dialects_verified"] == ["sqlite"]


def test_dimension_conflict_is_reported_not_swallowed(ont):
    out = slice_terms(ont, ["同店成交额"], dim_terms=["门店"])
    assert out["conflicts"] == [{
        "property": "store",
        "metric": "same_store_gmv",
        "reason": "dimension_forbidden",
        "policy": {"mode": "only", "dimensions": ["city", "region"]},
    }]


def test_no_conflict_when_policy_allows(ont):
    assert slice_terms(ont, ["gmv"], dim_terms=["门店"])["conflicts"] == []


def test_dimension_requested_by_user_comes_first(ont):
    out = slice_terms(ont, ["gmv"], dim_terms=["城市"])
    assert out["view"]["properties"][0]["name"] == "city"


def test_relations_are_limited_to_the_closure(ont):
    out = slice_terms(ont, ["jiaoyie"], dim_terms=["城市"])
    pairs = {(r["from"], r["to"]) for r in out["view"]["relations"]}
    assert pairs == {
        ("dwd_order_di", "dim_date"),
        ("dwd_order_di", "dim_product"),
        ("dwd_order_di", "dim_store"),
        ("ads_gmv_1d", "dim_store"),
    }


def test_ambiguous_candidates_drag_their_own_tables_in(ont):
    """『成交额』两个口径都成立，所以两个口径的表都得在闭包里 ——
    否则模型看见了候选却看不出它们各自连哪些维度。"""
    out = slice_terms(ont, ["成交额"])
    pairs = {(r["from"], r["to"]) for r in out["view"]["relations"]}
    assert ("dwd_settle_di", "dim_store") in pairs
    assert ("dwd_order_di", "dim_store") in pairs


def test_every_binding_table_enters_the_closure(ont):
    """gmv 有两个 binding。切片器不选表——选表是编译器的事，
    所以两条路径都进 view，扇出风险由下游自己看 cardinality。"""
    out = slice_terms(ont, ["gmv"])
    edges = {(r["from"], r["to"]): r["cardinality"] for r in out["view"]["relations"]}
    assert edges[("ads_gmv_1d", "dim_store")] == "one_to_many"
    assert edges[("dwd_order_di", "dim_store")] == "many_to_one"


def test_include_relations_false_drops_the_dimension_side(ont):
    out = slice_terms(ont, ["退款额"], include_relations=False)
    assert out["view"]["relations"] == []
    names = {p["name"] for p in out["view"]["properties"]}
    assert "region" not in names


def test_settle_domain_metrics_do_not_drag_ord_tables(ont):
    out = slice_terms(ont, ["结算金额"])
    pairs = {(r["from"], r["to"]) for r in out["view"]["relations"]}
    assert pairs == {("dwd_settle_di", "dim_date"), ("dwd_settle_di", "dim_store")}


def test_glossary_only_lists_terms_used_in_this_request(ont):
    out = slice_terms(ont, ["销售业绩"])
    assert out["view"]["glossary"] == [{"term": "销售业绩", "maps_to": "metric:gmv"}]
    other = slice_terms(ont, ["gmv"])
    assert other["view"]["glossary"] == []


def test_property_only_request_is_allowed(ont):
    out = slice_terms(ont, [], dim_terms=["城市"])
    assert out["view"]["metrics"] == []
    assert [p["name"] for p in out["view"]["properties"]] == ["city"]


def test_property_truncation_is_recorded(ont):
    out = slice_terms(ont, ["退款额"], dim_terms=["城市"], max_properties=3)
    props = [p["name"] for p in out["view"]["properties"]]
    assert props == ["city", "channel", "order_date"]
    assert len(out["truncated"]["properties"]) == 7


def test_user_terms_are_never_truncated(ont):
    """max_properties 小于用户明确提到的数量时，用户提的全留，砍的全是推出来的。"""
    out = slice_terms(ont, ["gmv"], dim_terms=["城市", "门店", "大区"], max_properties=2)
    names = [p["name"] for p in out["view"]["properties"]]
    assert names == ["city", "store", "region"]


def _group_ontology(n: int) -> Ontology:
    metrics = []
    for i in range(n):
        metrics.append({
            "name": f"m{i}",
            "display": f"M{i}",
            "domain": "x",
            "aliases": [f"别名{i}"],
            "visibility_scope": ["*"],
            "dimension_policy": {"mode": "all"},
            "supports_compare": True,
            "ambiguity_group": "g",
            "default_in_group": i == 0,
            "bindings": [{"table": "f1", "expr": "SUM({f}.v)"}],
        })
    return Ontology.from_dict({
        "version": "1.1",
        "capabilities": {"supports_compare": [], "min_granularity": "day",
                         "max_scan_days": 30, "dialects_verified": []},
        "tables": [{"name": "f1", "kind": "fact"}],
        "relations": [],
        "metrics": metrics,
        "properties": [],
    })


def test_group_siblings_are_truncated_last():
    ont = _group_ontology(4)  # m0 是默认，m1..m3 是兄弟
    out = slice_terms(ont, ["别名1"], max_metrics=2)
    assert [m["name"] for m in out["view"]["metrics"]] == ["m1", "m0"]
    assert out["truncated"]["metrics"] == ["m2", "m3"]


def test_matched_metric_is_never_truncated_even_over_limit():
    """用户明确提到的那个永远在。反过来做会得到最荒诞的结果：
    用户问成交额，返回的 view 里没有成交额。"""
    ont = _group_ontology(4)
    out = slice_terms(ont, ["别名1"], max_metrics=1)
    assert [m["name"] for m in out["view"]["metrics"]] == ["m1"]
    assert out["truncated"]["metrics"] == []


def test_duplicate_terms_are_collapsed(ont):
    out = slice_terms(ont, ["jiaoyie", "JIAOYIE", " jiaoyie "])
    assert len(out["matched"]) == 1


def test_same_request_twice_gives_same_hash(ont):
    a = slice_terms(ont, ["成交额"], dim_terms=["城市"])
    b = slice_terms(ont, ["成交额"], dim_terms=["城市"])
    assert a["view_hash"] == b["view_hash"]


def test_hash_changes_when_view_changes(ont):
    a = slice_terms(ont, ["成交额"])
    b = slice_terms(ont, ["成交额"], domain="ord")
    assert a["view_hash"] != b["view_hash"]


def test_slice_view_rejects_non_object(ont):
    import pytest

    from ontology_slicer.errors import BAD_REQUEST, SliceError

    with pytest.raises(SliceError) as exc:
        slice_view(ont, ["nope"])
    assert exc.value.code == BAD_REQUEST
