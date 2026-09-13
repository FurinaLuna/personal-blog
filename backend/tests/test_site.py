"""站点配置、统计、健康检查，以及跨模块的级联行为。"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload


class TestHealth:
    async def test_health_endpoint(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_root_endpoint(self, client: AsyncClient) -> None:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json()["name"]

    async def test_openapi_schema_is_generated(self, client: AsyncClient) -> None:
        """文档能生成说明所有路由的 schema 都是合法的，是很好的整体自检。"""
        response = await client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        for expected in (
            "/api/v1/articles",
            "/api/v1/articles/{slug_or_id}",
            "/api/v1/auth/login",
            "/api/v1/categories",
            "/api/v1/tags",
            "/api/v1/attachments/upload",
            "/api/v1/comments/article/{article_id}",
            "/api/v1/site/profile",
        ):
            assert expected in paths, f"缺少路由 {expected}"


class TestProfile:
    async def test_profile_is_public(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/site/profile")
        assert response.status_code == 200
        assert response.json()["owner_name"]

    async def test_admin_updates_profile(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile",
            json={
                "owner_name": "新的站长名",
                "headline": "新的签名",
                "skills": ["Python", "Vue"],
                "social_links": [{"label": "GitHub", "url": "https://github.com/x"}],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["owner_name"] == "新的站长名"
        assert body["skills"] == ["Python", "Vue"]
        assert body["social_links"][0]["label"] == "GitHub"

    async def test_boolean_switch_can_be_set_to_false(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """布尔开关传 False 必须生效，不能被"跳过 None"式的过滤逻辑吃掉。"""
        response = await client.patch(
            "/api/v1/site/profile",
            json={"comment_need_approval": False, "allow_guest_comment": False},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["comment_need_approval"] is False
        assert response.json()["allow_guest_comment"] is False

    async def test_partial_update_keeps_other_fields(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        original = (await client.get("/api/v1/site/profile")).json()
        await client.patch("/api/v1/site/profile", json={"location": "深圳"}, headers=admin_headers)
        updated = (await client.get("/api/v1/site/profile")).json()
        assert updated["location"] == "深圳"
        assert updated["about_md"] == original["about_md"]

    async def test_nullable_fields_can_be_cleared(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """可空文本/JSON 字段允许显式传 null 清空，而不是被当成「不修改」吃掉。"""
        await client.patch(
            "/api/v1/site/profile",
            json={"headline": "一句话介绍", "email": "me@example.com", "icp": "京ICP备00000000号"},
            headers=admin_headers,
        )
        response = await client.patch(
            "/api/v1/site/profile",
            json={"headline": None, "email": None, "icp": None},
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["headline"] is None
        assert body["email"] is None
        assert body["icp"] is None

    async def test_boolean_null_is_treated_as_noop(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """布尔开关显式传 null 视为「不修改」——布尔列非空，写 None 只会换来 500。"""
        before = (await client.get("/api/v1/site/profile")).json()["allow_guest_comment"]
        response = await client.patch(
            "/api/v1/site/profile", json={"allow_guest_comment": None}, headers=admin_headers
        )
        assert response.status_code == 200
        assert response.json()["allow_guest_comment"] == before

    async def test_author_cannot_update_profile(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile", json={"owner_name": "篡改"}, headers=author_headers
        )
        assert response.status_code == 403

    async def test_invalid_email_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile", json={"email": "not-an-email"}, headers=admin_headers
        )
        assert response.status_code == 422


class TestStats:
    async def test_stats_require_admin(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        assert (await client.get("/api/v1/site/stats", headers=author_headers)).status_code == 403
        assert (await client.get("/api/v1/site/stats")).status_code == 401

    async def test_stats_reflect_content(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        await client.post("/api/v1/articles", json=make_article_payload(), headers=author_headers)
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(status="draft"),
            headers=author_headers,
        )
        await client.post("/api/v1/categories", json={"name": "统计分类"}, headers=author_headers)

        stats = (await client.get("/api/v1/site/stats", headers=admin_headers)).json()
        assert stats["article_total"] == 2
        assert stats["published_total"] == 1
        assert stats["draft_total"] == 1
        assert stats["category_total"] == 1
        assert stats["tag_total"] >= 1
        assert stats["total_views"] >= 0
        assert stats["latest_published_at"] is not None

    async def test_pending_comment_counter(
        self, client: AsyncClient, published_article: dict, admin_headers: dict[str, str]
    ) -> None:
        await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"author_name": "甲", "content": "待审评论"},
        )
        stats = (await client.get("/api/v1/site/stats", headers=admin_headers)).json()
        assert stats["pending_comment_total"] == 1
        assert stats["comment_total"] == 1


class TestCascade:
    """跨模块级联：这些行为靠数据库外键实现，最容易因为"没打开 FK 约束"而静默失效。"""

    async def test_deleting_article_removes_its_comments(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        article = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=author_headers
            )
        ).json()
        await client.post(
            f"/api/v1/comments/article/{article['id']}",
            json={"author_name": "甲", "content": "会被连带删除"},
        )

        await client.delete(f"/api/v1/articles/{article['id']}", headers=author_headers)
        comments = (await client.get("/api/v1/comments", headers=admin_headers)).json()
        assert comments["total"] == 0

    async def test_deleting_user_removes_their_articles(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author: dict[str, object],
    ) -> None:
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(),
            headers=author["headers"],  # type: ignore[arg-type]
        )
        before = (await client.get("/api/v1/articles/manage/list", headers=admin_headers)).json()
        assert before["total"] == 1

        await client.delete(f"/api/v1/auth/users/{author['id']}", headers=admin_headers)
        after = (await client.get("/api/v1/articles/manage/list", headers=admin_headers)).json()
        assert after["total"] == 0
