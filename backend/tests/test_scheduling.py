"""定时发布（排期）。

设计取舍先写清楚：**没有新增 `scheduled` 状态**，而是「`status=published`
+ 一个未来的 `published_at`」。这样可见性判定只需要在既有的
``is_publicly_visible`` 一处加时间条件，不必改枚举、不必写迁移，
也不会到处都要回答「scheduled 到底算不算已发布」。

到点自动可见，不需要任何定时任务去改状态——这是选这个模型最大的好处。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from tests.factories import make_article_payload


def _at(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


class TestScheduledPublishing:
    async def test_future_article_hidden_from_public_list(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """排在未来时刻的文章不该出现在前台列表里。"""
        before = (await client.get("/api/v1/articles?page_size=50")).json()["total"]

        created = await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="未来的文章", status="published", published_at=_at(timedelta(days=3))
            ),
            headers=author_headers,
        )
        assert created.status_code == 201, created.text

        after = (await client.get("/api/v1/articles?page_size=50")).json()["total"]
        assert after == before, "排期文章提前出现在前台列表里了"

    async def test_future_article_detail_404_for_guest(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """详情页对访客返回 404（而不是 403）——403 等于承认这里有一篇文章。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="未来的文章", status="published", published_at=_at(timedelta(days=3))
                ),
                headers=author_headers,
            )
        ).json()

        assert (await client.get(f"/api/v1/articles/{created['slug']}")).status_code == 404
        # 作者自己要能预览，否则没法在发布前检查排版
        owned = await client.get(f"/api/v1/articles/{created['slug']}", headers=author_headers)
        assert owned.status_code == 200

    async def test_already_due_article_is_visible(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """时间已经过去的排期文章正常可见——「未来」才是隐藏条件。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="已到点的文章", status="published", published_at=_at(-timedelta(hours=1))
                ),
                headers=author_headers,
            )
        ).json()

        detail = await client.get(f"/api/v1/articles/{created['slug']}")
        assert detail.status_code == 200
        assert detail.json()["is_scheduled"] is False

    async def test_is_scheduled_flag(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """响应里要能区分「已发布」和「已排期但没到点」。

        后台界面上两者都是 status=published，没有这个派生字段的话会显示成
        「已发布」而前台又搜不到，作者会以为是自己搞错了。
        """
        scheduled = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="排期", status="published", published_at=_at(timedelta(days=1))
                ),
                headers=author_headers,
            )
        ).json()
        assert scheduled["is_scheduled"] is True

        immediate = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(title="立即", status="published"),
                headers=author_headers,
            )
        ).json()
        assert immediate["is_scheduled"] is False

        draft = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(title="草稿", status="draft"),
                headers=author_headers,
            )
        ).json()
        assert draft["is_scheduled"] is False

    async def test_publishing_without_time_is_immediate(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """不传 published_at 时保持原行为：立刻发布，发布时间是现在。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(title="立即发布", status="published"),
                headers=author_headers,
            )
        ).json()

        assert created["published_at"] is not None
        assert created["is_scheduled"] is False
        assert (await client.get(f"/api/v1/articles/{created['slug']}")).status_code == 200

    async def test_rescheduling_into_future_hides_it_again(
        self, client: AsyncClient, published_article: dict, author_headers: dict[str, str]
    ) -> None:
        """已发布的文章改期到未来 → 重新从前台消失。"""
        assert (
            await client.get(f"/api/v1/articles/{published_article['slug']}")
        ).status_code == 200

        updated = await client.patch(
            f"/api/v1/articles/{published_article['id']}",
            json={"published_at": _at(timedelta(days=7))},
            headers=author_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["is_scheduled"] is True

        assert (
            await client.get(f"/api/v1/articles/{published_article['slug']}")
        ).status_code == 404

    async def test_rescheduling_into_past_makes_it_visible_again(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """反过来也要成立：把排期时间改到过去，立刻可见。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="先排期", status="published", published_at=_at(timedelta(days=7))
                ),
                headers=author_headers,
            )
        ).json()
        assert (await client.get(f"/api/v1/articles/{created['slug']}")).status_code == 404

        await client.patch(
            f"/api/v1/articles/{created['id']}",
            json={"published_at": _at(-timedelta(minutes=1))},
            headers=author_headers,
        )
        assert (await client.get(f"/api/v1/articles/{created['slug']}")).status_code == 200

    async def test_explicit_time_is_not_overwritten_by_publish(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """显式传的发布时间不能被「首次发布补当前时间」这一步覆盖掉。

        这是实现里最容易写错的地方：两个分支如果写成两个独立 if，
        后一个会把前一个的结果无声地改成「现在」，排期功能直接失效。
        """
        target = _at(timedelta(days=5))
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="排期覆盖检查", status="published", published_at=target
                ),
                headers=author_headers,
            )
        ).json()

        stored = datetime.fromisoformat(created["published_at"])
        expected = datetime.fromisoformat(target)
        assert abs((stored - expected).total_seconds()) < 2, "显式时间被改成了别的值"

    async def test_naive_datetime_is_interpreted_as_utc(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """不带时区的时间按 UTC 解释。

        不归一化的话，「谁先谁后」的比较结果会取决于服务器时区，
        是最难查的一类 bug。
        """
        naive = (datetime.now(UTC) + timedelta(days=2)).replace(tzinfo=None).isoformat()
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(title="裸时间", status="published", published_at=naive),
                headers=author_headers,
            )
        ).json()

        assert created["is_scheduled"] is True

    async def test_comments_on_scheduled_article_are_hidden(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """排期文章的评论区不能匿名读到——与详情页同一个口径。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="排期评论", status="published", published_at=_at(timedelta(days=3))
                ),
                headers=author_headers,
            )
        ).json()

        assert (await client.get(f"/api/v1/comments/article/{created['id']}")).status_code == 404
        # 作者自己可以看
        owned = await client.get(
            f"/api/v1/comments/article/{created['id']}", headers=author_headers
        )
        assert owned.status_code == 200

    async def test_cannot_comment_on_scheduled_article(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """也不能往上发评论，否则会出现「文章 404 但评论能提交」的矛盾状态。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="排期禁评", status="published", published_at=_at(timedelta(days=3))
                ),
                headers=author_headers,
            )
        ).json()

        response = await client.post(
            f"/api/v1/comments/article/{created['id']}",
            json={"author_name": "路人", "content": "抢先评论"},
        )
        assert response.status_code == 404

    async def test_draft_with_future_time_is_still_draft(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """草稿即使带了发布时间也还是草稿——状态才是主导，时间只是补充条件。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="草稿带时间", status="draft", published_at=_at(-timedelta(days=1))
                ),
                headers=author_headers,
            )
        ).json()

        assert created["is_scheduled"] is False
        assert (await client.get(f"/api/v1/articles/{created['slug']}")).status_code == 404

    async def test_scheduled_article_stays_in_admin_list(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """后台列表必须仍然能看到它，否则作者根本找不到自己排的文章。"""
        created = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="后台可见的排期文",
                    status="published",
                    published_at=_at(timedelta(days=3)),
                ),
                headers=admin_headers,
            )
        ).json()

        # 用默认分页参数：page_size 有上限，传大了会拿到 422
        # （响应体里是 detail 而不是 items，断言会以 KeyError 的形式炸出来）
        listing = (await client.get("/api/v1/articles/manage/list", headers=admin_headers)).json()
        assert created["id"] in [item["id"] for item in listing["items"]]
