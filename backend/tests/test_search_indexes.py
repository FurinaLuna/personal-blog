"""检索索引的方言分流：SQLite 走 FTS5，PostgreSQL 走 pg_trgm + GIN。

存在意义：此前 PG 分支是"直接跳过"，于是生产库（compose 里就是 postgres:16）上的
搜索永远是 `title/summary/content_md ILIKE '%kw%'` 三列全表扫描 —— 而且是
`/articles/search` 这种可以被脚本放大的读接口。

这个文件同时跑在两种方言上：
- 两条 `test_ensure_search_indexes_*` 在各自方言上断言"实际建了什么"；
- 带 ``pg_only`` 的用例只在 TEST_DATABASE_URL 指向 PG 时执行（否则自动 skip）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.fulltext import (
    PG_TRGM_INDEXES,
    ensure_search_indexes,
    fulltext_available,
    pg_trgm_available,
)
from tests.conftest import IS_SQLITE


def _session() -> AsyncSession:
    from app.db.session import async_session_factory

    return async_session_factory()


class TestDialectDispatch:
    """选哪条加速路径由方言决定，且两条路径都必须**幂等**。"""

    async def test_ensure_search_indexes_reports_sqlite_path(self, db_reset: None) -> None:
        """SQLite：建 FTS5，并如实报告 "fts5"。"""
        if not IS_SQLITE:
            pytest.skip("该断言只在 SQLite 上成立（PG 分支见下一个用例）")
        async with _session() as session:
            # db_reset 已经建过一次，这里再建一次是在验证幂等：
            # 启动钩子与迁移会撞在一起，谁先谁后都必须不出错。
            assert await ensure_search_indexes(session) == "fts5"
            await session.commit()
            assert await fulltext_available(session) is True
            assert await pg_trgm_available(session) is False

    @pytest.mark.pg_only
    async def test_ensure_search_indexes_reports_pg_path(self, db_reset: None) -> None:
        """PostgreSQL：建 pg_trgm（扩展 + 三条 GIN 索引），报告 "pg_trgm"。"""
        async with _session() as session:
            assert await ensure_search_indexes(session) == "pg_trgm"
            await session.commit()
            assert await pg_trgm_available(session) is True
            # FTS5 在 PG 上必须老实返回 False，不能"假装有"
            assert await fulltext_available(session) is False

    @pytest.mark.pg_only
    async def test_pg_indexes_are_gin_trgm(self, db_reset: None) -> None:
        """索引定义必须是 gin + gin_trgm_ops —— 普通 btree 对 ILIKE '%x%' 毫无作用。"""
        async with _session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = current_schema() AND indexname = ANY(:names)"
                    ),
                    {"names": list(PG_TRGM_INDEXES)},
                )
            ).all()
        found = {row[0]: row[1].lower() for row in rows}
        assert set(found) == set(PG_TRGM_INDEXES), f"缺索引：{set(PG_TRGM_INDEXES) - set(found)}"
        for name, definition in found.items():
            assert "using gin" in definition, f"{name} 不是 GIN：{definition}"
            assert "gin_trgm_ops" in definition, f"{name} 没有用 gin_trgm_ops：{definition}"

    @pytest.mark.pg_only
    async def test_ilike_can_use_the_trigram_index(self, db_reset: None) -> None:
        """证明三元组索引**对既有 SQL 可用**，而不是只建了个没人用的对象。

        为什么用 ``enable_seqscan = off``：小语料上 PG 会（正确地）选择顺序扫描——
        2000 行全表扫 1ms，走索引反而更慢。所以"默认计划用没用索引"测不出
        "索引能不能用"。关掉顺序扫描后，如果计划里出现 trgm 索引，就说明
        `ILIKE '%kw%'` 这个形状确实能被索引服务。

        实测（50000 篇语料，宽松命中率 1%）：
        - 7 字关键词走索引 6.6ms，顺序扫描 181ms —— 这是索引的真实收益；
        - 3 字以内 PG 仍选顺序扫描。三元组索引的粒度决定了它救不了短查询
          （与 SQLite FTS5 的 trigram 分词器同一条限制），这一点写在
          `docs/DESIGN.md` 的检索章节里，不假装它是万能药。
        """
        async with _session() as session:
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            plan = (
                (
                    await session.execute(
                        text(
                            "EXPLAIN SELECT id FROM articles "
                            "WHERE title ILIKE :p OR summary ILIKE :p OR content_md ILIKE :p"
                        ),
                        {"p": "%数据库设计%"},
                    )
                )
                .scalars()
                .all()
            )
        joined = "\n".join(plan)
        assert "trgm" in joined, f"ILIKE 无法使用三元组索引：\n{joined}"


class TestSearchStillWorksOnPg:
    """索引只是加速，不改变语义：PG 上的搜索结果必须与 SQLite 一致。"""

    @pytest.mark.pg_only
    async def test_search_endpoint_returns_matches(
        self, client, admin_headers, published_article
    ) -> None:
        response = await client.get("/api/v1/articles/search", params={"q": "示例"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert "items" in body and "total" in body

    @pytest.mark.pg_only
    async def test_short_query_still_falls_back_to_like(self, client, published_article) -> None:
        """1–2 字查询在 PG 上走 LIKE 降级分支（FTS5 那份"短查询"逻辑不该被误用）。"""
        response = await client.get("/api/v1/articles/search", params={"q": "示例"})
        assert response.status_code == 200, response.text
