"""请求级中间件。"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.utils.logging import REQUEST_ID_HEADER, sanitize_request_id, set_request_id

logger = logging.getLogger("blog")

# 探活接口不记访问日志：编排系统每秒都在打，会把真正的请求淹掉
_QUIET_PATHS = frozenset({"/health", "/ready"})


class HeadMethodMiddleware:
    """让 HEAD 请求复用 GET 处理器。

    FastAPI 不像 Starlette 的 ``Route`` 那样给 GET 自动补上 HEAD
    （``starlette/routing.py`` 里有 ``if "GET" in methods: methods.add("HEAD")``，
    ``fastapi/routing.py`` 的 APIRoute 没跟这条），于是 ``HEAD /api/v1/articles``
    会得到 **405**。而 HEAD 恰恰是这些场景的常用手段：

    - 缓存 / CDN 的内容再验证（先问"变了吗"，不传正文）；
    - 监控与探活的轻量探测；
    - 排查时最顺手的 ``curl -I``。

    实现要点：

    1. 只改**内层** scope 的方法名，外层（日志、限流、CORS）看到的仍是 HEAD ——
       否则访问日志会把 HEAD 记成 GET，观测数据就失真了；
    2. 正文一律丢掉，但**头部原样保留** —— 包括 ``Content-Length``：
       HEAD 的语义就是"头部与 GET 完全一致，只是没有正文"；
    3. 必须放在 PublicCache **外层**：ETag 是对响应体取哈希算出来的，
       如果在它之前就把正文清空，所有资源的 ETag 会变成同一个空串的哈希。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "HEAD":
            await self.app(scope, receive, send)
            return

        inner: Scope = {**scope, "method": "GET"}

        async def send_headers_only(message: Message) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                message = {**message, "body": b""}
            await send(message)

        await self.app(inner, receive, send_headers_only)

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return f"{type(self).__name__}({self.app!r})"


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
