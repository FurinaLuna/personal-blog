"""访问统计测试。

核心契约：
1. 记录口径与 view_count 一致——仅已发布文章的详情访问（草稿预览 / 归档 / 列表都不记）；
2. 缺日补零——曲线数据永远是连续日期序列；
3. 聚合接口仅站长可用。
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload

STATS_URL = "/api/v1/stats/views/daily"


async def today_row(client: AsyncClient, admin_headers: dict[str, str]) -> dict:
    """取统计序列的最后一项（今天）。"""
    rows = (await client.get(STATS_URL, headers=admin_headers)).json()
    return rows[-1]


class TestVisitRecording:
    async def test_detail_views_are_recorded(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """详情访问 3 次 → 记 3 条（PV 语义）。"""
        for _ in range(3):
            await client.get(f"/api/v1/articles/{published_article['slug']}")

        today = await today_row(client, admin_headers)
        assert today["views"] == 3

    async def test_same_ip_counts_one_uv(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """同 IP（测试下 ASGITransport 全部取不到 client，归一为同一摘要）多次
        访问 UV=1——uv < views 即证明 distinct 生效。"""
        for _ in range(3):
            await client.get(f"/api/v1/articles/{published_article['slug']}")

        today = await today_row(client, admin_headers)
        assert today["views"] == 3
        assert today["unique_visitors"] == 1

    async def test_draft_preview_not_recorded(
        self,
        client: AsyncClient,
        author_headers: dict[str, str],
        admin_headers: dict[str, str],
    ) -> None:
        """作者预览自己的草稿：不记录（草稿不是内容分发）。"""
        draft = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(status="draft"),
                headers=author_headers,
            )
        ).json()
        response = await client.get(f"/api/v1/articles/{draft['slug']}", headers=author_headers)
        assert response.status_code == 200

        today = await today_row(client, admin_headers)
        assert today["views"] == 0

    async def test_archived_article_not_recorded(
        self,
        client: AsyncClient,
        published_article: dict,
        author_headers: dict[str, str],
        admin_headers: dict[str, str],
    ) -> None:
        """归档文章详情仍可达但不记录——与 view_count「仅已发布累计」同口径。"""
        archived = await client.patch(
            f"/api/v1/articles/{published_article['id']}",
            json={"status": "archived"},
            headers=author_headers,
        )
        assert archived.status_code == 200
        assert (
            await client.get(f"/api/v1/articles/{published_article['slug']}")
        ).status_code == 200

        today = await today_row(client, admin_headers)
        assert today["views"] == 0

    async def test_list_api_records_nothing(
        self, client: AsyncClient, published_article: dict, admin_headers: dict[str, str]
    ) -> None:
        """列表接口不产生记录——只记详情页。"""
        for _ in range(2):
            await client.get("/api/v1/articles")

        today = await today_row(client, admin_headers)
        assert today["views"] == 0


class TestDailyViewsApi:
    async def test_zero_fill_keeps_series_continuous(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """days=7：数据库里只有今天一条，但序列必须 7 天连续且日期不重不漏。"""
        await client.get(f"/api/v1/articles/{published_article['slug']}")

        rows = (await client.get(f"{STATS_URL}?days=7", headers=admin_headers)).json()
        assert len(rows) == 7
        dates = [row["date"] for row in rows]
        assert len(set(dates)) == 7  # 不重
        assert dates == sorted(dates)  # 不漏且升序
        assert rows[-1]["views"] == 1  # 只有今天有量
        assert all(row["views"] == 0 for row in rows[:-1])  # 缺日补零

    async def test_days_validation(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        for bad_days in (0, 91):
            response = await client.get(f"{STATS_URL}?days={bad_days}", headers=admin_headers)
            assert response.status_code == 422

    async def test_author_gets_403(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        assert (await client.get(STATS_URL, headers=author_headers)).status_code == 403

    async def test_guest_gets_401(self, client: AsyncClient) -> None:
        assert (await client.get(STATS_URL)).status_code == 401


class TestVisitLogPruning:
    """访问日志留存清理。

    存在意义：record() 每次详情访问写一行，而读取只覆盖近 90 天。
    没有清理的话这张表只增不减，代价（备份体积 / 迁移耗时 / VACUUM 时间）
    是持续的，而收益是零。
    """

    async def _insert_old_log(self, article_id: int, days_ago: int, count: int = 1) -> None:
        from datetime import UTC, datetime, timedelta

        from app.db.session import async_session_factory
        from app.repositories import VisitLogRepository

        day = (datetime.now(UTC) - timedelta(days=days_ago)).date()
        async with async_session_factory() as session:
            repo = VisitLogRepository(session)
            for index in range(count):
                # 61 位而不是 62 位：ip_hash 是 String(64)，"old" + 62 位 = 65 字符。
                # SQLite 不校验 VARCHAR 长度，所以这里曾长期悄悄写超长值；
                # 换成 PostgreSQL 会直接 StringDataRightTruncationError（PG 测试路径抓到的）。
                await repo.create(article_id=article_id, date=day, ip_hash=f"old{index:061d}")
            await session.commit()

    async def test_prune_removes_only_expired_rows(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """留存期外的删掉，期内的保留。"""
        article_id = int(published_article["id"])
        await self._insert_old_log(article_id, days_ago=400, count=3)
        await self._insert_old_log(article_id, days_ago=10, count=2)

        response = await client.post(
            "/api/v1/stats/visit-logs/prune?retention_days=180", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["removed"] == 3
        assert body["retention_days"] == 180

        # 近 10 天那两条必须还在：清理不能误伤读取窗口内的数据
        from sqlalchemy import func, select

        from app.db.session import async_session_factory
        from app.models import VisitLog

        async with async_session_factory() as session:
            remaining = await session.execute(select(func.count()).select_from(VisitLog))
            assert int(remaining.scalar_one()) == 2

    async def test_prune_keeps_what_the_chart_reads(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """清理之后趋势曲线不能出现空洞：默认留存期必须大于读取窗口。"""
        article_id = int(published_article["id"])
        await self._insert_old_log(article_id, days_ago=80, count=1)

        await client.post("/api/v1/stats/visit-logs/prune", headers=admin_headers)

        rows = (await client.get(f"{STATS_URL}?days=90", headers=admin_headers)).json()
        assert len(rows) == 90
        # 80 天前那条仍在：说明默认留存期（180 天）确实比读取窗口（90 天）宽
        assert rows[-81]["views"] == 1

    async def test_only_admin_can_prune(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """破坏性维护操作不能开放给作者。"""
        response = await client.post("/api/v1/stats/visit-logs/prune", headers=author_headers)
        assert response.status_code == 403

    async def test_guest_cannot_prune(self, client: AsyncClient) -> None:
        assert (await client.post("/api/v1/stats/visit-logs/prune")).status_code == 401

    async def test_retention_days_is_validated(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """retention_days=0 会让「删掉 0 天前的所有行」= 清空整张表，必须挡在路由层。"""
        response = await client.post(
            "/api/v1/stats/visit-logs/prune?retention_days=0", headers=admin_headers
        )
        assert response.status_code == 422
