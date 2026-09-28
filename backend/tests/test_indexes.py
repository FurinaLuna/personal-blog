"""索引是否**真的存在**且**真的被用上**。

## 为什么单独一个文件

「建了索引」与「查询用得上索引」是两件事：索引名写错、列序不对（复合索引的
前导列必须匹配谓词）、或者索引声明只加了没进迁移，都会让它形同虚设，
而**功能测试全绿**——只是慢。这个文件用查询计划（``EXPLAIN QUERY PLAN``）
把"用上了"变成机器可验证的断言。

目前守两条：
1. ``article_tags(tag_id, article_id)`` 反向索引：按标签筛文章 / 标签云计数要用；
2. ``articles`` 上的列表排序索引方向（见 ``test_article_list_uses_index_for_month``
   之外的排序用例，如后续补）。

方言边界：``EXPLAIN QUERY PLAN`` 是 SQLite 语法；PostgreSQL 上这类断言要么
用 ``EXPLAIN`` 文本匹配、要么用 ``pg_indexes`` 检查存在性。两条都写会让本文件
迅速膨胀，所以这里对 PG 只断言索引**存在**（走 ``pg_indexes``），
计划断言仅 SQLite。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import IS_SQLITE

_INDEX = "ix_article_tags_tag_id"
_TABLE = "article_tags"


def _session() -> AsyncSession:
    from app.db.session import async_session_factory

    return async_session_factory()


async def _sqlite_query_plan(session: AsyncSession, sql: str) -> str:
    rows = (await session.execute(text(f"EXPLAIN QUERY PLAN {sql}"))).all()
    return "\n".join(str(row[-1]) for row in rows)


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
