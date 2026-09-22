"""公开读接口的 HTTP 缓存：ETag + Cache-Control。

## 为什么需要它

前台所有只读接口（文章列表/详情/归档/搜索、分类、标签、系列、站点信息、评论列表）
此前**没有任何缓存头**，于是：

- 浏览器每次前进/后退、每次二次访问都要重新发一次真实请求并全量查库；
- 中间任何一层缓存（CDN、反代）都无从判断内容有没有变，只能一律回源；
- 唯一带 `Cache-Control` 的只有 RSS 与 sitemap（见 `api/feed.py`）。

对一个"读多写少"的博客来说这是最划算的一类优化：**服务端一行 SQL 都不用动**，
却能把重复访问的数据库压力直接消掉。

## 设计取舍

1. **ETag 由响应体本身算**（`sha256(body)` 取前 32 位，弱验证器）。
   也可以用 `max(updated_at)` 之类的"版本号"，但那要求每个查询都把版本信息
   带出来，且任何一个漏掉的相关表都会产出**过期 ETag**——
   一旦 ETag 说谎，客户端会拿着 304 一直看旧内容，属于最难排查的一类 bug。
   哈希响应体不可能说谎：内容变了，ETag 必变。
2. **只对匿名 GET 生效**。带 `Authorization` 的请求一律跳过：
   同一个 URL 对不同用户可能不同（草稿、待审评论），
   共享缓存一旦命中就是越权读。
3. **只对白名单前缀生效**，且排除 `/manage`（后台列表与前台共用一个前缀）。
4. **max-age 只给 60 秒**。定时发布的文章到点才可见，
   缓存太久会让"我明明设了 10:00 发布，10:01 还没出现"变成投诉。
   60 秒是"体感即时"与"挡住重复访问"的平衡点。
5. 非 200 一律不加缓存头：错误页、401、404 被缓存住会很难自救。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# 允许被公开缓存的路径前缀（都是前台只读接口）
CACHEABLE_PREFIXES = (
    "/api/v1/articles",
    "/api/v1/categories",
    "/api/v1/tags",
    "/api/v1/series",
    "/api/v1/site",
    "/api/v1/comments",
)

# 与前台共用前缀、但属于后台/个人化的路径标记
_EXCLUDED_MARKERS = ("/manage", "/revisions")

# 前台内容的缓存时长（秒）。改大之前请先想清楚"定时发布"的体感。
PUBLIC_MAX_AGE = 60

_ETAG_HEADER = "ETag"
_CACHE_CONTROL = "Cache-Control"


def is_cacheable_request(request: Request) -> bool:
    """这个请求是否允许被公开缓存。"""
    if request.method not in ("GET", "HEAD"):
        return False
    # 带凭证的请求一律不缓存：同一 URL 对不同用户可能不同，共享缓存命中即越权
    if request.headers.get("authorization"):
        return False
    path = request.url.path
    if not path.startswith(CACHEABLE_PREFIXES):
        return False
    return not any(marker in path for marker in _EXCLUDED_MARKERS)


def make_etag(body: bytes) -> str:
    """弱 ETag：`W/"<sha256 前 32 位>"`。

    用弱验证器是因为这里的字节级差异（比如 JSON 字段顺序、时间戳精度）
    不必然代表语义差异；弱 ETag 允许中间层做语义比较。
    """
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def if_none_match_hits(header: str | None, etag: str) -> bool:
    """`If-None-Match` 是否命中。

    按 RFC 9110：`*` 匹配任何当前表示；否则是逗号分隔的候选列表，
    逐个**精确**比较（弱验证器比较时忽略 `W/` 前缀，这里同时接受两种写法）。
    """
    if not header:
        return False
    candidates = [item.strip() for item in header.split(",")]
    if "*" in candidates:
        return True
    normalized = etag.removeprefix("W/")
    return any(candidate.removeprefix("W/") == normalized for candidate in candidates)


class PublicCacheMiddleware(BaseHTTPMiddleware):
    """给公开读接口补上 `ETag` 与 `Cache-Control`，并处理条件请求（304）。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        if not is_cacheable_request(request) or response.status_code != 200:
            return response

        # 需要读完 body 才能算哈希。这里只对白名单里的 JSON 接口生效，
        # 都是几 KB 的响应，不存在"把大文件读进内存"的风险。
        body = b"".join([chunk async for chunk in response.body_iterator])

        etag = make_etag(body)
        # 复制原响应头，但丢掉 content-length：下面会重建一个响应体，
        # 让 Starlette 按新的字节数重新算，避免出现"头部说 A、实际 B"。
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() != "content-length"
        }
        headers[_ETAG_HEADER] = etag
        headers[_CACHE_CONTROL] = f"public, max-age={PUBLIC_MAX_AGE}"

        if if_none_match_hits(request.headers.get("if-none-match"), etag):
            # 304 必须**不带响应体**，但 ETag / Cache-Control 要一致，
            # 否则客户端拿到 304 后无法更新自己缓存的元信息。
            return Response(status_code=304, headers=headers)

        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )
