"""评论测试：审核流、两级回复、越权防护。"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload


def _comment(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "author_name": "路人甲",
        "content": "写得不错，学到了。",
    }
    payload.update(overrides)
    return payload


class TestCreate:
    async def test_guest_comment_needs_approval_by_default(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        """默认先审后发——个人博客最大的运营成本是清垃圾评论。"""
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}", json=_comment()
        )
        assert response.status_code == 201
        assert response.json()["is_approved"] is False

    async def test_guest_must_provide_name(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"content": "匿名说点什么"},
        )
        assert response.status_code == 400

    async def test_logged_in_user_name_is_filled_automatically(
        self, client: AsyncClient, published_article: dict, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"content": "登录用户的评论"},
            headers=author_headers,
        )
        assert response.status_code == 201
        assert response.json()["author_name"] == "写手"

    async def test_article_author_reply_is_auto_approved(
        self, client: AsyncClient, published_article: dict, author_headers: dict[str, str]
    ) -> None:
        """站长/作者回复还走审核，就变成"我评论了自己看不见"的荒诞剧。"""
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"content": "作者亲自回复"},
            headers=author_headers,
        )
        assert response.status_code == 201
        assert response.json()["is_approved"] is True
        assert response.json()["is_admin_reply"] is True

    async def test_comment_on_missing_article_404(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/comments/article/999999", json=_comment())
        assert response.status_code == 404

    async def test_comment_on_draft_404(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        draft = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(status="draft"),
                headers=author_headers,
            )
        ).json()
        response = await client.post(f"/api/v1/comments/article/{draft['id']}", json=_comment())
        assert response.status_code == 404

    async def test_closed_comment_article_rejects(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        article = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(allow_comment=False),
                headers=author_headers,
            )
        ).json()
        response = await client.post(f"/api/v1/comments/article/{article['id']}", json=_comment())
        assert response.status_code == 400

    async def test_empty_content_rejected(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"author_name": "甲", "content": "   "},
        )
        assert response.status_code == 422

    async def test_too_long_content_rejected(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_comment(content="字" * 3000),
        )
        assert response.status_code == 422

    async def test_guest_comment_disabled_by_admin(
        self, client: AsyncClient, admin_headers: dict[str, str], published_article: dict
    ) -> None:
        await client.patch(
            "/api/v1/site/profile", json={"allow_guest_comment": False}, headers=admin_headers
        )
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}", json=_comment()
        )
        assert response.status_code == 403


class TestReplies:
    async def test_reply_is_nested_under_root(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        root = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}", json=_comment()
            )
        ).json()
        await client.patch(
            f"/api/v1/comments/{root['id']}", json={"is_approved": True}, headers=admin_headers
        )
        reply = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}",
                json=_comment(author_name="乙", content="我也这么觉得", parent_id=root["id"]),
            )
        ).json()

        # 回复同样要审核：没过审之前，访客看到的根评论下就是空的
        assert (await client.get(f"/api/v1/comments/article/{published_article['id']}")).json()[0][
            "replies"
        ] == []

        await client.patch(
            f"/api/v1/comments/{reply['id']}", json={"is_approved": True}, headers=admin_headers
        )
        tree = (await client.get(f"/api/v1/comments/article/{published_article['id']}")).json()
        assert len(tree) == 1
        assert tree[0]["id"] == root["id"]
        assert len(tree[0]["replies"]) == 1
        assert tree[0]["replies"][0]["author_name"] == "乙"

    async def test_reply_stays_flat_two_levels(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """两级封顶：回复的回复仍然挂在根评论下，不会无限嵌套拖垮前端渲染。"""
        root = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}", json=_comment()
            )
        ).json()
        reply = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}",
                json=_comment(parent_id=root["id"], content="第一层回复"),
            )
        ).json()
        # 对回复再回复，parent 仍指向 root
        await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_comment(parent_id=root["id"], content="第二层回复"),
        )
        for item in (root, reply):
            await client.patch(
                f"/api/v1/comments/{item['id']}",
                json={"is_approved": True},
                headers=admin_headers,
            )
        await client.patch(
            f"/api/v1/comments/{root['id']}", json={"is_approved": True}, headers=admin_headers
        )

        tree = (await client.get(f"/api/v1/comments/article/{published_article['id']}")).json()
        assert len(tree) == 1
        assert all(item["parent_id"] == root["id"] for item in tree[0]["replies"])

    async def test_reply_to_comment_of_another_article_rejected(
        self,
        client: AsyncClient,
        author_headers: dict[str, str],
        published_article: dict,
    ) -> None:
        """不校验的话，可以把评论挂到任意文章下，污染别人的评论区。"""
        other = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=author_headers
            )
        ).json()
        foreign_root = (
            await client.post(
                f"/api/v1/comments/article/{other['id']}",
                json=_comment(content="另一篇文章的评论"),
            )
        ).json()

        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_comment(parent_id=foreign_root["id"]),
        )
        assert response.status_code == 400

    async def test_missing_parent_rejected(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_comment(parent_id=999999),
        )
        assert response.status_code == 400


class TestVisibility:
    async def test_guest_sees_only_approved(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        pending = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}",
                json=_comment(content="待审核的"),
            )
        ).json()
        approved = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}",
                json=_comment(content="已通过的"),
            )
        ).json()
        await client.patch(
            f"/api/v1/comments/{approved['id']}",
            json={"is_approved": True},
            headers=admin_headers,
        )

        guest_view = (
            await client.get(f"/api/v1/comments/article/{published_article['id']}")
        ).json()
        assert [item["id"] for item in guest_view] == [approved["id"]]

        # 作者自己的文章里能看到待审核评论，否则回复完会以为丢了
        staff_view = (
            await client.get(
                f"/api/v1/comments/article/{published_article['id']}", headers=admin_headers
            )
        ).json()
        assert {item["id"] for item in staff_view} == {pending["id"], approved["id"]}


class TestModeration:
    async def test_moderation_list_requires_author(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        assert (await client.get("/api/v1/comments")).status_code == 401

    async def test_filter_by_approved(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        await client.post(f"/api/v1/comments/article/{published_article['id']}", json=_comment())
        pending = (
            await client.get("/api/v1/comments?approved=false", headers=admin_headers)
        ).json()
        assert pending["total"] == 1
        assert pending["items"][0]["is_approved"] is False

    async def test_approve_then_revoke(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        comment = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}", json=_comment()
            )
        ).json()
        approved = await client.patch(
            f"/api/v1/comments/{comment['id']}",
            json={"is_approved": True},
            headers=admin_headers,
        )
        assert approved.json()["is_approved"] is True

        revoked = await client.patch(
            f"/api/v1/comments/{comment['id']}",
            json={"is_approved": False},
            headers=admin_headers,
        )
        assert revoked.json()["is_approved"] is False

    async def test_delete_root_removes_replies(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        """依赖数据库 CASCADE：删父评论必须带走子回复，不能留孤儿。"""
        root = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}", json=_comment()
            )
        ).json()
        await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_comment(parent_id=root["id"], content="子回复"),
        )
        await client.patch(
            f"/api/v1/comments/{root['id']}", json={"is_approved": True}, headers=admin_headers
        )

        assert (
            await client.delete(f"/api/v1/comments/{root['id']}", headers=admin_headers)
        ).status_code == 200
        assert (await client.get("/api/v1/comments", headers=admin_headers)).json()["total"] == 0

    async def test_author_cannot_delete_comment_on_others_article(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author_headers: dict[str, str],
    ) -> None:
        admin_article = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=admin_headers
            )
        ).json()
        comment = (
            await client.post(f"/api/v1/comments/article/{admin_article['id']}", json=_comment())
        ).json()
        response = await client.delete(f"/api/v1/comments/{comment['id']}", headers=author_headers)
        assert response.status_code == 403

    async def test_comment_count_reflected_on_article(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
    ) -> None:
        comment = (
            await client.post(
                f"/api/v1/comments/article/{published_article['id']}", json=_comment()
            )
        ).json()
        listing = (await client.get("/api/v1/articles")).json()
        assert listing["items"][0]["comment_count"] == 0

        await client.patch(
            f"/api/v1/comments/{comment['id']}", json={"is_approved": True}, headers=admin_headers
        )
        listing = (await client.get("/api/v1/articles")).json()
        assert listing["items"][0]["comment_count"] == 1
