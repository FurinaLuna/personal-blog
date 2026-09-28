"""多对多关联表。单独成文件，避免 article / taxonomy 互相 import 造成循环依赖。"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, Table

from app.db.base import Base

# 文章 <-> 标签（多对多）。用纯 Table 而非关联对象：
# 该关系没有额外属性，避免为一个连接表凭空造一个 ORM 类。
article_tags = Table(
    "article_tags",
    Base.metadata,
    Column("article_id", Integer, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    # 反向索引：`tag_id -> article_id`。
    #
    # 主键 `(article_id, tag_id)` 只服务「这篇文章有哪些标签」这个方向，
    # 而下面两个高频查询要的是**反方向**：
    #   - 按标签筛文章：`Article.tags.any(Tag.slug == ...)`
    #     （见 article_query_repository 的 build_article_filter）
    #   - 标签云计数：`article_tags JOIN tags ON tag_id = tags.id`
    #     （见 taxonomy_repository.list_with_counts）
    # 没有它时，这两条只能对 articles 做全表扫描 + 逐行 EXISTS 判定，
    # 随文章数线性变慢。SQLite 与 PostgreSQL 都不会自动为复合主键建反向索引。
    Index("ix_article_tags_tag_id", "tag_id", "article_id"),
)
