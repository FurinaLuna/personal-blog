"""用户与认证相关 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.utils.security import BCRYPT_MAX_BYTES, password_byte_length


def _ensure_bcrypt_compatible(password: str) -> str:
    """bcrypt 只接受 ≤72 **字节** 的输入，超限就报错而不是静默截断。"""
    if password_byte_length(password) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"密码过长：UTF-8 编码后不得超过 {BCRYPT_MAX_BYTES} 字节"
            f"（当前 {password_byte_length(password)} 字节，1 个汉字算 3 字节）"
        )
    return password


class UserBase(BaseModel):
    username: str = Field(min_length=3, max_length=50, description="登录名")
    nickname: str | None = Field(default=None, max_length=50)
    avatar_url: str | None = Field(default=None, max_length=500)
    bio: str | None = None


class UserCreate(UserBase):
    email: EmailStr
    password: str = Field(
        min_length=8, max_length=72, description="明文密码，服务端 bcrypt 哈希后存储"
    )
    role: UserRole = UserRole.AUTHOR

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _ensure_bcrypt_compatible(value)


class UserUpdate(BaseModel):
    """管理员改用户资料（不含密码）。"""

    nickname: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    avatar_url: str | None = None
    bio: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserSelfUpdate(BaseModel):
    """用户改自己的资料，不允许改 role / is_active。"""

    nickname: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    avatar_url: str | None = None
    bio: str | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime


class UserBrief(BaseModel):
    """嵌在文章里的作者信息，只暴露公开字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str | None = None
    avatar_url: str | None = None


class LoginRequest(BaseModel):
    """支持用用户名或邮箱登录。"""

    username: str = Field(description="用户名或邮箱")
    password: str


class Token(BaseModel):
    access_token: str
    # 默认 **不返回**：浏览器走 httpOnly Cookie（见 config.py 的
    # refresh_token_cookie_*），响应体里带着它等于给 XSS 留了一扇窗。
    # 只有在 REFRESH_TOKEN_IN_BODY=true 时（非浏览器客户端）才会出现。
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int = Field(description="access_token 剩余有效期（秒）")


class RefreshRequest(BaseModel):
    """刷新请求。

    浏览器**不传**这个字段——refresh token 在 httpOnly Cookie 里，服务端自己取。
    留着它是为了非浏览器客户端（脚本 / CI / curl）：它们拿不到 Cookie Jar，
    显式传这一字段时服务端按它处理（便于继续跑那些直接打 HTTP 的回归脚本）。

    优先级：显式传的 body > Cookie。这样"测刷新接口拿错令牌该被拒"这类
    反向用例仍然成立（不会被浏览器 Cookie 悄悄救回）。
    """

    refresh_token: str | None = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def _not_same(cls, value: str, info) -> str:
        if info.data.get("old_password") == value:
            raise ValueError("新密码不能与旧密码相同")
        return _ensure_bcrypt_compatible(value)
