"""文章模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.associations import article_tags
from app.models.enums import ArticleStatus, enum_values

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.series import Series
    from app.models.taxonomy import Category, Tag
    from app.models.user import User


class Article(Base, TimestampMixin):
    """博客文章。

    正文以 Markdown 原文存储（``content_md``），渲染交给前端（marked + DOMPurify），
    这样后端保持纯粹的「内容源」角色，将来换渲染器不用动数据库。
    """

    __tablename__ = "articles"
    __table_args__ = (
        # 列表页最常见的组合条件：已发布 + 按发布时间倒序
        Index("ix_articles_status_published_at", "status", "published_at"),
        Index("ix_articles_is_top_published_at", "is_top", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    content_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cover_image: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[ArticleStatus] = mapped_column(
        SQLEnum(ArticleStatus, native_enum=False, length=20, values_callable=enum_values),
        default=ArticleStatus.DRAFT,
        nullable=False,
        index=True,
    )
    is_top: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_comment: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reading_time: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="预估阅读时长（分钟）"
    )
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    # 系列（可选，一对多）：系列内顺序由 series_order 决定，
    # 详情页系列导航与 prev/next 优先用它
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="SET NULL"), index=True
    )
    series_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    author: Mapped[User] = relationship(back_populates="articles", lazy="joined")
    category: Mapped[Category | None] = relationship(back_populates="articles", lazy="joined")
    series: Mapped[Series | None] = relationship(back_populates="articles", lazy="joined")
    tags: Mapped[list[Tag]] = relationship(
        secondary=article_tags, back_populates="articles", lazy="selectin"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="article", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_public(self) -> bool:
        """访客是否可见（草稿只对作者/管理员可见）。"""
        return self.status is not ArticleStatus.DRAFT

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Article id={self.id} slug={self.slug!r} status={self.status.value}>"
