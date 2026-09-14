"""评论邮件通知退订模型。

一行 = 一个「再也不想收回复通知」的邮箱。只有**邮箱维度**的退订
（没有「按文章退订」）：被通知的唯一场景是「自己的评论被回复」，
粒度再细就是伪需求。重新评论即恢复订阅——退订不改评论历史，
新评论时发信前照常查这张表，查不到就发。
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class NotificationOptOut(Base, TimestampMixin):
    __tablename__ = "notification_opt_outs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 归一小写后唯一；插入方负责归一（token 解码时已做）
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NotificationOptOut id={self.id} email={self.email!r}>"
