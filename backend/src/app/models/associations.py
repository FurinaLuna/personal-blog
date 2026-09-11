"""多对多关联表。单独成文件，避免 article / taxonomy 互相 import 造成循环依赖。"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Table

from app.db.base import Base

# 文章 <-> 标签（多对多）。用纯 Table 而非关联对象：
# 该关系没有额外属性，避免为一个连接表凭空造一个 ORM 类。
article_tags = Table(
    "article_tags",
    Base.metadata,
    Column("article_id", Integer, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)
