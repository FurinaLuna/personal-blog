"""领域异常。

服务层抛领域异常，API 层由统一 handler 翻译成 HTTP 响应——
好处是服务层不依赖 FastAPI，可以脱离 Web 框架被脚本/定时任务复用。
"""

from __future__ import annotations


class DomainError(Exception):
    """所有业务异常的基类。"""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
