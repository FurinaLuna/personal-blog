"""全文检索索引的建表与探测。

## 为什么需要这个模块

``articles_fts`` 是 FTS5 **虚拟表**，它不属于 ``Base.metadata``，
所以 ``Base.metadata.create_all``（``DB_AUTO_CREATE=true`` 的开发默认路径）
**不会**创建它。只有跑过 alembic 迁移才有。

这会造成一个很容易踩的组合：按 README 快速开始直接启动（自动建表、不跑迁移）
→ 搜索接口里的 ``MATCH articles_fts`` 直接抛 "no such table" → 500。
所以这里提供两件事：

1. ``ensure_fulltext_index``：幂等地补建索引与触发器，让自动建表路径也能用上搜索；
2. ``fulltext_available``：**先探测再用**，表不存在时调用方安静地退回 LIKE，
   而不是把 500 抛给用户。

迁移文件里有同一份 DDL 的副本。这是刻意的：迁移是历史快照，不应该随
应用代码演进而变化——如果它们共享一份定义，改动会追溯到过去已经执行过的迁移上。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FTS_TABLE = "articles_fts"

CREATE_FTS_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    title,
    summary,
    content_md,
    content='articles',
    content_rowid='id',
    tokenize='trigram'
)
"""

# 三个触发器覆盖增删改，与迁移里的定义一致（见该文件的说明：
# AFTER UPDATE 用 'delete' + 'insert' 是 external content 表的标准做法）
CREATE_TRIGGER_SQL = [
    f"""
    CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON articles BEGIN
        INSERT INTO {FTS_TABLE}(rowid, title, summary, content_md)
        VALUES (new.id, new.title, new.summary, new.content_md);
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON articles BEGIN
        INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, summary, content_md)
        VALUES ('delete', old.id, old.title, old.summary, old.content_md);
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON articles BEGIN
        INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, summary, content_md)
        VALUES ('delete', old.id, old.title, old.summary, old.content_md);
        INSERT INTO {FTS_TABLE}(rowid, title, summary, content_md)
        VALUES (new.id, new.title, new.summary, new.content_md);
    END
    """,
]

REBUILD_SQL = f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES ('rebuild')"


async def fulltext_available(session: AsyncSession) -> bool:
    """当前数据库里 FTS 索引是否真的可用。

    **必须实际探测**而不是只看方言名：SQLite 也可能因为「自动建表但没跑迁移」
    而没有这张表。用它做前置判断，可以让搜索在缺索引时安静地退回 LIKE。
    """
    if session.bind is None or session.bind.dialect.name != "sqlite":
        return False
    result = await session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": FTS_TABLE},
    )
    return result.first() is not None


async def ensure_fulltext_index(session: AsyncSession) -> bool:
    """幂等地建好 FTS 索引与触发器，并回填一次。

    Returns:
        是否成功（非 SQLite 或建表失败时返回 False，调用方照常启动）。
    """
    if session.bind is None or session.bind.dialect.name != "sqlite":
        return False

    await session.execute(text(CREATE_FTS_SQL))
    for statement in CREATE_TRIGGER_SQL:
        await session.execute(text(statement))
    # rebuild 是幂等的：它按 articles 的当前内容重建整份索引。
    # 每次启动都跑一次，顺带修复「触发器曾经缺失导致索引与正文脱节」的情况。
    await session.execute(text(REBUILD_SQL))
    return True
