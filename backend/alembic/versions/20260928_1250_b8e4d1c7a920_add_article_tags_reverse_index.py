"""add reverse index on article_tags (tag_id, article_id)

Revision ID: b8e4d1c7a920
Revises: a4c9e2f71b38
Create Date: 2026-09-28 12:50:00.000000

## 为什么需要

``article_tags`` 原先只有主键 ``(article_id, tag_id)``，那个复合索引只服务
「这篇文章有哪些标签」这个方向。但项目里两个高频查询要的是**反方向**
``tag_id -> article_id``：

1. 按标签筛文章 —— ``Article.tags.any(Tag.slug == ...)``
   （``repositories/article_query_repository.py`` 的 ``build_article_filter``）；
2. 标签云计数 —— ``article_tags JOIN tags ON tag_id = tags.id``
   （``repositories/taxonomy_repository.py`` 的 ``list_with_counts``）。

SQLite 与 PostgreSQL 都**不会**自动为复合主键建反向索引，于是这两条只能对
``articles`` 做全表扫描 + 逐行 ``EXISTS`` 判定，随文章数线性变慢。
标签页与「按标签筛选」正是最常被点到的两个入口，因此补一条
``(tag_id, article_id)`` 的复合索引：前导列用于定位，第二列让索引本身覆盖连接，
不必回表。

## 与模型保持一致

索引同时声明在 ``models/associations.py`` 的 ``article_tags`` 上，
所以「自动建表」路径（测试与开发库）也拿得到同一个索引；
本迁移负责让**已存在的库**补上它。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4d1c7a920"
down_revision: str | None = "a4c9e2f71b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_article_tags_tag_id"
_TABLE = "article_tags"
_COLUMNS = ["tag_id", "article_id"]


def upgrade() -> None:
    # 幂等：两条建表路径（迁移 / create_all）可能都跑过
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return
    existing = {index["name"] for index in inspector.get_indexes(_TABLE)}
    if _INDEX_NAME in existing:
        return
    op.create_index(_INDEX_NAME, _TABLE, _COLUMNS, unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name=_TABLE)
