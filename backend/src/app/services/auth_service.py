"""认证与用户服务。"""

from __future__ import annotations

from functools import lru_cache

import jwt

from app.models import User, UserRole
from app.repositories import UserRepository
from app.schemas.user import Token, UserCreate, UserSelfUpdate, UserUpdate
from app.utils.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnauthorizedError,
)
from app.utils.security import (
    access_token_ttl_seconds,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """占位哈希。

    用户不存在时也走一次 bcrypt 校验，让「账号不存在」与「密码错误」的响应耗时
    接近，避免通过响应时间枚举出站点存在哪些用户名。进程内只算一次。
    """
    return hash_password("timing-attack-placeholder-please-ignore")


class AuthService:
    def __init__(self, session) -> None:
        self.session = session
        self.users = UserRepository(session)

    # ---------------------------------------------------------------- 登录

    async def authenticate(self, login: str, password: str) -> User:
        """校验账号密码。

        Args:
            login: 用户名或邮箱。
            password: 明文密码。

        Raises:
            UnauthorizedError: 账号不存在或密码错误（对外统一为同一条文案，
                不区分「用户不存在」和「密码错误」，避免账号枚举）。
            PermissionDeniedError: 账号被停用。
        """
        user = await self.users.get_by_login(login)
        if user is None:
            verify_password(password, _dummy_hash())
            raise UnauthorizedError("用户名或密码错误")

        if not verify_password(password, user.hashed_password):
            raise UnauthorizedError("用户名或密码错误")

        if not user.is_active:
            raise PermissionDeniedError("账号已被停用，请联系站长")

        return user

    def issue_tokens(self, user: User) -> Token:
        """签发 access + refresh 双 token（带上当前的令牌代次）。"""
        return Token(
            access_token=create_access_token(
                user.id, user.role.value, token_version=user.token_version
            ),
            refresh_token=create_refresh_token(
                user.id, user.role.value, token_version=user.token_version
            ),
            expires_in=access_token_ttl_seconds(),
        )

    async def refresh(self, refresh_token: str) -> Token:
        """用 refresh token 换新的双 token（滚动刷新，前端无需感知）。"""
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("刷新令牌已过期，请重新登录") from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("无效的刷新令牌") from exc

        user = await self.users.get(payload.sub)
        if user is None or not user.is_active:
            raise UnauthorizedError("用户不存在或已被停用")
        # 代次不符 = 这个 refresh token 已被吊销（用户改过密码），
        # 不能拿它换新的 access token —— 否则「改密码即全端下线」形同虚设。
        if payload.token_version != user.token_version:
            raise UnauthorizedError("登录状态已失效，请重新登录")
        return self.issue_tokens(user)

    async def change_password(self, user: User, old_password: str, new_password: str) -> None:
        if not verify_password(old_password, user.hashed_password):
            raise BadRequestError("原密码不正确")
        user.hashed_password = hash_password(new_password)
        # 改密码 = 全端下线：代次 +1 让所有已签发的 access / refresh token 立刻失效。
        # 这是「密码可能泄露」时唯一的应急手段 —— 只改哈希的话，
        # 攻击者手上那个还没过期的 refresh token 照样能换出新令牌。
        user.token_version += 1
        await self.session.flush()

    async def logout(self, user: User) -> None:
        """登出：让该用户已签发的令牌全部失效。

        实现就是复用了改密码那套令牌代次机制——``token_version += 1`` 之后，
        所有旧 token 的 ``ver`` 与库里的值不匹配，认证依赖与 ``refresh()``
        都会拒掉它们。

        **代价**：这会让该用户在**所有设备**上的令牌同时失效（手机上登出，
        电脑上也得重新登录）。要做「只吊销当前设备」就得给每个会话记 jti
        并引入黑名单存储，个人博客的规模不值得——而登出的语义本来就是
        「我不再信任这些凭证」，全端失效正是它唯一能真正止损的手段。
        什么都不做（只回一句「请在客户端清除凭证」）是最差的选项：
        用户以为已登出，凭证在有效期内（access 120 分钟、refresh 7 天）
        仍能访问所有受保护资源。
        """
        user.token_version += 1
        await self.session.flush()

    # ---------------------------------------------------------------- 用户

    async def update_self(self, user: User, payload: UserSelfUpdate) -> User:
        """用户改自己的资料。role / is_active 不在此接口范围，避免越权提权。"""
        data = payload.model_dump(exclude_unset=True)

        new_email = data.get("email")
        if new_email and await self.users.email_taken(new_email, exclude_id=user.id):
            raise ConflictError("该邮箱已被占用")
        for key, value in data.items():
            if value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user

    async def create_user(self, payload: UserCreate) -> User:
        """站长新增用户。站点不开放自助注册，账号由站长分配。"""
        if await self.users.username_taken(payload.username):
            raise ConflictError(f"用户名 {payload.username} 已存在")
        if await self.users.email_taken(str(payload.email)):
            raise ConflictError(f"邮箱 {payload.email} 已被使用")

        return await self.users.create(
            username=payload.username,
            email=str(payload.email),
            hashed_password=hash_password(payload.password),
            nickname=payload.nickname or payload.username,
            avatar_url=payload.avatar_url,
            bio=payload.bio,
            role=payload.role,
        )

    async def list_users(self) -> list[User]:
        return await self.users.list_all()

    async def update_user(self, user_id: int, payload: UserUpdate) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("用户不存在")

        data = payload.model_dump(exclude_unset=True)

        new_email = data.get("email")
        if new_email and await self.users.email_taken(str(new_email), exclude_id=user_id):
            raise ConflictError("该邮箱已被占用")

        # 保护性检查：不允许把最后一个可用管理员降级或停用，否则站点会把自己锁在门外
        will_lose_admin = data.get("role") is not None and data["role"] is not UserRole.ADMIN
        will_be_disabled = data.get("is_active") is False
        if (will_lose_admin or will_be_disabled) and user.role is UserRole.ADMIN:
            remaining = await self.users.count_active_admins(exclude_id=user_id)
            if remaining == 0:
                raise BadRequestError("不能取消最后一个可用管理员，否则将无人能管理站点")

        for key, value in data.items():
            if value is not None:
                setattr(user, key, value)
        await self.session.flush()
        return user

    async def delete_user(self, user_id: int, *, operator: User) -> None:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        if user.id == operator.id:
            raise BadRequestError("不能删除当前登录的自己")
        if user.role is UserRole.ADMIN:
            remaining = await self.users.count_active_admins(exclude_id=user_id)
            if remaining == 0:
                raise BadRequestError("不能删除最后一个可用管理员")

        await self.users.delete(user)
