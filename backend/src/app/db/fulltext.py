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


# ---------------------------------------------------------------------------
# PostgreSQL：pg_trgm + GIN
# ---------------------------------------------------------------------------
#
# FTS5 是 SQLite 专有的虚拟表，PG 上没有对应物。此前 PG 分支的处理是"直接跳过"，
# 于是生产库（compose 里就是 postgres:16）上的搜索永远是
# ``title/summary/content_md ILIKE '%kw%'`` 三列全表扫描 —— 而且
# ``/articles/search`` 还不在限流名单里，等于一个廉价的放大攻击面。
#
# 这里不去做 tsvector：那需要中文分词器（zhparser / pg_jieba），是**扩展的扩展**，
# 且在"短查询"（1–2 字）上依然要退回 LIKE —— 而中文搜索里短查询恰恰是主流
# （「博客」「数据」「测试」）。pg_trgm 的三元组索引对 LIKE/ILIKE 直接生效，
# 与既有 SQL 零改动，覆盖面反而更全。
#
# 代价与边界：
# - 需要 `CREATE EXTENSION pg_trgm`（PG 13+ 标记为 trusted，库 owner 就能建；
#   没有权限时这里会失败并**安静退回全表扫描**，不影响服务可用性）；
# - 索引会让写入变慢、体积变大（content_md 越长越明显）。对个人博客的写入量
#   可以忽略，这是刻意的取舍。
PG_TRGM_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS pg_trgm"

# 三个字段各建一条：查询是 `a ILIKE x OR b ILIKE x OR c ILIKE x`，
# PG 会用 BitmapOr 把三条索引合并，比单条表达式索引更贴合现有 SQL。
PG_TRGM_INDEXES: dict[str, str] = {
    "ix_articles_title_trgm": (
        "CREATE INDEX IF NOT EXISTS ix_articles_title_trgm "
        "ON articles USING gin (title gin_trgm_ops)"
    ),
    "ix_articles_summary_trgm": (
        "CREATE INDEX IF NOT EXISTS ix_articles_summary_trgm "
        "ON articles USING gin (summary gin_trgm_ops)"
    ),
    "ix_articles_content_trgm": (
        "CREATE INDEX IF NOT EXISTS ix_articles_content_trgm "
        "ON articles USING gin (content_md gin_trgm_ops)"
    ),
}


def _dialect(session: AsyncSession) -> str:
    """当前会话绑定的方言名（拿不到时返回空串）。"""
    if session.bind is None:
        return ""
    return session.bind.dialect.name


async def pg_trgm_available(session: AsyncSession) -> bool:
    """pg_trgm 扩展与三条 GIN 索引是否都已就位（PG 上的"检索加速可用"）。

    与 ``fulltext_available`` 的区别：那个问的是"SQLite 的 FTS5 能不能用"，
    这个问的是"PG 的三元组索引建好没有"。两者都只影响**走哪条加速路径**，
    不影响搜索结果本身——没有加速时 ILIKE 依然返回正确结果（只是慢）。
    """
    if _dialect(session) != "postgresql":
        return False
    result = await session.execute(
        text(
            "SELECT count(*) FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = ANY(:names)"
        ),
        {"names": list(PG_TRGM_INDEXES)},
    )
    return int(result.scalar_one()) == len(PG_TRGM_INDEXES)


async def ensure_pg_trgm_indexes(session: AsyncSession) -> bool:
    """幂等地建好 pg_trgm 扩展与三条 GIN 索引。

    Returns:
        是否成功。任何一步失败（最常见的是没有 CREATE EXTENSION 权限）
        都返回 False，让调用方记一条日志继续启动——搜索会退回全表扫描，
        结果依然正确，只是慢。
    """
    if _dialect(session) != "postgresql":
        return False

    await session.execute(text(PG_TRGM_EXTENSION_SQL))
    for statement in PG_TRGM_INDEXES.values():
        await session.execute(text(statement))
    return True


async def ensure_search_indexes(session: AsyncSession) -> str:
    """按方言补建检索索引，返回实际做了什么（供启动日志使用）。

    取值：``"fts5"``（SQLite）、``"pg_trgm"``（PostgreSQL）、``""``（都没有）。

    两条路径都幂等，且都**尽力而为**：索引是增强项，建不出来不该挡住启动。
    迁移文件里有同一份 DDL 的副本（迁移是历史快照，不随应用代码演进）。
    """
    dialect = _dialect(session)
    if dialect == "sqlite":
        return "fts5" if await ensure_fulltext_index(session) else ""
    if dialect == "postgresql":
        return "pg_trgm" if await ensure_pg_trgm_indexes(session) else ""
    return ""
