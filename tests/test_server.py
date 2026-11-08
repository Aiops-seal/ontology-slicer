"""HTTP 接口。无状态、标准库、零依赖。"""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from ontology_slicer.server import build_server


@pytest.fixture()
def httpd(ont):
    server = build_server(ont, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(httpd, method: str, path: str, body: bytes | None = None):
    host, port = httpd.server_address[:2]
    conn = HTTPConnection(host, port, timeout=5)
    try:
        conn.request(method, path, body=body,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, resp.getheader("Content-Type"), json.loads(raw)
    finally:
        conn.close()


def _post(httpd, payload) -> tuple[int, dict]:
    status, _, data = _request(httpd, "POST", "/slice",
                               json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return status, data


def test_get_root_describes_itself(httpd):
    status, ctype, data = _request(httpd, "GET", "/")
    assert status == 200
    assert ctype == "application/json; charset=utf-8"
    assert data["name"] == "ontology-slicer"
    assert data["ontology_version"] == "1.1"
    assert data["endpoint"] == "POST /slice"
    assert data["domains"] == ["mkp", "ord", "settle"]
    assert data["metrics"] == 12


def test_get_health(httpd):
    status, _, data = _request(httpd, "GET", "/health")
    assert status == 200
    assert data == {"ok": True, "ontology_version": "1.1", "metrics": 12, "properties": 12}


def test_unknown_get_path(httpd):
    status, _, data = _request(httpd, "GET", "/nope")
    assert status == 404
    assert data["ok"] is False
    assert data["error"]["code"] == "BAD_REQUEST"


def test_post_slice_ok(httpd):
    status, data = _post(httpd, {"metric_terms": ["成交额"], "dim_terms": ["城市"]})
    assert status == 200
    assert data["ok"] is True
    assert data["view_hash"].startswith("sha256:")
    assert [m["name"] for m in data["view"]["metrics"]] == ["gmv", "gmv_settle"]


def test_post_slice_carries_access(httpd):
    status, data = _post(httpd, {"metric_terms": ["退款额"],
                                 "principal": {"subject": "role:analyst"}})
    assert status == 200
    assert data["view"]["metrics"][0]["access"] == "denied"


def test_post_unknown_param_returns_400(httpd):
    status, data = _post(httpd, {"metric_terms": ["gmv"], "nope": 1})
    assert status == 400
    assert data["error"]["code"] == "UNKNOWN_PARAM"


def test_post_unknown_domain_returns_400(httpd):
    status, data = _post(httpd, {"metric_terms": ["gmv"], "domain": "nope"})
    assert status == 400
    assert data["error"]["code"] == "UNKNOWN_DOMAIN"


def test_post_invalid_json_returns_400(httpd):
    status, _, data = _request(httpd, "POST", "/slice", b"{not json")
    assert status == 400
    assert data["error"]["code"] == "BAD_REQUEST"


def test_post_empty_body_returns_400(httpd):
    status, _, data = _request(httpd, "POST", "/slice", b"")
    assert status == 400
    assert data["error"]["code"] == "BAD_REQUEST"


def test_post_unknown_path(httpd):
    status, _, data = _request(httpd, "POST", "/nope", b"{}")
    assert status == 404
    assert data["ok"] is False


def test_two_calls_return_the_same_hash(httpd):
    _, first = _post(httpd, {"metric_terms": ["成交额"], "dim_terms": ["城市"]})
    _, second = _post(httpd, {"metric_terms": ["成交额"], "dim_terms": ["城市"]})
    assert first["view_hash"] == second["view_hash"]


def test_server_is_stateless_across_roles(httpd):
    _, a = _post(httpd, {"metric_terms": ["退款额"], "principal": {"subject": "role:hq"}})
    _, b = _post(httpd, {"metric_terms": ["退款额"], "principal": {"subject": "role:analyst"}})
    assert a["view_hash"] == b["view_hash"]
    assert a["view"]["metrics"][0]["access"] == "granted"
    assert b["view"]["metrics"][0]["access"] == "denied"
