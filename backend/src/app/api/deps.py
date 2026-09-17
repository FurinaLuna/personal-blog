"""FastAPI 依赖注入：数据库会话、当前用户、角色门禁、限流。

设计要点：
- ``get_current_user`` 负责认证（你是谁）；
- ``require_admin`` / ``require_author`` 负责授权（你能干什么），
  两者分开，路由上按需组合，避免在每个 handler 里手写 if 判断角色；
- ``rate_limit`` 只针对「可被脚本滥用」的写入口（登录、评论、点赞），
  不铺到全部接口——限流本身也是成本。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models import User, UserRole
from app.repositories import UserRepository
from app.utils.exceptions import PermissionDeniedError, RateLimitedError, UnauthorizedError
from app.utils.ratelimit import limiter
from app.utils.security import decode_token

# auto_error=False：自己抛 UnauthorizedError，保证 401 响应体格式与全站一致
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/token",
    auto_error=False,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
OptionalToken = Annotated[str | None, Depends(oauth2_scheme)]


async def get_current_user(session: SessionDep, token: OptionalToken) -> User:
    """解析 Bearer Token 并加载用户。

    Raises:
        UnauthorizedError: 未带 token、token 无效/过期、用户不存在或被禁用。
    """
    if not token:
        raise UnauthorizedError("未登录或登录状态已失效")

    try:
        payload = decode_token(token, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("无效的访问凭证") from exc

    # 用户加载走仓储：本层不该手写 ORM 查询（和路由不手写 SQL 同一个道理）
    user = await UserRepository(session).get(payload.sub)
    if user is None:
        raise UnauthorizedError("用户不存在")
    if not user.is_active:
        raise PermissionDeniedError("账号已被停用，请联系管理员")
    return user


async def get_optional_user(session: SessionDep, token: OptionalToken) -> User | None:
    """可选登录态。

    用于「登录用户能看到自己的草稿、访客只能看已发布」这类接口。
    注意：token 无效时静默降级为访客，而不是报错——这里不应该因为一个坏 token
    就把公开页面挡在门外。
    """
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.InvalidTokenError:
        return None
    user = await UserRepository(session).get(payload.sub)
    return user if user and user.is_active else None


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def require_admin(user: CurrentUser) -> User:
    """仅站长。

    Raises:
        PermissionDeniedError: 当前用户不是 admin。
    """
    if user.role is not UserRole.ADMIN:
        raise PermissionDeniedError("该操作仅站长可用")
    return user


async def require_author(user: CurrentUser) -> User:
    """作者或站长（能发文章的人）。"""
    if user.role not in (UserRole.ADMIN, UserRole.AUTHOR):
        raise PermissionDeniedError("需要作者权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
AuthorUser = Annotated[User, Depends(require_author)]


def _clean_ip(value: str | None) -> str | None:
    """规范化候选 IP：去空白、丢弃明显非法的空串、限长防日志/内存被灌爆。"""
    if not value:
        return None
    return value.strip()[:64] or None


def client_ip(request: Request) -> str | None:
    """取客户端 IP。

    ``X-Forwarded-For`` **默认不信任**：这个头是客户端可以随意伪造的，
    盲信它等于把限流变成「改个头就能绕过」，更糟的是能借伪造 IP 把别人封掉，
    或者把伪造 IP 当成评论者地址存进数据库。
    只有当部署确实在可信反代（nginx）之后时，才通过 ``TRUST_PROXY_HEADERS=true`` 打开。

    **取值端是右不是左**（这里曾经取错过，是个真实的限流绕过漏洞）：

    - ``X-Real-IP`` 优先。nginx 配置写的是 ``proxy_set_header X-Real-IP $remote_addr``，
      它恒等于 nginx 直连的对端地址，客户端改不动。
    - ``X-Forwarded-For`` 退而取**最右一跳**。因为 nginx 用的是
      ``$proxy_add_x_forwarded_for``，它的语义是「在客户端传来的值后面**追加** $remote_addr」，
      所以最右才是最近一跳可信代理写进去的、真正的来源 IP；最左是客户端自己塞的。
      取最左等于「谁都能换个头拿一个全新的限流桶」，限流形同不存在，
      而且伪造 IP 会被当成评论者地址存库。
    """
    if settings.trust_proxy_headers:
        real_ip = _clean_ip(request.headers.get("X-Real-IP"))
        if real_ip:
            return real_ip

        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # 取最右一跳：那才是最近的代理追加的，左侧都是可伪造的
            return _clean_ip(forwarded.split(",")[-1])
    return request.client.host if request.client else None


def client_key(request: Request) -> str:
    """限流用的客户端标识。"""
    return client_ip(request) or "unknown"


def rate_limit(name: str, *, limit: int, window_seconds: int) -> Callable[[Request], None]:
    """生成一个限流依赖。

    Args:
        name: 规则名，参与 key 构造，避免不同接口的计数互相影响。
        limit: 窗口内允许次数。
        window_seconds: 窗口长度（秒）。

    Returns:
        可直接放进 ``Depends(...)`` 的依赖函数。

    Raises:
        RateLimitedError: 超出配额时（带 Retry-After）。
    """

    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        allowed, retry_after = limiter.hit(
            f"{name}:{client_key(request)}",
            limit=limit,
            window_seconds=window_seconds,
        )
        if not allowed:
            raise RateLimitedError(
                detail="操作过于频繁，请稍后再试",
                retry_after=retry_after,
            )

    return dependency


# 各入口的配额。数值按「正常用户不可能触发的频率」定，而不是按「够用就行」：
# 登录 5 次/分 覆盖手工输错密码，脚本撞库则会被立刻挡住。
LOGIN_RATE_LIMIT = Depends(rate_limit("login", limit=5, window_seconds=60))
COMMENT_RATE_LIMIT = Depends(rate_limit("comment", limit=5, window_seconds=60))
LIKE_RATE_LIMIT = Depends(rate_limit("like", limit=20, window_seconds=60))
