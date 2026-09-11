"""用户模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
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
