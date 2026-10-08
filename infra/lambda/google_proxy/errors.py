import time
from typing import Any, Dict, Optional


class ProxyError(Exception):
    def __init__(self, status_code: int, message: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra or {}


def remaining_seconds(deadline: float, *, limit: float = 27) -> float:
    remaining = min(limit, deadline - time.monotonic())
    if remaining <= 0:
        raise ProxyError(504, "proxy request deadline exceeded")
    return remaining


class AuthError(Exception):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class WorkflowError(Exception):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
