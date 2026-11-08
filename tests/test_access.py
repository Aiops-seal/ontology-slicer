"""权限标注。

本模块不鉴权，只按角色查本体里的 visibility_scope 声明。
同一原则：不判定，只转换。
"""

from __future__ import annotations

from ontology_slicer.view import slice_terms


def _access(out: dict) -> dict[str, str]:
    return {m["name"]: m["access"] for m in out["view"]["metrics"]}


def test_no_principal_means_unknown_not_granted(ont):
    """默认放行比默认拒绝危险得多。"""
    assert _access(slice_terms(ont, ["退款额"])) == {"refund_amount": "unknown"}


def test_role_in_scope_is_granted(ont):
    assert _access(slice_terms(ont, ["退款额"], subject="role:hq")) == {"refund_amount": "granted"}


def test_role_out_of_scope_is_denied(ont):
    assert _access(slice_terms(ont, ["退款额"], subject="role:analyst")) == {"refund_amount": "denied"}


def test_denied_metric_is_still_in_the_view(ont):
    """指标名通常不是秘密，数据才是。藏掉只会让模型说『不知道』，
    用户听到的是系统坏了。"""
    out = slice_terms(ont, ["退款额"], subject="role:analyst")
    assert [m["name"] for m in out["view"]["metrics"]] == ["refund_amount"]
    assert out["matched"][0]["name"] == "refund_amount"


def test_wildcard_scope_grants_everyone(ont):
    for role in ("role:analyst", "role:hq", "role:finance", "role:whatever"):
        assert _access(slice_terms(ont, ["ord_qty"], subject=role)) == {"ord_qty": "granted"}


def test_hash_is_identical_across_roles(ont):
    """权限只点亮标记，不改变切片的形状 —— 同参数同 hash 才立得住。"""
    a = slice_terms(ont, ["退款额", "结算金额"], subject="role:hq")
    b = slice_terms(ont, ["退款额", "结算金额"], subject="role:analyst")
    c = slice_terms(ont, ["退款额", "结算金额"])
    assert a["view_hash"] == b["view_hash"] == c["view_hash"]


def test_view_shape_is_identical_across_roles(ont):
    a = slice_terms(ont, ["退款额", "结算金额"], subject="role:hq")["view"]
    b = slice_terms(ont, ["退款额", "结算金额"], subject="role:analyst")["view"]
    strip = lambda view: [{k: v for k, v in m.items() if k != "access"} for m in view["metrics"]]
    assert strip(a) == strip(b)


def test_three_roles_give_three_different_access_maps(ont):
    terms = ["退款额", "同店成交额", "ord_qty"]
    hq = _access(slice_terms(ont, terms, subject="role:hq"))
    analyst = _access(slice_terms(ont, terms, subject="role:analyst"))
    finance = _access(slice_terms(ont, terms, subject="role:finance"))

    assert hq == {"refund_amount": "granted", "same_store_gmv": "granted", "ord_qty": "granted"}
    assert analyst == {"refund_amount": "denied", "same_store_gmv": "denied", "ord_qty": "granted"}
    assert finance == {"refund_amount": "granted", "same_store_gmv": "denied", "ord_qty": "granted"}


def test_properties_carry_no_access_field(ont):
    """属性级权限会让 view 的形状随角色变化，hash 就不再稳定。"""
    props = slice_terms(ont, ["ord_qty"], subject="role:hq")["view"]["properties"]
    assert props
    assert all("access" not in p for p in props)
