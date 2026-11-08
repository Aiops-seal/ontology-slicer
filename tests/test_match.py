"""词到结构的精确对齐。

只做精确匹配。这里没有一条用例在测"猜得准不准"，因为模块根本不许猜。
"""

from __future__ import annotations

import pytest

from ontology_slicer.errors import UNKNOWN_DOMAIN, SliceError
from ontology_slicer.match import (
    AMBIGUOUS,
    MATCHED,
    METRIC,
    PROPERTY,
    UNMATCHED,
    ensure_domain,
    resolve,
)


def test_exact_metric_name(ont):
    r = resolve(ont, "ord_qty", METRIC)
    assert (r.status, r.name, r.how) == (MATCHED, "ord_qty", "name")


def test_metric_alias(ont):
    r = resolve(ont, "jiaoyie", METRIC)
    assert (r.status, r.name, r.how) == (MATCHED, "gmv", "alias")


def test_alias_is_case_insensitive_for_ascii(ont):
    assert resolve(ont, "JIAOYIE", METRIC).name == "gmv"
    assert resolve(ont, "  Jiaoyie ", METRIC).name == "gmv"


def test_name_and_alias_layers_are_merged(ont):
    """`gmv` 是小写后的指标名，同时 gmv 和 gmv_settle 都把它声明成别名。

    如果按「名字层命中即停」，这个词会被静默判给 gmv —— 但两个指标都声称
    自己叫这个。合并取候选之后，它就是一个真正的歧义。
    """
    by_name = resolve(ont, "gmv", METRIC)
    assert by_name.status == AMBIGUOUS
    assert by_name.candidates == ("gmv", "gmv_settle")


def test_shared_alias_becomes_ambiguous(ont):
    """两个域都有一份『成交额』，本体没声明默认 —— 必须让上游去问。"""
    r = resolve(ont, "成交额", METRIC)
    assert r.status == AMBIGUOUS
    assert r.candidates == ("gmv", "gmv_settle")
    assert r.reason == "family_no_default"


def test_group_with_default_resolves_instead_of_asking(ont):
    r = resolve(ont, "订单数", METRIC)
    assert (r.status, r.name, r.how) == (MATCHED, "order_cnt", "default")


def test_glossary_wins_over_name_layer(ont):
    r = resolve(ont, "销售业绩", METRIC)
    assert (r.status, r.name, r.how) == (MATCHED, "gmv", "glossary")


def test_glossary_pointing_at_property_is_rejected_in_metric_slot(ont):
    r = resolve(ont, "区域", METRIC)
    assert r.status == UNMATCHED
    assert r.hint == "该词指向属性，位置不对"


def test_glossary_pointing_at_metric_is_rejected_in_property_slot(ont):
    r = resolve(ont, "流水", PROPERTY)
    assert r.status == UNMATCHED
    assert r.hint == "该词指向指标，位置不对"


def test_property_display(ont):
    """「城市」是 city 的 display；它同时也出现在别名表里，display 层优先报出。"""
    r = resolve(ont, "城市", PROPERTY)
    assert (r.status, r.name, r.how) == (MATCHED, "city", "display")


def test_metric_display_is_matchable_even_when_aliases_omit_it(ont):
    """脏本体的典型样子：正式名写了 display，别名表只收口语。

    ord_qty 的 display 是「商品件数」，别名只有 件数/销量/ord_qty。
    不查 display，「商品件数」这个最正式的问法就判成不认识。
    """
    r = resolve(ont, "商品件数", METRIC)
    assert (r.status, r.name, r.how) == (MATCHED, "ord_qty", "display")
    assert resolve(ont, "件数", METRIC).how in ("alias", "display")


def test_display_shared_by_two_metrics_stays_ambiguous(ont):
    """display 参与匹配不能把歧义抹掉：gmv 与 gmv_settle 的 display 都叫「成交额」。"""
    r = resolve(ont, "成交额", METRIC)
    assert r.status == AMBIGUOUS
    assert r.candidates == ("gmv", "gmv_settle")


def test_city_and_city_name_do_not_collide(ont):
    """真本体里 city 和 city_name 并存，别名必须分得开。"""
    assert resolve(ont, "城市", PROPERTY).name == "city"
    assert resolve(ont, "城市名", PROPERTY).name == "city_name"


def test_unknown_word_is_unmatched_with_mechanical_hint(ont):
    r = resolve(ont, "毛利", METRIC)
    assert r.status == UNMATCHED
    assert r.hint == "本体无此词"


def test_value_like_term_is_unmatched(ont):
    """『华东』是值不是词。切片器只认识结构，不认识值 —— 所以它不会假装认识。"""
    assert resolve(ont, "华东", METRIC).status == UNMATCHED
    assert resolve(ont, "华东", PROPERTY).status == UNMATCHED


def test_metric_term_in_property_slot_is_unmatched(ont):
    assert resolve(ont, "gmv", PROPERTY).status == UNMATCHED


def test_domain_narrows_ambiguity_to_a_single_match(ont):
    r = resolve(ont, "成交额", METRIC, domain="ord")
    assert (r.status, r.name) == (MATCHED, "gmv")


def test_domain_narrows_to_the_other_side_too(ont):
    r = resolve(ont, "成交额", METRIC, domain="settle")
    assert (r.status, r.name) == (MATCHED, "gmv_settle")


def test_domain_that_kills_all_candidates_says_where_it_lives(ont):
    r = resolve(ont, "结算金额", METRIC, domain="ord")
    assert r.status == UNMATCHED
    assert r.hint == "该词只出现在域 settle"


def test_unknown_domain_raises(ont):
    with pytest.raises(SliceError) as exc:
        ensure_domain(ont, "nope")
    assert exc.value.code == UNKNOWN_DOMAIN
    assert exc.value.candidates == ["mkp", "ord", "settle"]


def test_none_domain_passes(ont):
    ensure_domain(ont, None)


def test_empty_term_is_unmatched(ont):
    assert resolve(ont, "   ", METRIC).status == UNMATCHED
