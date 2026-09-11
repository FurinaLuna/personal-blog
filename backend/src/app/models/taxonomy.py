"""分类与标签模型。两者结构高度相似，但语义与使用方式不同，故不强行抽象成一个表。

- Category：一对多，一篇文章最多一个分类，是「骨架」，用于导航。
- Tag：多对多，一篇文章可以有任意多个标签，是「网络」，用于关联发现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.associations import article_tags

if TYPE_CHECKING:
    from app.models.article import Article


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    articles: Mapped[list[Article]] = relationship(back_populates="category", passive_deletes=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Category id={self.id} slug={self.slug!r}>"


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)

    articles: Mapped[list[Article]] = relationship(
        secondary=article_tags, back_populates="tags", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tag id={self.id} slug={self.slug!r}>"
