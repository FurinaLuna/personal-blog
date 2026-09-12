"""领域异常。

服务层抛领域异常，API 层由统一 handler 翻译成 HTTP 响应——
好处是服务层不依赖 FastAPI，可以脱离 Web 框架被脚本/定时任务复用。
"""

from __future__ import annotations


class DomainError(Exception):
    """所有业务异常的基类。"""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        # 少数异常需要附带响应头（例如 401 的 WWW-Authenticate、429 的 Retry-After），
        # 统一由 handler 透传，避免每个 handler 各写一套
        self.headers = headers or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """唯一约束冲突，例如 slug / 用户名 / 邮箱重复。"""

    status_code = 409
    code = "conflict"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "forbidden"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"


class BadRequestError(DomainError):
    status_code = 400
    code = "bad_request"


class PayloadTooLargeError(DomainError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaTypeError(DomainError):
    status_code = 415
    code = "unsupported_media_type"


class RateLimitedError(DomainError):
    """触发限流。

    带 ``Retry-After``：客户端据此退避而不是立刻重试——
    没有这个头时，客户端往往会原地重试，把限流变成放大攻击。
    """

    status_code = 429
    code = "rate_limited"

    def __init__(self, detail: str = "操作过于频繁，请稍后再试", retry_after: int = 60) -> None:
        super().__init__(detail, headers={"Retry-After": str(max(1, retry_after))})
        self.retry_after = max(1, retry_after)
