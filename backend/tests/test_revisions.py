"""文章版本历史。

这是项目里**唯一一个真正不可替代**的功能：其它问题都能用别的方式凑合，
只有「误删了一段、改完发现还是上一版好」是没法补救的。

用例重点在**什么时候不该产生版本**——快照太密会让版本列表变成噪音，
真出事时反而找不到想看的那一版。
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload


async def _create(client: AsyncClient, headers: dict[str, str], **overrides: object) -> dict:
    payload = make_article_payload(**{"status": "published", **overrides})
    response = await client.post("/api/v1/articles", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _list_revisions(client: AsyncClient, headers: dict[str, str], article_id: int) -> list:
    response = await client.get(f"/api/v1/articles/{article_id}/revisions", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestSnapshotTrigger:
    async def test_content_change_creates_snapshot_of_old_value(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """存的是**改动前**的样子，这样「恢复到这一版」语义才直观。"""
        article = await _create(client, author_headers, title="原标题", content_md="原正文")

        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"content_md": "改过的正文"},
            headers=author_headers,
        )

        versions = await _list_revisions(client, author_headers, article["id"])
        assert len(versions) == 1
        assert versions[0]["title"] == "原标题"

        detail = (
            await client.get(
                f"/api/v1/articles/{article['id']}/revisions/{versions[0]['id']}",
                headers=author_headers,
            )
        ).json()
        assert detail["content_md"] == "原正文", "快照存的应当是改动前的正文"

    async def test_metadata_change_does_not_snapshot(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """改「是否置顶」不该产生版本——否则列表会被噪音淹没。"""
        article = await _create(client, author_headers, title="标题", content_md="正文")

        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"is_top": True, "allow_comment": False},
            headers=author_headers,
        )
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"tags": ["新标签"]},
            headers=author_headers,
        )

        assert await _list_revisions(client, author_headers, article["id"]) == []

    async def test_identical_content_does_not_snapshot(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """编辑器整段重发（内容其实没变）时不该产生版本。

        判定必须基于「库里当前值 vs 将要写入的值」，而不是「请求里有没有带
        这个字段」——按后者写的话，每次点保存都会多出一版完全相同的历史。
        """
        article = await _create(client, author_headers, title="标题", content_md="正文")

        for _ in range(3):
            await client.patch(
                f"/api/v1/articles/{article['id']}",
                json={"content_md": "正文", "title": "标题"},
                headers=author_headers,
            )

        assert await _list_revisions(client, author_headers, article["id"]) == []

    async def test_title_only_change_snapshots(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """只改标题也算内容变化。"""
        article = await _create(client, author_headers, title="旧标题", content_md="正文")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"title": "新标题"},
            headers=author_headers,
        )
        versions = await _list_revisions(client, author_headers, article["id"])
        assert len(versions) == 1
        assert versions[0]["title"] == "旧标题"


class TestListing:
    async def test_newest_first(self, client: AsyncClient, author_headers: dict[str, str]) -> None:
        article = await _create(client, author_headers, title="v1", content_md="正文1")
        await client.patch(
            f"/api/v1/articles/{article['id']}", json={"title": "v2"}, headers=author_headers
        )
        await client.patch(
            f"/api/v1/articles/{article['id']}", json={"title": "v3"}, headers=author_headers
        )

        versions = await _list_revisions(client, author_headers, article["id"])
        assert [v["title"] for v in versions] == ["v2", "v1"]

    async def test_list_omits_content(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """列表不带正文：一屏几十条、每条几 KB，带上会让响应轻松上兆。"""
        article = await _create(client, author_headers, title="v1", content_md="正文1")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"content_md": "正文2"},
            headers=author_headers,
        )

        versions = await _list_revisions(client, author_headers, article["id"])
        assert versions[0]["content_md"] is None
        # 但长度信息要在，列表里靠它显示「这一版多大」
        assert versions[0]["content_length"] == len("正文1")

    async def test_recorded_author(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """记下是谁改的——多作者协作时这是追责的唯一线索。"""
        article = await _create(client, author_headers, title="v1", content_md="x")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"content_md": "y"},
            headers=author_headers,
        )
        versions = await _list_revisions(client, author_headers, article["id"])
        assert versions[0]["author"] is not None
        assert versions[0]["author"]["username"]


class TestPermissions:
    async def test_only_author_or_admin_can_read(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """别人的文章版本历史不可读。

        历史版本可能包含作者删掉的内容，属于草稿性质，不能跟着
        「文章已发布」一起放行——所以这里的口径比文章详情更严。
        """
        article = await _create(client, admin_headers, title="站长的文章", content_md="x")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"content_md": "y"},
            headers=admin_headers,
        )

        response = await client.get(
            f"/api/v1/articles/{article['id']}/revisions", headers=author_headers
        )
        assert response.status_code == 403

    async def test_guest_cannot_read(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        article = await _create(client, author_headers, title="x", content_md="x")
        assert (await client.get(f"/api/v1/articles/{article['id']}/revisions")).status_code == 401

    async def test_revision_of_other_article_is_404(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """换一个 revision_id 不能读到别人文章的历史（IDOR）。

        只按 revision_id 查询、不校验归属的话，自增 id 枚举一遍就能把
        全站文章的正文历史都读出来。
        """
        first = await _create(client, author_headers, title="A", content_md="a1")
        await client.patch(
            f"/api/v1/articles/{first['id']}", json={"content_md": "a2"}, headers=author_headers
        )
        second = await _create(client, author_headers, title="B", content_md="b1")

        stolen = (await _list_revisions(client, author_headers, first["id"]))[0]

        response = await client.get(
            f"/api/v1/articles/{second['id']}/revisions/{stolen['id']}", headers=author_headers
        )
        assert response.status_code == 404


class TestRestore:
    async def test_restore_writes_content_back(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        article = await _create(client, author_headers, title="原标题", content_md="原正文")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"title": "改坏了", "content_md": "改坏的正文"},
            headers=author_headers,
        )

        versions = await _list_revisions(client, author_headers, article["id"])
        response = await client.post(
            f"/api/v1/articles/{article['id']}/revisions/{versions[0]['id']}/restore",
            headers=author_headers,
        )
        assert response.status_code == 200, response.text
        restored = response.json()
        assert restored["title"] == "原标题"
        assert restored["content_md"] == "原正文"

    async def test_restore_creates_a_way_back(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """恢复前先给「当前内容」留一版，这样恢复错了还能退回来。

        「恢复错了」恰恰是这个功能最可能被触发的场景，不给退路等于把
        用户从「改错了」推进「改错了且回不去」。
        """
        article = await _create(client, author_headers, title="v1", content_md="正文1")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"title": "v2", "content_md": "正文2"},
            headers=author_headers,
        )

        versions = await _list_revisions(client, author_headers, article["id"])
        first_version_id = versions[0]["id"]
        await client.post(
            f"/api/v1/articles/{article['id']}/revisions/{first_version_id}/restore",
            headers=author_headers,
        )

        after = await _list_revisions(client, author_headers, article["id"])
        assert len(after) == 2, "恢复本身也应当留下一条记录"
        # 最新一条是「恢复前的样子」，并记下它是因为要恢复到哪一版
        assert after[0]["reason"] == "restore"
        assert after[0]["restored_from_id"] == first_version_id

        # 再恢复回这一版 = 撤销刚才的恢复
        await client.post(
            f"/api/v1/articles/{article['id']}/revisions/{after[0]['id']}/restore",
            headers=author_headers,
        )
        detail = (await client.get(f"/api/v1/articles/{article['slug']}")).json()
        assert detail["title"] == "v2"
        assert detail["content_md"] == "正文2"

    async def test_restore_out_of_scope_article_denied(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        article = await _create(client, admin_headers, title="站长的", content_md="x")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"content_md": "y"},
            headers=admin_headers,
        )
        versions = await _list_revisions(client, admin_headers, article["id"])

        response = await client.post(
            f"/api/v1/articles/{article['id']}/revisions/{versions[0]['id']}/restore",
            headers=author_headers,
        )
        assert response.status_code == 403


class TestRetention:
    async def test_old_versions_are_trimmed(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch
    ) -> None:
        """超过上限时从最旧的开始删。

        不设上限的话，一篇长期维护的文章会无限累积；而「三个月前那一版」
        几乎不会被用到。把上限压到 3 来验证裁剪确实发生。
        """
        from app.services import revision_service

        monkeypatch.setattr(revision_service, "MAX_REVISIONS_PER_ARTICLE", 3)

        article = await _create(client, author_headers, title="v0", content_md="正文0")
        for index in range(1, 7):
            await client.patch(
                f"/api/v1/articles/{article['id']}",
                json={"title": f"v{index}", "content_md": f"正文{index}"},
                headers=author_headers,
            )

        versions = await _list_revisions(client, author_headers, article["id"])
        assert len(versions) == 3, f"应当只保留 3 版，实际 {len(versions)}"
        # 留下的是最新的三版（它们记录的是各自改动前的内容）
        assert [v["title"] for v in versions] == ["v5", "v4", "v3"]


class TestCascade:
    async def test_deleting_article_removes_its_revisions(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """文章删了，版本一起走——孤立版本无法恢复，只是垃圾。"""
        from sqlalchemy import func, select

        from app.db.session import async_session_factory
        from app.models import ArticleRevision

        article = await _create(client, author_headers, title="v1", content_md="x")
        await client.patch(
            f"/api/v1/articles/{article['id']}",
            json={"content_md": "y"},
            headers=author_headers,
        )
        assert len(await _list_revisions(client, author_headers, article["id"])) == 1

        assert (
            await client.delete(f"/api/v1/articles/{article['id']}", headers=author_headers)
        ).status_code == 204

        async with async_session_factory() as session:
            left = await session.execute(select(func.count()).select_from(ArticleRevision))
            assert int(left.scalar_one()) == 0
