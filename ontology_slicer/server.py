"""无状态 HTTP 服务。标准库，零依赖。

不设鉴权、不读 header、不写日志到文件、不热更新。
要鉴权就在前面放网关。热更新会让同一进程在不同时刻给出不同 hash，
那比不可用更难查。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import BAD_REQUEST, SliceError
from .ontology import Ontology
from .view import slice_view

NAME = "ontology-slicer"
API_VERSION = "1.0"
MAX_BODY_BYTES = 1 << 20


def make_handler(ont: Ontology):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ontology-slicer/{API_VERSION}"

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _fail(self, status: int, exc: SliceError) -> None:
            self._send(status, {"ok": False, "error": exc.to_dict()})

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", ""):
                self._send(200, {
                    "name": NAME,
                    "api_version": API_VERSION,
                    "ontology_version": ont.version,
                    "domains": list(ont.domains),
                    "metrics": len(ont.metrics),
                    "properties": len(ont.properties),
                    "endpoint": "POST /slice",
                })
            elif path == "/health":
                self._send(200, {
                    "ok": True,
                    "ontology_version": ont.version,
                    "metrics": len(ont.metrics),
                    "properties": len(ont.properties),
                })
            else:
                self._fail(404, SliceError(BAD_REQUEST, f"没有这个路径：{path}",
                                           path="path", candidates=["/", "/health", "/slice"]))

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path != "/slice":
                self._fail(404, SliceError(BAD_REQUEST, f"没有这个路径：{path}",
                                           path="path", candidates=["/slice"]))
                return

            length_raw = self.headers.get("Content-Length")
            try:
                length = int(length_raw or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._fail(400, SliceError(BAD_REQUEST, "请求体为空"))
                return
            if length > MAX_BODY_BYTES:
                self._fail(400, SliceError(BAD_REQUEST, f"请求体超过 {MAX_BODY_BYTES} 字节"))
                return

            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._fail(400, SliceError(BAD_REQUEST, f"请求体不是合法 JSON：{exc}"))
                return

            try:
                result = slice_view(ont, payload)
            except SliceError as exc:
                self._fail(400, exc)
                return
            self._send(200, result)

        def log_message(self, fmt: str, *args) -> None:
            """保持安静。日志归网关或调用方，不在这里落文件。"""
            return

    return Handler


def build_server(ont: Ontology, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(ont))


def serve(ont: Ontology, host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = build_server(ont, host, port)
    actual_host, actual_port = httpd.server_address[:2]
    print(f"{NAME} {API_VERSION} 已就绪  http://{actual_host}:{actual_port}")
    print(f"本体 version={ont.version}  metrics={len(ont.metrics)}  properties={len(ont.properties)}")
    print(f"域：{', '.join(ont.domains)}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
