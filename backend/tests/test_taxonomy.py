"""分类与标签测试。"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload, unique_suffix


class TestCategories:
    async def test_create_generates_slug(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/categories",
            json={"name": f"技术笔记{unique_suffix()}"},
            headers=author_headers,
        )
        assert response.status_code == 201
        assert response.json()["slug"]

    async def test_duplicate_name_conflicts(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        name = f"重复分类{unique_suffix()}"
        assert (
            await client.post("/api/v1/categories", json={"name": name}, headers=author_headers)
        ).status_code == 201
        again = await client.post("/api/v1/categories", json={"name": name}, headers=author_headers)
        assert again.status_code == 409

    async def test_guest_cannot_create(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/categories", json={"name": "游客建的分类"})
        assert response.status_code == 401

    async def test_counts_include_only_published(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """分类计数必须与列表页口径一致，否则会出现"显示 2 篇点进去只有 1 篇"。"""
        category = (
            await client.post(
                "/api/v1/categories",
                json={"name": f"计数分类{unique_suffix()}"},
                headers=author_headers,
            )
        ).json()
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(category_id=category["id"]),
            headers=author_headers,
        )
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(category_id=category["id"], status="draft"),
            headers=author_headers,
        )

        listed = (await client.get("/api/v1/categories")).json()
        target = next(item for item in listed if item["id"] == category["id"])
        assert target["article_count"] == 1

    async def test_count_can_be_skipped(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        await client.post(
            "/api/v1/categories",
            json={"name": f"轻量分类{unique_suffix()}"},
            headers=author_headers,
        )
        listed = (await client.get("/api/v1/categories?with_counts=false")).json()
        assert all(item["article_count"] == 0 for item in listed)

    async def test_get_by_slug_and_id(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        category = (
            await client.post(
                "/api/v1/categories",
                json={"name": f"详情分类{unique_suffix()}"},
                headers=author_headers,
            )
        ).json()
        assert (await client.get(f"/api/v1/categories/{category['slug']}")).status_code == 200
        assert (await client.get(f"/api/v1/categories/{category['id']}")).status_code == 200

    async def test_missing_category_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/categories/nope")).status_code == 404

    async def test_update_category(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        category = (
            await client.post(
                "/api/v1/categories",
                json={"name": f"待改分类{unique_suffix()}", "description": "旧描述"},
                headers=author_headers,
            )
        ).json()
        response = await client.patch(
            f"/api/v1/categories/{category['id']}",
            json={"name": "改后的名字", "description": None},
            headers=author_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "改后的名字"
        assert response.json()["description"] is None

    async def test_delete_category_keeps_articles(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        """删分类不能删文章——内容永远比分类结构重要。删除分类是站长权限。"""
        category = (
            await client.post(
                "/api/v1/categories",
                json={"name": f"将删分类{unique_suffix()}"},
                headers=author_headers,
            )
        ).json()
        article = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(category_id=category["id"]),
                headers=author_headers,
            )
        ).json()

        assert (
            await client.delete(f"/api/v1/categories/{category['id']}", headers=admin_headers)
        ).status_code == 200

        detail = (await client.get(f"/api/v1/articles/{article['slug']}")).json()
        assert detail["id"] == article["id"]
        assert detail["category"] is None

    async def test_delete_requires_admin(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        category = (
            await client.post(
                "/api/v1/categories",
                json={"name": f"权限分类{unique_suffix()}"},
                headers=author_headers,
            )
        ).json()
        response = await client.delete(
            f"/api/v1/categories/{category['id']}", headers=author_headers
        )
        assert response.status_code == 403


class TestTags:
    async def test_create_tag(self, client: AsyncClient, author_headers: dict[str, str]) -> None:
        response = await client.post(
            "/api/v1/tags", json={"name": f"标签{unique_suffix()}"}, headers=author_headers
        )
        assert response.status_code == 201
        assert response.json()["slug"]

    async def test_duplicate_tag_conflicts(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        name = f"重复标签{unique_suffix()}"
        await client.post("/api/v1/tags", json={"name": name}, headers=author_headers)
        again = await client.post("/api/v1/tags", json={"name": name}, headers=author_headers)
        assert again.status_code == 409

    async def test_tag_cloud_sorted_by_count(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        hot = f"热门{unique_suffix()}"
        cold = f"冷门{unique_suffix()}"
        for tag in (hot, hot, cold):
            await client.post(
                "/api/v1/articles", json=make_article_payload(tags=[tag]), headers=author_headers
            )

        tags = (await client.get("/api/v1/tags?limit=5")).json()
        assert tags[0]["name"] == hot
        assert tags[0]["article_count"] == 2

    async def test_min_count_filter(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(tags=[f"单次标签{unique_suffix()}"]),
            headers=author_headers,
        )
        tags = (await client.get("/api/v1/tags?min_count=2")).json()
        assert tags == []

    async def test_min_count_applies_before_limit(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """min_count 与 limit 同用：返回「计数达标的前 N 个」，达标数不足 N 才会短。"""
        hot = f"达标{unique_suffix()}"
        for _ in range(2):
            await client.post(
                "/api/v1/articles", json=make_article_payload(tags=[hot]), headers=author_headers
            )
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(tags=[f"空挂{unique_suffix()}"]),
            headers=author_headers,
        )

        tags = (await client.get("/api/v1/tags?limit=2&min_count=2")).json()
        # 截断发生在过滤之后：limit 的名额不该被空挂标签占掉
        assert len(tags) == 1
        assert tags[0]["name"] == hot
        assert tags[0]["article_count"] == 2

    async def test_update_tag(self, client: AsyncClient, author_headers: dict[str, str]) -> None:
        tag = (
            await client.post(
                "/api/v1/tags", json={"name": f"待改标签{unique_suffix()}"}, headers=author_headers
            )
        ).json()
        response = await client.patch(
            f"/api/v1/tags/{tag['id']}", json={"name": "改名后的标签"}, headers=author_headers
        )
        assert response.status_code == 200
        assert response.json()["name"] == "改名后的标签"

    async def test_delete_tag_keeps_article(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        name = f"将删标签{unique_suffix()}"
        article = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(tags=[name]), headers=author_headers
            )
        ).json()
        tag_id = article["tags"][0]["id"]

        assert (
            await client.delete(f"/api/v1/tags/{tag_id}", headers=author_headers)
        ).status_code == 200
        detail = (await client.get(f"/api/v1/articles/{article['slug']}")).json()
        assert detail["tags"] == []

    async def test_cleanup_orphan_tags(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        name = f"孤儿标签{unique_suffix()}"
        article = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(tags=[name]), headers=author_headers
            )
        ).json()
        # 把标签从文章上摘掉，它就变成了没有引用的孤儿
        await client.patch(
            f"/api/v1/articles/{article['id']}", json={"tags": []}, headers=author_headers
        )

        response = await client.post("/api/v1/tags/cleanup", headers=admin_headers)
        assert response.status_code == 200
        names = [item["name"] for item in (await client.get("/api/v1/tags")).json()]
        assert name not in names

    async def test_cleanup_requires_admin(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post("/api/v1/tags/cleanup", headers=author_headers)
        assert response.status_code == 403

    async def test_replacing_tags_is_idempotent(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        article = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=author_headers
            )
        ).json()
        new_tags = [f"A{unique_suffix()}", f"B{unique_suffix()}"]
        response = await client.patch(
            f"/api/v1/articles/{article['id']}", json={"tags": new_tags}, headers=author_headers
        )
        assert sorted(tag["name"] for tag in response.json()["tags"]) == sorted(new_tags)
