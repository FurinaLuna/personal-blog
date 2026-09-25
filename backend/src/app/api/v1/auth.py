"""认证与用户管理路由。

路由层只做三件事：解析入参、调用 service、返回 schema。
任何 ``if role == ...`` 的业务判断都不应该出现在这里——那是 service 的职责。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status

from app.api.cookies import (
    assert_same_origin,
    clear_refresh_cookie,
    read_refresh_token,
    set_refresh_cookie,
)
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
async def login(
    payload: LoginRequest, session: SessionDep, response: Response, _: None = LOGIN_RATE_LIMIT
) -> Token:
    """用用户名或邮箱 + 密码换取 access token（refresh 走 httpOnly Cookie）。"""
    service = AuthService(session)
    user = await service.authenticate(payload.username, payload.password)
    return set_refresh_cookie(response, await service.issue_tokens(user))


@router.post("/token", response_model=Token, summary="登录（OAuth2 表单，供 Swagger 调试）")
async def login_form(
    session: SessionDep,
    response: Response,
    username: Annotated[str, Form(description="用户名或邮箱")],
    password: Annotated[str, Form()],
    _: None = LOGIN_RATE_LIMIT,
) -> Token:
    """OAuth2 password flow 的兼容入口。

    存在意义：Swagger UI 右上角的 Authorize 按钮只会发
    ``application/x-www-form-urlencoded``，而正式前端用的是上面的 JSON 接口。
    两个入口共用同一套 service，不存在逻辑分叉。

    注意：Swagger 是浏览器里的页面，它同样拿不到 httpOnly Cookie 里的
    refresh token。要用它调试受保护接口，把响应里的 ``access_token``
    粘到 Authorize 框即可（access token 本来就是给前端内存用的）。
    """
    service = AuthService(session)
    user = await service.authenticate(username, password)
    return set_refresh_cookie(response, await service.issue_tokens(user))


@router.post("/refresh", response_model=Token, summary="刷新令牌")
async def refresh(
    session: SessionDep,
    request: Request,
    response: Response,
    # body 必须可省略：浏览器手里没有 refresh token（它在 httpOnly Cookie 里），
    # 让它为了满足框架而发一个空 ``{}`` 是纯粹的负担。而且 FastAPI 对
    # Pydantic model 类 body 一律按必填处理（哪怕模型字段全可选），
    # 漏发就返回 422——看起来像"参数错了"、实际是"你压根没发"，
    # 这种错误毫无提示性。可省略才准确表达「省略就从 Cookie 取」。
    #
    # 放在参数列表末尾是 Python 语法所迫：无默认值的参数不能排在
    # 有默认值的参数后面，而 SessionDep / Request / Response 都没有默认值。
    payload: RefreshRequest | None = None,
) -> Token:
    """用 refresh token 换一枚新的 access token。

    浏览器不传请求体——token 在 httpOnly Cookie 里，由服务端自己取。
    因为 Cookie 会被浏览器**自动附带**，这里额外做同源校验（CSRF 防线）。
    """
    assert_same_origin(request)
    token = read_refresh_token(request, payload.refresh_token if payload else None)
    return set_refresh_cookie(response, await AuthService(session).refresh(token))


@router.post("/logout", response_model=Message, summary="登出")
async def logout(user: CurrentUser, session: SessionDep, response: Response) -> Message:
    """登出：服务端真的吊销令牌，并把 refresh Cookie 删掉。

    实现走 ``AuthService.logout`` 递增 ``token_version``（与改密码同一套机制），
    该用户所有设备上已签发的 access / refresh token 立即失效。

    删 Cookie **不是**可选项：服务端吊销只保证"旧凭证换不出新凭证"，
    而 Cookie 还在浏览器里的话，每次刷新请求仍然会把一枚作废的 token 送上来。
    前端收到 200 后清掉内存里的 access token —— 那是客户端侧的事，两者不冲突。
    """
    await AuthService(session).logout(user)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    clear_refresh_cookie(response)
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
