"""评论模型（支持两级回复）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class Comment(Base, TimestampMixin):
    """访客可以匿名评论，登录用户则带上 user_id。

    默认 ``is_approved=False``：先审后发，避免博客变成垃圾场。
    站长也可在站点配置里打开「免审核」。
    """

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 只做两级：回复的回复仍然挂在根评论上，避免无限嵌套拖垮前端渲染
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), index=True
    )

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(String(50), nullable=False)
    author_email: Mapped[str | None] = mapped_column(String(255))
    author_site: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)

    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_admin_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    article: Mapped[Article] = relationship(back_populates="comments")
    user: Mapped[User | None] = relationship(lazy="joined")
    parent: Mapped[Comment | None] = relationship(remote_side="Comment.id", lazy="joined")
    replies: Mapped[list[Comment]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Comment id={self.id} article={self.article_id} approved={self.is_approved}>"
