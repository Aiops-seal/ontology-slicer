"""本体加载与校验。

校验的范围刻意只到本模块读的字段（SPEC §3.5）：
物理层的错归 mqlc，这里不碰，碰了就变成半个编译器。
"""

from __future__ import annotations

import pytest

from ontology_slicer import Ontology
from ontology_slicer.errors import BAD_ONTOLOGY, SliceError


def test_fixture_loads_with_expected_shape(ont):
    assert ont.version == "1.1"
    assert len(ont.tables) == 8
    assert len(ont.relations) == 9
    assert len(ont.metrics) == 12
    assert len(ont.properties) == 12
    assert len(ont.glossary) == 4
    assert ont.domains == ("mkp", "ord", "settle")


def test_fixture_has_two_ambiguity_groups(ont):
    assert sorted(ont.groups) == ["gmv_family", "order_family"]


def test_gmv_family_has_no_default(ont):
    """两个口径都成立，本体没声明默认 —— 所以必须追问，代码里没有开关能改这件事。"""
    members = ont.group_members("gmv_family")
    assert members == ["gmv", "gmv_settle"]
    assert not any(ont.metrics[m].default_in_group for m in members)


def test_order_family_has_one_default(ont):
    members = ont.group_members("order_family")
    defaults = [m for m in members if ont.metrics[m].default_in_group]
    assert defaults == ["order_cnt"]


def test_capabilities_are_carried_verbatim(ont):
    assert ont.capabilities["min_granularity"] == "day"
    assert ont.capabilities["max_scan_days"] == 1800
    assert ont.capabilities["dialects_verified"] == ["sqlite"]
    assert ont.capabilities["supports_compare"] == ["yoy", "mom"]


def test_version_1_0_is_rejected(raw):
    raw["version"] = "1.0"
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert exc.value.code == BAD_ONTOLOGY
    assert "不做向后兼容" in exc.value.message


def test_missing_capabilities_is_rejected(raw):
    del raw["capabilities"]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert exc.value.code == BAD_ONTOLOGY


def test_bad_min_granularity_is_rejected(raw):
    raw["capabilities"]["min_granularity"] = "hour"
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert exc.value.code == BAD_ONTOLOGY


def test_metric_without_domain_is_rejected(raw):
    del raw["metrics"][0]["domain"]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "domain" in exc.value.message


def test_metric_without_aliases_is_rejected(raw):
    """aliases 可空，但必须写出来 —— 让『没有别名』成为一个声明而不是一个省略。"""
    del raw["metrics"][0]["aliases"]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "aliases" in exc.value.message


def test_metric_with_empty_aliases_is_accepted(raw):
    raw["metrics"][0]["aliases"] = []
    assert Ontology.from_dict(raw).metrics["m1"].aliases == ()


def test_metric_without_visibility_scope_is_rejected(raw):
    del raw["metrics"][0]["visibility_scope"]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "visibility_scope" in exc.value.message


def test_empty_visibility_scope_is_rejected(raw):
    """公开也要写 ["*"]。代码里没有默认值可填。"""
    raw["metrics"][0]["visibility_scope"] = []
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "公开也要写" in exc.value.message


def test_metric_without_dimension_policy_is_rejected(raw):
    del raw["metrics"][0]["dimension_policy"]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "dimension_policy" in exc.value.message


def test_mode_all_with_dimensions_is_rejected(raw):
    raw["metrics"][0]["dimension_policy"] = {"mode": "all", "dimensions": ["p1"]}
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "两类语义" in exc.value.message


def test_policy_referencing_unknown_property_is_rejected(raw):
    """拼错的维度名会让限制静默失效，所以加载时就拦。"""
    raw["metrics"][0]["dimension_policy"] = {"mode": "except", "dimensions": ["p9"]}
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "静默失效" in exc.value.message


def test_default_outside_any_group_is_rejected(raw):
    raw["metrics"][0]["default_in_group"] = True
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "不在任何 ambiguity_group" in exc.value.message


def test_two_defaults_in_one_group_is_rejected(raw):
    """组内只能有一个默认，否则『默认用哪个』又变回代码要判断的事。"""
    second = copy_metric(raw, "m2")
    raw["metrics"][0]["ambiguity_group"] = "g"
    raw["metrics"][0]["default_in_group"] = True
    second["ambiguity_group"] = "g"
    second["default_in_group"] = True
    raw["metrics"].append(second)
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "只能有一个 default" in exc.value.message


def test_binding_to_unknown_table_is_rejected(raw):
    raw["metrics"][0]["bindings"][0]["table"] = "nope"
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "不存在的表" in exc.value.message


def test_relation_with_unknown_endpoint_is_rejected(raw):
    raw["relations"][0]["to"] = "nope"
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert exc.value.code == BAD_ONTOLOGY


def test_glossary_to_unknown_metric_is_rejected(raw):
    raw["glossary"] = [{"term": "黑话", "maps_to": "metric:nope"}]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "不存在的指标" in exc.value.message


def test_glossary_with_bad_prefix_is_rejected(raw):
    raw["glossary"] = [{"term": "黑话", "maps_to": "table:f1"}]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "前缀只能是 metric 或 property" in exc.value.message


def test_property_without_domain_is_rejected(raw):
    del raw["properties"][0]["domain"]
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "domain" in exc.value.message


def test_duplicate_table_is_rejected(raw):
    raw["tables"].append({"name": "f1", "kind": "fact"})
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict(raw)
    assert "重复声明" in exc.value.message


def test_top_level_not_object_is_rejected():
    with pytest.raises(SliceError) as exc:
        Ontology.from_dict([])
    assert exc.value.code == BAD_ONTOLOGY


def copy_metric(raw: dict, name: str) -> dict:
    import copy

    m = copy.deepcopy(raw["metrics"][0])
    m["name"] = name
    m["display"] = name
    m["aliases"] = [name]
    m.pop("ambiguity_group", None)
    m.pop("default_in_group", None)
    return m
