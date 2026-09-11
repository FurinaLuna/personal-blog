"""FastAPI 依赖注入：数据库会话、当前用户、角色门禁。

设计要点：
- ``get_current_user`` 负责认证（你是谁）；
- ``require_admin`` / ``require_author`` 负责授权（你能干什么），
  两者分开，路由上按需组合，避免在每个 handler 里手写 if 判断角色。
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models import User, UserRole
from app.utils.exceptions import PermissionDeniedError, UnauthorizedError
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

    user = await session.get(User, payload.sub)
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
    user = await session.get(User, payload.sub)
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
