"""认证与用户服务。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt

from app.config import settings
from app.models import User, UserRole
from app.repositories import RefreshSessionRepository, UserRepository
from app.schemas.user import Token, UserCreate, UserSelfUpdate, UserUpdate
from app.utils.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnauthorizedError,
)
from app.utils.security import (
    TokenPayload,
    access_token_ttl_seconds,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_password_async,
    verify_password_async,
)

logger = logging.getLogger("blog")

# 多标签页宽容窗口（秒）。
#
# 一枚 refresh token 被轮换后，极短时间内又被使用，更可能来自
# "两个标签页同时刷新"（前端的单飞锁只在单页内生效）而不是盗用。
# 窗口内放行，窗口外才判定为复用并吊销整族。
#
# 取 30 秒的依据：跨标签页的两次刷新通常相差毫秒级到一两秒（都发生在
# access token 过期的那一刻）；而真正的盗用者往往在被发现前有更长的间隔。
# 窗口越大越宽容（对盗用越迟钝），30 秒是"能用"与"能抓"之间的折中。
REFRESH_REUSE_GRACE_SECONDS = 30


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
        self.sessions = RefreshSessionRepository(session)

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
            # 等时校验也走线程池：它的目的就是"耗时与真校验接近"，
            # 若在事件循环里跑，这一条恰恰会把循环按住 180ms。
            await verify_password_async(password, _dummy_hash())
            raise UnauthorizedError("用户名或密码错误")

        if not await verify_password_async(password, user.hashed_password):
            raise UnauthorizedError("用户名或密码错误")

        if not user.is_active:
            raise PermissionDeniedError("账号已被停用，请联系站长")

        return user

    async def issue_tokens(self, user: User) -> Token:
        """签发 access + refresh 双 token，并登记这条刷新会话。

        refresh token 从"无状态"变成"有状态"是刻意的：只有落库的 jti 才能支持
        轮换、复用检测与单会话吊销（见 ``models/refresh_session.py``）。
        每次签发多一次 INSERT，对登录频率来说可忽略。
        """
        access_token, refresh_token, payload = self._mint(user)
        await self.sessions.open_session(user_id=user.id, jti=payload.jti, expires_at=payload.exp)
        await self.session.commit()
        return Token(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_token_ttl_seconds(),
        )

    @staticmethod
    def _mint(user: User) -> tuple[str, str, TokenPayload]:
        """生成一对令牌，并解出 refresh 的载荷（要拿 jti 与过期时间落库）。"""
        access_token = create_access_token(
            user.id, user.role.value, token_version=user.token_version
        )
        refresh_token = create_refresh_token(
            user.id, user.role.value, token_version=user.token_version
        )
        return access_token, refresh_token, decode_token(refresh_token, expected_type="refresh")

    async def refresh(self, refresh_token: str) -> Token:
        """用 refresh token 换新的双 token。

        **轮换**：旧的会被标记为已使用，之后再用即视为盗用 —— 唯一的例外是
        `REFRESH_REUSE_GRACE_SECONDS` 的宽容窗口（容忍多标签页同时刷新）。
        """
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("刷新令牌已过期，请重新登录") from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("无效的刷新令牌") from exc

        user = await self.users.get(payload.sub)
        if user is None or not user.is_active:
            raise UnauthorizedError("用户不存在或已被停用")
        # 代次不符 = 这个 refresh token 已被吊销（用户改过密码 / 登过出），
        # 不能拿它换新的 access token —— 否则「改密码即全端下线」形同虚设。
        if payload.token_version != user.token_version:
            raise UnauthorizedError("登录状态已失效，请重新登录")

        record = await self.sessions.get_by_jti(payload.jti)
        if record is None or record.revoked_at is not None:
            # 不认识这枚令牌（本次升级之前签发、或已被清理），或它已被吊销
            raise UnauthorizedError("登录状态已失效，请重新登录")
        if record.expires_at <= datetime.now(UTC):
            raise UnauthorizedError("刷新令牌已过期，请重新登录")

        if record.rotated_at is not None:
            # 这枚已经用过了。可能是盗用，也可能是**两个标签页同时刷新**
            # （前端的单飞锁只在单个页面内生效，跨标签页无效）。
            # 用时间窗区分：窗口内当作"再来一次"放行，窗口外判定为复用。
            age = (datetime.now(UTC) - record.rotated_at).total_seconds()
            if age > REFRESH_REUSE_GRACE_SECONDS:
                revoked = await self.sessions.revoke_user_sessions(user.id)
                await self.session.commit()
                logger.warning(
                    "已轮换的 refresh token 被重复使用，按盗用处理：吊销该用户全部会话",
                    extra={
                        "extra_fields": {
                            "user_id": user.id,
                            "age_seconds": round(age, 1),
                            "revoked_sessions": revoked,
                        }
                    },
                )
                raise UnauthorizedError("登录状态已失效，请重新登录")

        access_token, new_refresh, new_payload = self._mint(user)
        await self.sessions.open_session(
            user_id=user.id, jti=new_payload.jti, expires_at=new_payload.exp
        )
        await self.sessions.mark_rotated(record, replaced_by_jti=new_payload.jti)
        await self.session.commit()
        return Token(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_in=access_token_ttl_seconds(),
        )

    async def change_password(self, user: User, old_password: str, new_password: str) -> None:
        if not await verify_password_async(old_password, user.hashed_password):
            raise BadRequestError("原密码不正确")
        user.hashed_password = await hash_password_async(new_password)
        # 改密码 = 全端下线：代次 +1 让所有已签发的 access token 立刻失效，
        # 同时吊销全部刷新会话 —— 只改哈希的话，攻击者手上那个还没过期的
        # refresh token 照样能换出新令牌（它能自我续期，这是最危险的一点）。
        user.token_version += 1
        await self.sessions.revoke_user_sessions(user.id)
        await self.session.flush()

    async def logout(self, user: User) -> None:
        """登出：让该用户已签发的令牌全部失效。

        两件事一起做，缺一不可：

        1. **吊销全部刷新会话**（按行打 ``revoked_at``）—— 否则手上的
           refresh token 照样能换出新的 access token（它是能自我续期的）；
        2. ``token_version += 1`` —— access token 是无状态的、不落库，
           只有代次能立刻废掉它们（有效期 120 分钟）。

        **代价**：这是"全端下线"（手机上登出，电脑上也得重新登录）。
        本轮引入 ``refresh_sessions`` 之后，**单会话吊销**在存储层已经具备条件
        （``revoked_at`` 是按行记的），缺的只是接口语义 —— 而登出按钮表达的就是
        "我不再信任这些凭证"，全端失效正是它能真正止损的方式。
        什么都不做（只回一句「请在客户端清除凭证」）是最差的选项：
        用户以为已登出，凭证在有效期内仍能访问所有受保护资源。
        """
        user.token_version += 1
        await self.sessions.revoke_user_sessions(user.id)
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
            hashed_password=await hash_password_async(payload.password),
            nickname=payload.nickname or payload.username,
            avatar_url=payload.avatar_url,
            bio=payload.bio,
            role=payload.role,
        )

    async def prune_expired_sessions(self) -> int:
        """删掉早就过期的刷新会话行（启动时调用）。

        与访问日志同理：这张表每次登录/刷新都写一行，只增不减的话
        备份体积与迁移耗时都会慢慢变难看，而收益是零。
        留一点余量（过期后再留 7 天）是为了排障时还能看到"谁在什么时候登过"。
        """
        cutoff = datetime.now(UTC) - timedelta(days=settings.refresh_token_expire_days)
        return await self.sessions.prune_expired(before=cutoff)

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
