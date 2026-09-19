"""create articles full-text index (FTS5)

Revision ID: d9e5f6a7b8c0
Revises: c8d4e5f6a7b9
Create Date: 2026-09-17 19:10:00.000000

实现说明（**分词器选择是这个迁移最关键的决定**）：

原本的搜索是 `title/summary/content_md` 三字段 `LIKE '%kw%'`，永远走不了索引，
文章上百篇后就是全表扫描，而且无法按相关度排序。

FTS5 有两个候选分词器，实测结果决定了选型：

- ``unicode61``（默认）：**对中文完全无效**。它按 Unicode 类别切词，一整段
  连续汉字会被当成**一个** token。实测搜「数据层」在
  「我把博客的数据层重写了一遍」里返回 0 条，只有把整句原样贴进去才命中。
  用它等于把中文搜索做成了「精确全文匹配」，比原来的 LIKE 还差。
- ``trigram``：按三字符滑窗建索引，中文子串检索正常，``bm25()`` 排序与
  ``snippet()`` 高亮都能用。代价是**查询词必须 ≥3 个字符**——
  实测「数据」「博客」这类两字查询返回 0 条。

所以最终方案是**混合**：≥3 字符走 FTS5（快、带相关度排序），
1–2 字符退回 LIKE（慢但不漏）。服务层按长度分流，见
``ArticleRepository.search_ids`` 与 ``ArticleService.search``。

这个迁移只在 SQLite 上建 FTS5 对象；其它方言（PostgreSQL）直接跳过，
搜索会自动退回原有的 LIKE 实现——不假装支持没验证过的东西。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e5f6a7b8c0"
down_revision: str | None = "c8d4e5f6a7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# external content 模式：FTS 表只存倒排索引，正文仍留在 articles 里，
# 不占第二份存储。代价是必须自己维护同步触发器。
_CREATE_FTS = """
CREATE VIRTUAL TABLE articles_fts USING fts5(
    title,
    summary,
    content_md,
    content='articles',
    content_rowid='id',
    tokenize='trigram'
)
"""

# 三个触发器覆盖增删改。AFTER UPDATE 用 'delete' 再 'insert' 是 FTS5 官方
# 对 external content 表的标准做法——直接 update 会让索引与正文脱节。
_CREATE_TRIGGERS = [
    """
    CREATE TRIGGER articles_fts_ai AFTER INSERT ON articles BEGIN
        INSERT INTO articles_fts(rowid, title, summary, content_md)
        VALUES (new.id, new.title, new.summary, new.content_md);
    END
    """,
    """
    CREATE TRIGGER articles_fts_ad AFTER DELETE ON articles BEGIN
        INSERT INTO articles_fts(articles_fts, rowid, title, summary, content_md)
        VALUES ('delete', old.id, old.title, old.summary, old.content_md);
    END
    """,
    """
    CREATE TRIGGER articles_fts_au AFTER UPDATE ON articles BEGIN
        INSERT INTO articles_fts(articles_fts, rowid, title, summary, content_md)
        VALUES ('delete', old.id, old.title, old.summary, old.content_md);
        INSERT INTO articles_fts(rowid, title, summary, content_md)
        VALUES (new.id, new.title, new.summary, new.content_md);
    END
    """,
]

_TRIGGER_NAMES = ("articles_fts_ai", "articles_fts_ad", "articles_fts_au")

# 回填存量数据：external content 表建立时索引是空的，必须显式 rebuild
_REBUILD = "INSERT INTO articles_fts(articles_fts) VALUES ('rebuild')"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _is_sqlite():
        # PostgreSQL 用 tsvector + GIN 才是正解，但那需要另一套配置与中文分词器
        # （zhparser / pg_jieba）。没验证过的实现不写进来，搜索会退回 LIKE。
        return

    # **幂等**：应用启动时也会建这套索引（``db/fulltext.py`` 的
    # ``ensure_fulltext_index``，为了让「自动建表 + 不跑迁移」的开发路径也能搜索）。
    # 两条路径会撞在一起 —— 开发库由 create_all + 启动钩子建好后，
    # alembic_version 还停在旧版本，此时跑 upgrade 就会因「表已存在」而失败。
    # 两边都幂等，谁先谁后都不会出错。
    if _has_table("articles_fts"):
        op.execute(_REBUILD)
        return

    op.execute(_CREATE_FTS)
    for statement in _CREATE_TRIGGERS:
        op.execute(statement)
    op.execute(_REBUILD)


def downgrade() -> None:
    if not _is_sqlite():
        return

    for name in _TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS articles_fts")
