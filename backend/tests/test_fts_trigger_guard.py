"""FTS5 同步触发器：守卫语义、定义漂移、以及「旧定义自愈」。

## 为什么需要这个文件

``articles_fts_au`` 原来**没有 WHEN 守卫**，于是 ``articles`` 上任何 UPDATE 都会
重写整篇 FTS 索引。而详情页每次访问都会 ``view_count + 1``（点赞同理）——
**读一次文章就重新分词一次正文**，实测 100KB 正文 4.73ms，与正文长度线性相关。

更要命的是修它的方式：三条触发器都写成 ``CREATE TRIGGER IF NOT EXISTS``，
``IF NOT EXISTS`` **不会更新已存在的触发器**，所以只改代码里的 DDL
对**存量库完全无效**（新库拿到新定义、老库留着旧定义，且没有任何报错）。
这正是 ``add_guard_to_articles_fts_trigger`` 迁移与
``db/fulltext.py`` 的 ``_refresh_update_trigger`` 自愈逻辑要解决的问题。

三类用例分别守住：
1. **参数化行为**：只看阅读量不重建索引；正文/摘要变了必须重建（含 summary 从 NULL 变有值）；
2. **定义漂移**：sqlite_master 里的触发器体必须与代码里的常量逐字一致
   （否则「改了 DDL 但存量库没变」会静默发生）；
3. **自愈**：把触发器换成旧的（无守卫）定义后跑一次 ensure，必须被替换回新定义。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.fulltext import (
    CREATE_TRIGGER_SQL,
    FTS_TABLE,
    _refresh_update_trigger,
    ensure_fulltext_index,
)
from tests.conftest import IS_SQLITE

pytestmark = pytest.mark.sqlite_only

# 与 db/fulltext.py 里 *_au 那条对应；用于「自愈」用例把定义回退成旧版本
_LEGACY_UPDATE_TRIGGER = f"""
CREATE TRIGGER {FTS_TABLE}_au AFTER UPDATE ON articles BEGIN
    INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, summary, content_md)
    VALUES ('delete', old.id, old.title, old.summary, old.content_md);
    INSERT INTO {FTS_TABLE}(rowid, title, summary, content_md)
    VALUES (new.id, new.title, new.summary, new.content_md);
