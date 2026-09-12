"""请求级中间件。"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.logging import REQUEST_ID_HEADER, sanitize_request_id, set_request_id

logger = logging.getLogger("blog")

# 探活接口不记访问日志：编排系统每秒都在打，会把真正的请求淹掉
_QUIET_PATHS = frozenset({"/health", "/ready"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """注入请求 ID，并在响应结束后记一条结构化访问日志。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        set_request_id(request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 异常也要留一条带 request_id 的记录，否则 500 现场只剩框架的默认打印
            logger.exception(
                "请求处理异常",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
            raise

        # 把请求 ID 回给客户端：用户报障时提供它，就能在日志里精确定位这一次请求
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path not in _QUIET_PATHS:
            logger.info(
                "请求完成",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )

        return response
