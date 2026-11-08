"""错误码。

每个错误对象必须能被调用方按 code 分支处理，不许返回 list[str]。
字符串匹配不可靠，而且改一个字就静默失效。
"""

from __future__ import annotations

BAD_REQUEST = "BAD_REQUEST"
UNKNOWN_PARAM = "UNKNOWN_PARAM"
BAD_LIMIT = "BAD_LIMIT"
BAD_PRINCIPAL = "BAD_PRINCIPAL"
UNKNOWN_DOMAIN = "UNKNOWN_DOMAIN"
BAD_ONTOLOGY = "BAD_ONTOLOGY"


class SliceError(Exception):
    """本模块唯一的异常类型。"""

    def __init__(
        self,
        code: str,
        message: str,
        path: str = "",
        candidates: list[str] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.path = path
        self.candidates = candidates

    def to_dict(self) -> dict:
        out: dict = {"code": self.code, "path": self.path, "message": self.message}
        if self.candidates is not None:
            out["candidates"] = list(self.candidates)
        return out
