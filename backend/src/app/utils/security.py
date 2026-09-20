"""密码哈希与 JWT 签发/校验。

安全约定：
- 密码用 bcrypt（自带盐、抗暴力破解），**从不**在日志里输出明文或哈希。
- bcrypt 的输入上限是 72 字节（注意是字节不是字符），超过会直接抛 ValueError，
  因此这里显式校验字节长度，而不是静默截断——截断会让"前 72 字节相同"的两个
  不同密码互相能登录。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.config import settings

BCRYPT_MAX_BYTES = 72
TokenType = Literal["access", "refresh"]


class PasswordTooLongError(ValueError):
    """密码字节长度超过 bcrypt 上限。"""


def password_byte_length(password: str) -> int:
    """返回密码的 UTF-8 字节长度（BCRYPT 的真实限制口径）。"""
    return len(password.encode("utf-8"))


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希。

    Args:
        password: 明文密码。

    Returns:
        形如 ``$2b$12$...`` 的哈希串。

    Raises:
        PasswordTooLongError: 密码 UTF-8 字节数超过 72。
    """
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise PasswordTooLongError(
            f"密码 UTF-8 字节数不得超过 {BCRYPT_MAX_BYTES}（当前 {len(raw)}）"
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码。哈希串损坏或格式不符时返回 False 而不是抛异常（避免 500）。"""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """解析后的 JWT 载荷。"""

    sub: int
    role: str
    type: TokenType
    exp: datetime
    jti: str
    # 令牌代次：签发时是用户的 token_version 快照。
    # 与库里的当前值不一致 = 这个令牌已被吊销（例如用户改过密码）。
    token_version: int


def _create_token(
    subject: int,
    role: str,
    token_type: TokenType,
    expires_delta: timedelta,
    *,
    token_version: int = 0,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),  # JWT 规范要求 sub 是字符串
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": uuid.uuid4().hex,  # 便于将来做黑名单/单点登出
        # 令牌代次。校验方拿它与库里的 users.token_version 比对，
        # 不一致即吊销 —— 改密码 / 登出时服务端 +1，所有旧令牌立刻失效。
        "ver": token_version,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, role: str, *, token_version: int = 0) -> str:
    return _create_token(
        user_id,
        role,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
        token_version=token_version,
    )


def create_refresh_token(user_id: int, role: str, *, token_version: int = 0) -> str:
    return _create_token(
        user_id,
        role,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
        token_version=token_version,
    )


def decode_token(token: str, *, expected_type: TokenType | None = None) -> TokenPayload:
    """解码并校验 JWT。

    Args:
        token: 客户端传来的 token。
        expected_type: 期望的 token 类型；传入后类型不符会抛 ``InvalidTokenError``，
            防止用 refresh token 直接访问业务接口。

    Raises:
        jwt.InvalidTokenError: 签名不对、过期、结构非法或类型不符。
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub", "type"]},
    )
    token_type = payload.get("type")
    if expected_type is not None and token_type != expected_type:
        raise jwt.InvalidTokenError(f"token 类型不符：期望 {expected_type}，实际 {token_type}")
    return TokenPayload(
        sub=int(payload["sub"]),
        role=payload.get("role", ""),
        type=token_type,
        exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
        jti=payload.get("jti", ""),
        # 缺失时按 0 处理，**这是升级兼容的关键**：本次改动之前签发的 token
        # 里没有 ver 字段，而所有存量用户的 token_version 默认就是 0，
        # 于是老令牌继续有效 —— 升级不会把所有人踢下线。
        token_version=int(payload.get("ver", 0)),
    )


def access_token_ttl_seconds() -> int:
    """access token 有效期（秒），用于响应体里的 ``expires_in``。"""
    return settings.access_token_expire_minutes * 60


# ---------------------------------------------------------------- 退订 token
#
# 独立于上面的登录 token 体系（TokenType / TokenPayload 的 ``sub`` 是 int 强转，
# 塞 email 进去会在解析时炸）：退订凭证的 ``sub`` 是小写邮箱、type 固定
# "unsubscribe"。有效期 10 年——退订链接出现在邮件里，跟着邮件活很久。

UNSUBSCRIBE_TOKEN_TYPE = "unsubscribe"
UNSUBSCRIBE_TOKEN_TTL = timedelta(days=3650)


def create_unsubscribe_token(email: str) -> str:
    """签发退订 token（``sub`` 为小写邮箱）。"""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": email.lower(),
        "type": UNSUBSCRIBE_TOKEN_TYPE,
        "iat": now,
        "exp": now + UNSUBSCRIBE_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_unsubscribe_token(token: str) -> str:
    """验签并返回 token 中的小写邮箱。

    Raises:
        jwt.InvalidTokenError: 签名不对、过期或类型不符。
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub", "type"]},
    )
    if payload.get("type") != UNSUBSCRIBE_TOKEN_TYPE:
        raise jwt.InvalidTokenError(f"token 类型不符：期望 {UNSUBSCRIBE_TOKEN_TYPE}")
    return str(payload["sub"]).lower()
