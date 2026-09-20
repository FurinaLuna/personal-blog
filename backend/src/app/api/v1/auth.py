"""认证与用户管理路由。

路由层只做三件事：解析入参、调用 service、返回 schema。
任何 ``if role == ...`` 的业务判断都不应该出现在这里——那是 service 的职责。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, status

from app.api.deps import LOGIN_RATE_LIMIT, AdminUser, CurrentUser, SessionDep
from app.schemas.common import Message
from app.schemas.user import (
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    Token,
    UserCreate,
    UserRead,
    UserSelfUpdate,
    UserUpdate,
)
from app.services import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=Token, summary="登录（JSON）")
async def login(payload: LoginRequest, session: SessionDep, _: None = LOGIN_RATE_LIMIT) -> Token:
    """用用户名或邮箱 + 密码换取双 token。"""
    service = AuthService(session)
    user = await service.authenticate(payload.username, payload.password)
    return service.issue_tokens(user)


@router.post("/token", response_model=Token, summary="登录（OAuth2 表单，供 Swagger 调试）")
async def login_form(
    session: SessionDep,
    username: Annotated[str, Form(description="用户名或邮箱")],
    password: Annotated[str, Form()],
    _: None = LOGIN_RATE_LIMIT,
) -> Token:
    """OAuth2 password flow 的兼容入口。

    存在意义：Swagger UI 右上角的 Authorize 按钮只会发
    ``application/x-www-form-urlencoded``，而正式前端用的是上面的 JSON 接口。
    两个入口共用同一套 service，不存在逻辑分叉。
    """
    service = AuthService(session)
    user = await service.authenticate(username, password)
    return service.issue_tokens(user)


@router.post("/refresh", response_model=Token, summary="刷新令牌")
async def refresh(payload: RefreshRequest, session: SessionDep) -> Token:
    return await AuthService(session).refresh(payload.refresh_token)


@router.post("/logout", response_model=Message, summary="登出")
async def logout(user: CurrentUser, session: SessionDep) -> Message:
    """登出：服务端真的吊销令牌，而不是只给前端一句语义消息。

    实现走 ``AuthService.logout`` 递增 ``token_version``（与改密码同一套机制），
    该用户所有设备上已签发的 access / refresh token 立即失效。
    前端收到 200 后仍要清掉本地凭证——那是客户端侧的事，两者不冲突。
    """
    await AuthService(session).logout(user)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    return Message(detail="已登出，所有设备的登录状态已失效")


@router.get("/me", response_model=UserRead, summary="当前登录用户")
async def read_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("/me", response_model=UserRead, summary="修改自己的资料")
async def update_me(payload: UserSelfUpdate, user: CurrentUser, session: SessionDep) -> UserRead:
    """注意这里用的是 ``UserSelfUpdate``：不含 role / is_active，
    从 schema 层面就杜绝了普通用户给自己提权的可能。"""
    updated = await AuthService(session).update_self(user, payload)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    return UserRead.model_validate(updated)


@router.post("/me/password", response_model=Message, summary="修改自己的密码")
async def change_password(
    payload: PasswordChange, user: CurrentUser, session: SessionDep
) -> Message:
    await AuthService(session).change_password(user, payload.old_password, payload.new_password)
    await session.commit()
    return Message(detail="密码已更新，请重新登录")


# ------------------------------------------------------------------ 站长专属


@router.get("/users", response_model=list[UserRead], summary="用户列表（站长）")
async def list_users(_: AdminUser, session: SessionDep) -> list[UserRead]:
    users = await AuthService(session).list_users()
    return [UserRead.model_validate(item) for item in users]


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增用户（站长）",
)
async def create_user(payload: UserCreate, _: AdminUser, session: SessionDep) -> UserRead:
    """站点不开放自助注册——个人博客的账号应当由站长分配。"""
    created = await AuthService(session).create_user(payload)
    await session.commit()
    return UserRead.model_validate(created)


@router.patch("/users/{user_id}", response_model=UserRead, summary="修改用户（站长）")
async def update_user(
    user_id: int, payload: UserUpdate, _: AdminUser, session: SessionDep
) -> UserRead:
    updated = await AuthService(session).update_user(user_id, payload)
    await session.commit()
    return UserRead.model_validate(updated)


@router.delete(
    "/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除用户（站长）"
)
async def delete_user(user_id: int, operator: AdminUser, session: SessionDep) -> None:
    await AuthService(session).delete_user(user_id, operator=operator)
    await session.commit()
