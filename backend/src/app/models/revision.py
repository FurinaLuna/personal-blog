"""文章版本历史模型。

## 为什么值得单独建一张表

这是整个项目里**唯一一个真正不可替代**的功能：其它问题都可以用别的方式
凑合，只有「误删了一段、改完发现还是上一版好」是没法补救的。
存储成本极低（Markdown 纯文本），而收益是「敢改」——
没有版本历史的时候，人对已发布文章是能不碰就不碰的。

## 设计取舍

- **只在正文/标题/摘要真正变化时快照**，不是每次 PATCH 都存。
  改个「是否置顶」也存一版的话，版本列表会被噪音淹没，真出事时反而找不到。
- 存**完整快照**而不是 diff。Markdown 单篇通常几 KB，全量存让「恢复」
  变成一次赋值，不需要重放补丁链——复杂度差一个数量级，而存储省不下多少。
- ``content_md`` 允许为空串但不允许 NULL：快照要能如实反映「当时正文是空的」。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.user import User


class ArticleRevision(Base, TimestampMixin):
    """一次内容快照。

    刻意**不继承** Article 的字段命名习惯去存 category/tags/series：
    版本历史要保的是「文字」，分类标签属于元数据，恢复它们会把
    「我只是想找回删掉的那段话」变成一次难以预期的整体回滚。
    """

    __tablename__ = "article_revisions"
    __table_args__ = (
        # 版本列表的主查询：某篇文章的全部版本，按时间倒序
        Index("ix_article_revisions_article_created", "article_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 文章删除时版本一起删：留着孤立的版本没有意义，也无法恢复
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 谁改的。用户被删时置空而不是级联删除——版本历史不该因为删了个账号就消失
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    content_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 快照时正文的字数，让版本列表不用把所有正文都取出来就能显示「大小」
    content_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 产生这一版的原因：手动保存 / 状态流转 / 从某一版恢复
    reason: Mapped[str] = mapped_column(String(20), default="save", nullable=False)
    # 被恢复时指向源版本，便于在列表里标出「这一版是从 X 恢复来的」
    restored_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    article: Mapped[Article] = relationship(back_populates="revisions")
    author: Mapped[User | None] = relationship(lazy="joined")
