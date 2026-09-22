"""add pg_trgm search indexes for postgresql

Revision ID: a7c1e9d4b2f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-22 18:40:00.000000

为什么需要这个迁移
==================

``articles_fts``（FTS5）是 SQLite 专有的虚拟表，上一个迁移在非 SQLite 方言上
直接 return —— 那个决定本身没错（不假装支持没验证过的东西），但它留下了一个
**只在生产方言上出现**的后果：

- compose 里生产库是 ``postgres:16``；
- PG 上没有 FTS5，``fulltext_available()`` 恒为 False，搜索永远走
  ``title/summary/content_md ILIKE '%kw%'``；
- 三列都没有索引 ⇒ **每次搜索都是全表扫描**，而且带 OR + CASE 打分 + 同条件
  COUNT。``/articles/search`` 又不在限流名单里。

也就是说：这个项目投入最多的一条功能链路（FTS5 + trigram + LIKE 兜底 + 打分），
在它唯一的生产库上退化成了最慢的那条路径。

方案：``pg_trgm`` + GIN
=======================

不选 tsvector 的理由：PG 原生的全文检索需要中文分词器（zhparser / pg_jieba），
那是"扩展的扩展"，且在 1–2 字短查询上仍要退回 LIKE —— 而中文搜索里短查询
恰恰是主流（「博客」「数据」「测试」）。

``pg_trgm`` 的三元组索引对 LIKE/ILIKE **直接生效**，现有 SQL 一行不用改，
短查询也照样受益。

边界（诚实记录）：

- 需要 ``CREATE EXTENSION pg_trgm``。PG 13 起它被标记为 trusted，库 owner
  即可创建；没有权限时迁移会失败 —— 那种情况下应改由 DBA 预建扩展，
  或接受"搜索退回全表扫描"（应用启动钩子会尝试建，失败只记日志）。
- 索引会增加写入成本与磁盘占用（``content_md`` 越长越明显）。
  对个人博客的写入量可忽略，这是刻意取舍。
- 本迁移在 SQLite 上是 **no-op**（保持"升降级都可用"这一契约）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7c1e9d4b2f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app/db/fulltext.py 中的定义保持一致（刻意重复：迁移是历史快照，
# 不应随应用代码演进而变化——两者共享一份定义会把改动追溯到已执行过的迁移上）。
_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS pg_trgm"

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_articles_title_trgm ON articles USING gin (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_articles_summary_trgm "
    "ON articles USING gin (summary gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_articles_content_trgm "
    "ON articles USING gin (content_md gin_trgm_ops)",
)

_INDEX_NAMES = (
    "ix_articles_title_trgm",
    "ix_articles_summary_trgm",
    "ix_articles_content_trgm",
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        # SQLite 的检索加速是 FTS5，由 d9e5f6a7b8c0 负责；这里什么都不做。
        return

    op.execute(_CREATE_EXTENSION)
    for statement in _CREATE_INDEXES:
        op.execute(statement)


def downgrade() -> None:
    if not _is_postgresql():
        return

    for name in _INDEX_NAMES:
        op.execute(f"DROP INDEX IF EXISTS {name}")

    # 刻意**不** DROP EXTENSION pg_trgm：
    # 1) 扩展是库级对象，可能还有别的索引/查询在用，回滚一次迁移不该把它拆掉；
    # 2) 有些托管 PG（RDS 等）不允许 DROP EXTENSION，会让回滚直接失败——
    #    而"回滚必须能跑通"是这个仓库对迁移的硬要求。
