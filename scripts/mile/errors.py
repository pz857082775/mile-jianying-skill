"""统一错误类型：所有对外错误都带稳定的 ``code``，便于 Agent 与脚本判断。"""


class MileError(Exception):
    """米乐剪辑层的统一异常。"""

    def __init__(self, code: str, reason: str, **details):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.details = details

    def to_dict(self) -> dict:
        payload = {"code": self.code, "reason": self.reason}
        if self.details:
            payload["details"] = _jsonable(self.details)
        return payload

    def __str__(self) -> str:
        return f"[{self.code}] {self.reason}"


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
