"""系列（技术连载 / 合集）测试。"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload, unique_suffix


def make_series_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": f"系列-{unique_suffix()}", "description": "连载测试"}
    payload.update(overrides)
    return payload


class TestSeriesCrud:
    async def test_create_generates_slug(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/series", json=make_series_payload(), headers=author_headers
        )
        assert response.status_code == 201
        body = response.json()
        assert body["slug"]
        assert body["description"] == "连载测试"
        assert body["article_count"] == 0

    async def test_duplicate_name_conflicts(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        name = f"重复系列{unique_suffix()}"
        assert (
            await client.post("/api/v1/series", json={"name": name}, headers=author_headers)
        ).status_code == 201
        again = await client.post("/api/v1/series", json={"name": name}, headers=author_headers)
        assert again.status_code == 409

    async def test_guest_cannot_create(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/series", json=make_series_payload())
        assert response.status_code == 401

    async def test_update_and_clear_description(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        updated = await client.patch(
            f"/api/v1/series/{series['id']}",
            json={"name": f"改名{unique_suffix()}", "description": None},
            headers=author_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] != series["name"]
        assert updated.json()["description"] is None

    async def test_author_cannot_delete_series(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        denied = await client.delete(
            f"/api/v1/series/{series['id']}", headers=author_headers
        )
        assert denied.status_code == 403
        ok = await client.delete(f"/api/v1/series/{series['id']}", headers=admin_headers)
        assert ok.status_code == 200


class TestSeriesArticleIntegration:
    async def test_article_attaches_series(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        created = await client.post(
            "/api/v1/articles",
            json=make_article_payload(series_id=series["id"], series_order=2),
            headers=author_headers,
        )
        assert created.status_code == 201
        body = created.json()
        assert body["series"]["id"] == series["id"]
        assert body["series_order"] == 2

    async def test_invalid_series_id_rejected(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/articles",
            json=make_article_payload(series_id=999999),
            headers=author_headers,
        )
        assert response.status_code == 400

    async def test_detail_returns_series_neighbors(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """同系列内按 series_order 出上下篇；全局 prev/next 仍按时间排。"""
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        first = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"], series_order=1),
                headers=author_headers,
            )
        ).json()
        middle = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"], series_order=2),
                headers=author_headers,
            )
        ).json()
        last = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"], series_order=3),
                headers=author_headers,
            )
        ).json()

        detail_middle = await client.get(f"/api/v1/articles/{middle['id']}")
        assert detail_middle.status_code == 200
        body = detail_middle.json()
        assert body["series_prev"]["id"] == first["id"]
        assert body["series_next"]["id"] == last["id"]

        detail_first = (await client.get(f"/api/v1/articles/{first['id']}")).json()
        assert detail_first["series_prev"] is None
        assert detail_first["series_next"]["id"] == middle["id"]

    async def test_update_moves_article_out_of_series(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        article = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"]),
                headers=author_headers,
            )
        ).json()
        updated = await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"series_id": None},
            headers=author_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["series"] is None
        assert updated.json()["series_prev"] is None
        assert updated.json()["series_next"] is None

    async def test_delete_series_keeps_articles(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        """删系列：文章不删，变回普通文章（内容优先原则）。"""
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        article = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"]),
                headers=author_headers,
            )
        ).json()
        assert (
            await client.delete(f"/api/v1/series/{series['id']}", headers=admin_headers)
        ).status_code == 200
        detail = await client.get(f"/api/v1/articles/{article['id']}")
        assert detail.status_code == 200
        assert detail.json()["series"] is None


class TestSeriesListing:
    async def test_counts_include_only_published(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """系列计数必须与列表页口径一致（只算已发布）。"""
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(series_id=series["id"]),
            headers=author_headers,
        )
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(series_id=series["id"], status="draft"),
            headers=author_headers,
        )
        listed = (await client.get("/api/v1/series")).json()
        entry = next(item for item in listed if item["id"] == series["id"])
        assert entry["article_count"] == 1

    async def test_series_detail_lists_articles_in_order(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        series = (
            await client.post("/api/v1/series", json=make_series_payload(), headers=author_headers)
        ).json()
        first = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"], series_order=5),
                headers=author_headers,
            )
        ).json()
        second = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(series_id=series["id"], series_order=1),
                headers=author_headers,
            )
        ).json()
        detail = await client.get(f"/api/v1/series/{series['slug']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["series"]["article_count"] == 2
        # 按 series_order 升序：second(1) 在 first(5) 前面
        assert [item["id"] for item in body["articles"]["items"]] == [second["id"], first["id"]]
