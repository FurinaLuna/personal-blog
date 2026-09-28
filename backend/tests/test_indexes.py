"""索引是否**真的存在**且**真的被用上**。

## 为什么单独一个文件

「建了索引」与「查询用得上索引」是两件事：索引名写错、列序不对（复合索引的
前导列必须匹配谓词）、或者索引声明只加了没进迁移，都会让它形同虚设，
而**功能测试全绿**——只是慢。这个文件用查询计划（``EXPLAIN QUERY PLAN``）
把"用上了"变成机器可验证的断言。

目前守三条：
1. ``article_tags(tag_id, article_id)`` 反向索引：按标签筛文章 / 标签云计数要用；
2. ``articles(status, published_ym)``：按月归档的筛选与分组要用
   （``published_ym`` 这一列存在的**唯一理由**就是让谓词可索引，
   所以这里同时反向断言"换成旧的列函数谓词，月份就用不上索引"）；
3. ``articles`` 上的列表排序索引方向（见 ``test_article_list_uses_index_for_month``
   之外的排序用例，如后续补）。

方言边界：``EXPLAIN QUERY PLAN`` 是 SQLite 语法；PostgreSQL 上这类断言要么
用 ``EXPLAIN`` 文本匹配、要么用 ``pg_indexes`` 检查存在性。两条都写会让本文件
迅速膨胀，所以这里对 PG 只断言索引**存在**（走 ``pg_indexes``），
计划断言仅 SQLite。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import IS_SQLITE

_INDEX = "ix_article_tags_tag_id"
_TABLE = "article_tags"

_YM_INDEX = "ix_articles_status_published_ym"
_ARTICLES = "articles"


def _session() -> AsyncSession:
    from app.db.session import async_session_factory

    return async_session_factory()


async def _sqlite_query_plan(session: AsyncSession, sql: str) -> str:
    rows = (await session.execute(text(f"EXPLAIN QUERY PLAN {sql}"))).all()
    return "\n".join(str(row[-1]) for row in rows)


def _literal_sql(stmt: Select[Any]) -> str:
    """把 ORM 语句编译成**带字面量**的 SQL。

    ``EXPLAIN QUERY PLAN`` 不接受占位符（``?`` 会被当成语法错误），
    所以断言必须建立在 literalbinds 编译出来的那版 SQL 上。
    """
    from app.db.session import engine

    return str(
        stmt.compile(dialect=engine.sync_engine.dialect, compile_kwargs={"literal_binds": True})
    )


class TestArticleTagsReverseIndex:
    """`article_tags` 的反向索引：主键 `(article_id, tag_id)` 只够一个方向。"""

    async def test_index_exists(self, db_reset: None) -> None:
        async with _session() as session:
            if IS_SQLITE:
                names = {
                    row[1]
                    for row in (await session.execute(text(f"PRAGMA index_list({_TABLE})"))).all()
                }
            else:
                names = {
                    row[0]
                    for row in (
                        await session.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE schemaname = current_schema() AND tablename = :t"
                            ),
                            {"t": _TABLE},
                        )
                    ).all()
                }

        assert _INDEX in names, (
            f"{_TABLE} 上缺少 {_INDEX}：按标签筛文章与标签云计数会退化成全表扫描 + 逐行 EXISTS"
        )

    @pytest.mark.sqlite_only
    async def test_tag_filtered_join_uses_the_index(self, db_reset: None) -> None:
        """标签 -> 文章方向的连接必须走这条索引（这是它存在的唯一理由）。"""
        async with _session() as session:
            plan = await _sqlite_query_plan(
                session,
                "SELECT a.id FROM articles a "
                "JOIN article_tags at ON at.article_id = a.id WHERE at.tag_id = 1",
            )

        assert _INDEX in plan, f"查询计划没有用上反向索引：\n{plan}"
        # 反向断言：不能是"扫描关联表"。索引缺失时正是这个形状。
        assert "SCAN at" not in plan, f"关联表被全表扫描，索引没有被用上：\n{plan}"


class TestPublishedYmArchiveIndex:
    """``articles(status, published_ym)``：按月归档的筛选与分组。

    这一列存在的**唯一理由**就是让谓词可索引。旧谓词是列的函数
    （``substr(cast(coalesce(published_at, created_at), text), 1, 7)``），
    任何索引都服务不了它：每取一个月都要从头扫一遍已发布集合，还必须回表取整行。
    换成裸列比较之后计划里会直接出现 ``published_ym=?`` —— 那才是"月份被当成
    索引约束"，而不只是"顺手用上了同一条索引"。
    """

    async def test_column_and_index_exist(self, db_reset: None) -> None:
        async with _session() as session:
            if IS_SQLITE:
                columns = {
                    row[1]
                    for row in (
                        await session.execute(text(f"PRAGMA table_info({_ARTICLES})"))
                    ).all()
                }
                names = {
                    row[1]
                    for row in (
                        await session.execute(text(f"PRAGMA index_list({_ARTICLES})"))
                    ).all()
                }
            else:
                columns = {
                    row[0]
                    for row in (
                        await session.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = :t"
                            ),
                            {"t": _ARTICLES},
                        )
                    ).all()
                }
                names = {
                    row[0]
                    for row in (
                        await session.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE schemaname = current_schema() AND tablename = :t"
                            ),
                            {"t": _ARTICLES},
                        )
                    ).all()
                }

        assert "published_ym" in columns, (
            "articles 上缺少 published_ym：归档谓词只能退回「列的函数」，索引永远用不上"
        )
        assert _YM_INDEX in names, (
            f"{_ARTICLES} 上缺少 {_YM_INDEX}：按月筛选与按月分组都会退化成全表扫描"
        )

    @pytest.mark.sqlite_only
    async def test_month_filter_seeks_the_index(self, db_reset: None) -> None:
        """取某个月的条目：月份必须是索引约束（``published_ym=?``）。"""
        from app.models import ArticleStatus
        from app.repositories import ArticleQueryRepository

        stmt = ArticleQueryRepository._by_month_stmt(
            year_month="2026-09", statuses=(ArticleStatus.PUBLISHED,), limit=200
        )
        async with _session() as session:
            plan = await _sqlite_query_plan(session, _literal_sql(stmt))

        assert _YM_INDEX in plan, f"按月筛选没有用上索引：\n{plan}"
        assert "published_ym=?" in plan, (
            f"月份没有被当成索引约束 —— 说明索引只是被 status 顺手用上，"
            f"每个月仍要扫过全部已发布行：\n{plan}"
        )
        assert "SCAN articles" not in plan, f"articles 被全表扫描：\n{plan}"

    @pytest.mark.sqlite_only
    async def test_archive_group_by_is_covered_by_the_index(self, db_reset: None) -> None:
        """按月分组计数：覆盖索引，不必回表。

        ``count(id)`` 里的 ``id`` 是 SQLite 的 rowid，索引项自带它，所以
        计划里出现 ``COVERING INDEX`` —— 整个归档接口一次索引扫描就能出结果。
        """
        from app.models import ArticleStatus
        from app.repositories import ArticleQueryRepository

        stmt = ArticleQueryRepository._archive_stmt((ArticleStatus.PUBLISHED,))
        async with _session() as session:
            plan = await _sqlite_query_plan(session, _literal_sql(stmt))

        assert f"COVERING INDEX {_YM_INDEX}" in plan, f"按月分组没有走覆盖索引：\n{plan}"
        assert "SCAN articles" not in plan, f"articles 被全表扫描：\n{plan}"

    @pytest.mark.sqlite_only
    async def test_legacy_function_predicate_cannot_seek_the_month(self, db_reset: None) -> None:
        """**反向验证**：换回旧的「列的函数」谓词，月份这一维就用不上索引。

        存在的意义是让上面两条断言有判别力：``ix_articles_status_published_ym``
        的前导列是 ``status``，所以哪怕谓词写成 ``substr(...)``，计划里**照样**会
        出现这个索引名（被 ``status IN (...)`` 用上）。真正区分新旧写法的是
        「有没有 ``published_ym=?`` 这个月份约束」——
        旧写法每个月份都要读一遍全部已发布行，这正是本批要修的成本。
        """
        legacy_sql = (
            "SELECT articles.id FROM articles "
            "WHERE articles.status IN ('published') "
            "AND substr(CAST(COALESCE(articles.published_at, articles.created_at) AS TEXT), 1, 7) "
            "= '2026-09'"
        )
        async with _session() as session:
            plan = await _sqlite_query_plan(session, legacy_sql)

        assert "published_ym=?" not in plan, (
            f"旧的列函数谓词竟然能按月份定位索引项？这条反向断言的假设已经不成立，"
            f"需要重新审视上面两条断言是否还有判别力：\n{plan}"
        )
