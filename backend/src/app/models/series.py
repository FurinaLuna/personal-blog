"""系列模型（技术连载 / 合集）。

语义介于 Category 与 Tag 之间：一对多（一篇文章最多属于一个系列），
但系列内的文章有**顺序**（``series_order``），用于详情页的系列内上下篇导航——
这是 ROADMAP 1.2 的核心动机：默认的 prev/next 只按发布时间排，
连载文章读起来是断的（上篇跳到无关文章）。

删除系列时文章 ``SET NULL``（变回普通文章），与分类同策略：
内容永远比结构重要。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.article import Article


class Series(Base, TimestampMixin):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    articles: Mapped[list[Article]] = relationship(back_populates="series", passive_deletes=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Series id={self.id} slug={self.slug!r}>"
