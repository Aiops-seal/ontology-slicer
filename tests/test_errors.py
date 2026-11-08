"""请求校验与错误码。

错误对象必须是 {code, path, message}，不许返回 list[str]——
调用方要按 code 决定是"改请求重试"还是"停下来报错"，字符串匹配不可靠。
"""

from __future__ import annotations

import pytest

from ontology_slicer.errors import (
    BAD_LIMIT,
    BAD_PRINCIPAL,
    BAD_REQUEST,
    UNKNOWN_DOMAIN,
    UNKNOWN_PARAM,
    SliceError,
)
from ontology_slicer.view import slice_view


def _err(ont, request) -> SliceError:
    with pytest.raises(SliceError) as exc:
        slice_view(ont, request)
    return exc.value


def test_missing_metric_terms(ont):
    exc = _err(ont, {})
    assert exc.code == BAD_REQUEST
    assert "metric_terms" in exc.message


def test_metric_terms_must_be_a_list(ont):
    assert _err(ont, {"metric_terms": "gmv"}).code == BAD_REQUEST


def test_metric_terms_items_must_be_strings(ont):
    exc = _err(ont, {"metric_terms": [123]})
    assert exc.code == BAD_REQUEST
    assert exc.path == "request.metric_terms[0]"


def test_empty_metric_terms_is_valid(ont):
    out = slice_view(ont, {"metric_terms": []})
    assert out["ok"] is True
    assert out["view"]["metrics"] == []


def test_unknown_field_is_rejected(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "filter_terms": ["华东"]})
    assert exc.code == UNKNOWN_PARAM
    assert exc.candidates == [
        "metric_terms", "dim_terms", "domain", "principal",
        "max_metrics", "max_properties", "include_relations",
    ]


def test_filter_terms_is_gone_for_good(ont):
    """filter_terms 装的是值，值域在数据层。切片器不认识『华东』。"""
    exc = _err(ont, {"metric_terms": ["gmv"], "filter_terms": []})
    assert exc.code == UNKNOWN_PARAM


def test_max_hops_is_gone_for_good(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "max_hops": 2})
    assert exc.code == UNKNOWN_PARAM


@pytest.mark.parametrize("value", [0, -1, 51, 999])
def test_max_metrics_out_of_range(ont, value):
    exc = _err(ont, {"metric_terms": ["gmv"], "max_metrics": value})
    assert exc.code == BAD_LIMIT


@pytest.mark.parametrize("value", ["8", 1.5, True, None])
def test_max_metrics_wrong_type(ont, value):
    if value is None:
        slice_view(ont, {"metric_terms": ["gmv"], "max_metrics": None})
        return
    exc = _err(ont, {"metric_terms": ["gmv"], "max_metrics": value})
    assert exc.code == BAD_LIMIT


def test_max_properties_out_of_range(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "max_properties": 201})
    assert exc.code == BAD_LIMIT


def test_principal_must_be_object(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "principal": "role:hq"})
    assert exc.code == BAD_PRINCIPAL


def test_principal_subject_must_start_with_role(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "principal": {"subject": "zhangsan"}})
    assert exc.code == BAD_PRINCIPAL
    assert "role:" in exc.message


def test_principal_rejects_row_level_conditions(ont):
    """把 region_ids 递进来，等于把编译器的活塞给切片器。"""
    exc = _err(ont, {
        "metric_terms": ["gmv"],
        "principal": {"subject": "role:hq", "region_ids": ["R01"]},
    })
    assert exc.code == BAD_PRINCIPAL
    assert "permissions" in exc.message


def test_unknown_domain(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "domain": "nope"})
    assert exc.code == UNKNOWN_DOMAIN
    assert exc.candidates == ["mkp", "ord", "settle"]


def test_domain_must_be_non_empty_string(ont):
    assert _err(ont, {"metric_terms": ["gmv"], "domain": ""}).code == BAD_REQUEST


def test_include_relations_must_be_bool(ont):
    assert _err(ont, {"metric_terms": ["gmv"], "include_relations": "yes"}).code == BAD_REQUEST


def test_error_dict_shape(ont):
    exc = _err(ont, {"metric_terms": ["gmv"], "domain": "nope"})
    payload = exc.to_dict()
    assert set(payload) == {"code", "path", "message", "candidates"}
    assert payload["path"] == "request.domain"
    assert isinstance(payload["message"], str)


def test_error_dict_omits_candidates_when_absent(ont):
    exc = _err(ont, {"metric_terms": "gmv"})
    assert "candidates" not in exc.to_dict()