END
"""


def _normalize(sql: str | None) -> str:
    """归一化 DDL 以便逐字比较。

    **`IF NOT EXISTS` 必须被剥掉**：SQLite 存进 ``sqlite_master`` 时会把它去掉
    （实测：常量里是 ``CREATE TRIGGER IF NOT EXISTS x``，库内读回来是
    ``CREATE TRIGGER x``）。不归一这一处，比较永远不相等 ——
    那会让「定义漂移」检测恒为假阳性，也会让生产里的自愈逻辑
    **每次启动都重建一次触发器**（``_refresh_update_trigger`` 用的是同一套比较）。
    """
    normalized = " ".join((sql or "").split()).lower()
    return normalized.replace("if not exists ", "")


def _session() -> AsyncSession:
    from app.db.session import async_session_factory

    return async_session_factory()


async def _trigger_sql(session: AsyncSession, name: str) -> str | None:
    result = await session.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"),
        {"name": name},
    )
    row = result.first()
    return row[0] if row else None


async def _fts_match_count(session: AsyncSession, term: str) -> int:
    result = await session.execute(
        text(f"SELECT count(*) FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :q"), {"q": term}
    )
    return int(result.scalar_one())


async def _seed_article(session: AsyncSession, *, content: str = "原始正文甲乙丙") -> int:
    """直接插一行已发布文章。

    走裸 SQL 而不是 API 是为了让用例只依赖「触发器行为」这一件事。
    ``articles`` 上有好几个 NOT NULL 且**没有服务端默认值**的列
    （``is_top`` / ``allow_comment`` / ``view_count`` / ``like_count`` /
    ``reading_time`` / ``author_id``）—— 少写一个就是 NOT NULL 失败，
    所以这里一次给全。``author_id`` 先建一个作者。
    """
    author_id = (
        await session.execute(
            text(
                "INSERT INTO users (username, email, hashed_password, nickname, role, is_active) "
                "VALUES ('probe-author', 'probe@example.com', 'x', '探针', 'author', 1) RETURNING id"
            )
        )
    ).scalar_one()

    result = await session.execute(
        text(
            "INSERT INTO articles ("
            "  title, slug, content_md, summary, status, is_top, allow_comment,"
            "  view_count, like_count, reading_time, author_id"
            ") VALUES ("
            "  '标题甲乙丙', 'probe-slug', :content, NULL, 'published', 0, 1, 0, 0, 1, :author_id"
            ") RETURNING id"
        ),
        {"content": content, "author_id": author_id},
    )
    return int(result.scalar_one())


class TestTriggerDefinitions:
    """定义漂移检测：库里的触发器体必须与代码常量一致。"""

    async def test_all_three_triggers_match_code_constants(self, db_reset: None) -> None:
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            stored = {
                name: await _trigger_sql(session, name)
                for name in (f"{FTS_TABLE}_ai", f"{FTS_TABLE}_ad", f"{FTS_TABLE}_au")
            }

        expected = {}
        for statement in CREATE_TRIGGER_SQL:
            for name in stored:
                if f" {name} " in statement:
                    expected[name] = statement

        assert set(expected) == set(stored), "代码常量里缺少某条触发器的定义"
        for name, want in expected.items():
            assert _normalize(stored[name]) == _normalize(want), (
                f"{name} 的库内定义与代码常量不一致——"
                "说明「改了 DDL 但存量库没跟着变」（IF NOT EXISTS 不会更新已存在的触发器）"
            )

    async def test_update_trigger_has_when_guard(self, db_reset: None) -> None:
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            sql = await _trigger_sql(session, f"{FTS_TABLE}_au")
        assert sql is not None
        assert "WHEN" in sql.upper(), "AFTER UPDATE 触发器必须有 WHEN 守卫，否则每次浏览都重写索引"


class TestGuardBehaviour:
    """守卫的实际语义：只重建"内容真的变了"的那些更新。"""

    async def test_view_only_update_keeps_index_searchable(self, db_reset: None) -> None:
        """只改 view_count（详情页每次访问都做）不该动索引，但索引必须仍然可搜。"""
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            article_id = await _seed_article(session, content="只有阅读量变化的正文甲乙丙")
            await session.commit()

            assert await _fts_match_count(session, "只有阅读量变化") == 1

            for _ in range(3):
                await session.execute(
                    text("UPDATE articles SET view_count = view_count + 1 WHERE id = :id"),
                    {"id": article_id},
                )
            await session.commit()

            # 关键：守卫没有把该做的事也挡掉
            assert await _fts_match_count(session, "只有阅读量变化") == 1

    async def test_content_change_reindexes(self, db_reset: None) -> None:
        """正文变了必须重建：旧内容搜不到、新内容搜得到。"""
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            article_id = await _seed_article(session, content="改动之前的正文甲乙丙")
            await session.commit()
            assert await _fts_match_count(session, "改动之前的正文") == 1

            await session.execute(
                text("UPDATE articles SET content_md = '改动之后的正文丁戊己' WHERE id = :id"),
                {"id": article_id},
            )
            await session.commit()

            assert await _fts_match_count(session, "改动之前的正文") == 0
            assert await _fts_match_count(session, "改动之后的正文") == 1

    async def test_summary_null_to_value_reindexes(self, db_reset: None) -> None:
        """``summary`` 从 NULL 变成有值也必须重建。

        这条是守卫写法的核心回归点：如果写成 ``new.summary IS NOT old.summary``，
        旧值为 NULL 时结果是 NULL，整个 AND 短路 ⇒ 触发器不跑 ⇒
        「补上摘要」永远不进索引，而且完全没有报错。
        """
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            article_id = await _seed_article(session, content="正文与摘要无关甲乙丙")
            await session.commit()
            # 建的时候 summary 是 NULL
            assert (
                await session.execute(
                    text("SELECT summary FROM articles WHERE id = :id"), {"id": article_id}
                )
            ).scalar_one() is None

            await session.execute(
                text("UPDATE articles SET summary = '补上的摘要丁戊己' WHERE id = :id"),
                {"id": article_id},
            )
            await session.commit()

            assert await _fts_match_count(session, "补上的摘要丁戊己") == 1


class TestTriggerSelfHealing:
    """存量库里的旧触发器必须能被自动换成新定义。"""

    async def test_stale_unguarded_trigger_is_replaced(self, db_reset: None) -> None:
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            # 模拟「升级前建的库」：换成没有守卫的旧定义
            await session.execute(text(f"DROP TRIGGER IF EXISTS {FTS_TABLE}_au"))
            await session.execute(text(_LEGACY_UPDATE_TRIGGER))
            await session.commit()

            stale = await _trigger_sql(session, f"{FTS_TABLE}_au")
            assert stale is not None and "WHEN" not in stale.upper()

            # ensure_fulltext_index 是启动钩子与 db_reset 都会走的那条路径
            assert await ensure_fulltext_index(session) is True
            await session.commit()

            healed = await _trigger_sql(session, f"{FTS_TABLE}_au")
            assert healed is not None and "WHEN" in healed.upper(), "旧触发器没有被自愈替换"

    async def test_refresh_is_idempotent(self, db_reset: None) -> None:
        """定义已经一致时不该反复重建（否则每次启动都改一次 schema）。"""
        if not IS_SQLITE:
            pytest.skip("FTS5 只在 SQLite 上存在")
        async with _session() as session:
            assert await _refresh_update_trigger(session) is False
            await session.commit()
