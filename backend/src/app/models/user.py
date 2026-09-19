"""用户模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import UserRole, enum_values

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.attachment import Attachment


class User(Base, TimestampMixin):
    """站点用户。访客不落库，因此只有 admin / author 两种角色。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole, native_enum=False, length=20, values_callable=enum_values),
        default=UserRole.AUTHOR,
        nullable=False,
    )
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    bio: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 令牌代次（token versioning）。
    #
    # 签发时把这个数字写进 JWT，校验时与库里的当前值比对，不一致即视为失效。
    # **改密码时 +1，于是所有已签发的 access / refresh token 立刻作废。**
    #
    # 为什么需要它：refresh token 是不可吊销的（签名有效 + 用户存在就认），
    # 默认 7 天有效期内泄露了就无法止损；而「改密码」这个应急动作原本
    # 只是改了个哈希，对已泄露的 token 毫无影响 —— 那等于没有应急手段。
    #
    # 用「代次」而不是「黑名单表」：一个整数就能表达「所有旧令牌作废」，
    # 不需要为每个 jti 落库、也没有清理过期条目的问题。
    # server_default 必须与迁移里写的保持一致：迁移给存量行补了 0，
    # 模型这边不声明的话，alembic autogenerate 会认为「模型没有默认值、
    # 库里却有」，于是每次 make migration 都生成一条删默认值的噪音迁移。
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # 反向集合默认惰性加载：只在删除用户触发级联时才由 SQLAlchemy 自行加载，
    # 业务代码里不要直接访问（异步下会抛 MissingGreenlet），要用就显式 selectinload。
    articles: Mapped[list[Article]] = relationship(
        back_populates="author", cascade="all, delete-orphan", passive_deletes=True
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="uploader", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return f"<User id={self.id} username={self.username!r} role={self.role.value}>"
