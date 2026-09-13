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
