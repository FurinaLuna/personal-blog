"""留言板测试。

覆盖八块内容，每块都写清了它守的是哪条契约：

1. **公开读**：只返回已过审、``id DESC`` 排序、分页信封字段正确，
   响应里**没有**邮箱 / IP / UA（这既是隐私，也是它能进共享缓存的前提）；
2. **创建**：匿名可发（默认待审）、昵称 / 正文 / 邮箱 / 网址的校验口径、
   IP 与 UA 落库、站长发的直接过审、登录用户自动取昵称；
3. **限流**：第 6 次 429（与评论同一档），且被挡的请求不落库、与评论桶互不影响；
4. **后台**：匿名 401 / 作者 403 / 站长 200 且含邮箱，``?approved=`` 过滤、
   ``/manage`` 不进公开缓存；
5. **审核**：过审后出现在公开列表、撤回后消失、不存在 404、权限边界；
6. **回复**：写入后公开响应可见（含 ``replied_at``）、空字符串清除（三列一起清）、
   「不传字段」不等于「清空」；
7. **删除**：站长可删、删后公开列表没有、404 / 401；
8. **通知**：新留言提醒站长、新回复通知留言者（含退订链接）、清除回复不发信、
   改写已有回复不重复发信、SMTP 关闭时静默。

风格与断言密度对齐 ``tests/test_friend_links.py`` / ``tests/test_comments.py``。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.config import settings
from app.db.session import async_session_factory
from app.models import GuestbookMessage
from app.services import notification_service
from app.services.notification_service import drain_notifications
from app.utils.ratelimit import limiter
from app.utils.security import create_unsubscribe_token

PUBLIC_LIST = "/api/v1/guestbook"
MANAGED_LIST = "/api/v1/guestbook/manage"
ADMIN_EMAIL = "admin@example.com"
GUEST_EMAIL = "guest@example.com"

# 公开响应的字段集合是**冻结契约**（前端 types 逐字对应），多一个少一个都要在这里红
PUBLIC_FIELDS = {
    "id",
    "author_name",
    "author_site",
    "content",
    "is_approved",
    "reply_content",
    "replied_at",
    "created_at",
}


def make_payload(**overrides: object) -> dict[str, object]:
    """构造留言请求体。"""
    payload: dict[str, object] = {"author_name": "路人甲", "content": "路过留个言。"}
    payload.update(overrides)
    return payload


async def create_message(
    client: AsyncClient, headers: dict[str, str] | None = None, **overrides: object
) -> dict[str, object]:
    """发一条留言并返回响应体（要求 201，否则用例直接失败在真实原因上）。"""
    response = await client.post(PUBLIC_LIST, json=make_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def list_public(client: AsyncClient) -> dict[str, object]:
    response = await client.get(PUBLIC_LIST)
    assert response.status_code == 200, response.text
    return response.json()


async def list_manage(
    client: AsyncClient, headers: dict[str, str], **params: object
) -> dict[str, object]:
    response = await client.get(MANAGED_LIST, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def fetch_row(message_id: int) -> GuestbookMessage:
    """直连数据库读原始行。

    断言「IP / UA / replied_by_id 真的落库了」必须绕开 API——只信响应体的话，
    这些「不公开」的字段永远验证不到（它们本来就不在响应里）。
    """
    async with async_session_factory() as session:
        row = await session.get(GuestbookMessage, message_id)
        assert row is not None
        return row


async def count_messages() -> int:
    async with async_session_factory() as session:
        result = await session.execute(select(func.count()).select_from(GuestbookMessage))
        return int(result.scalar_one())


class TestPublicList:
    async def test_anonymous_can_read(self, client: AsyncClient) -> None:
        """公开接口不带任何凭证也必须能读（这是访客唯一能看到自己留言的路径）。"""
        response = await client.get(PUBLIC_LIST)

        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_only_approved_are_listed(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """未过审的留言对匿名访客不可见——先审后发是这个域的核心契约。"""
        pending = await create_message(client, content="待审的留言")
        visible = await create_message(client, admin_headers, content="站长自己发的")

        assert pending["is_approved"] is False
        ids = [item["id"] for item in (await list_public(client))["items"]]
        assert ids == [visible["id"]]

    async def test_order_is_newest_first(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``id DESC``：最新的留言在最前。"""
        first = await create_message(client, admin_headers, content="第一条")
        second = await create_message(client, admin_headers, content="第二条")
        third = await create_message(client, admin_headers, content="第三条")

        ids = [item["id"] for item in (await list_public(client))["items"]]
        assert ids == [third["id"], second["id"], first["id"]]

    async def test_pagination_envelope(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        for index in range(3):
            await create_message(client, admin_headers, content=f"第 {index} 条")

        first_page = (await client.get(PUBLIC_LIST, params={"page": 1, "page_size": 2})).json()
        assert first_page["total"] == 3
        assert first_page["page"] == 1
        assert first_page["page_size"] == 2
        assert first_page["pages"] == 2
        assert len(first_page["items"]) == 2

        second_page = (await client.get(PUBLIC_LIST, params={"page": 2, "page_size": 2})).json()
        assert len(second_page["items"]) == 1

    async def test_public_response_has_no_private_fields(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """邮箱 / IP / UA 一个都不能出现在公开响应里。

        两个理由，缺一不可：邮箱是留言者隐私；而公开响应会被
        ``PublicCacheMiddleware`` 打进**共享缓存**，任何随人变化的字段都会污染缓存
        （这也是 read schema 与 admin schema 分成两个模型的原因）。
        """
        created = await create_message(client, author_email=GUEST_EMAIL, author_site="example.com")
        # 过审之后才会出现在公开列表里（待审的本来就不该被读到）
        await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"is_approved": True}, headers=admin_headers
        )
        item = (await list_public(client))["items"][0]

        assert set(item) == PUBLIC_FIELDS
        assert set(created) == PUBLIC_FIELDS
        assert "author_email" not in item
        assert "ip_address" not in item
        assert "user_agent" not in item
        # 没回复时两个字段都在，值都为 null（前端只需要判断一种「还没回」）
        assert item["reply_content"] is None
        assert item["replied_at"] is None

    async def test_public_list_is_publicly_cached(self, client: AsyncClient) -> None:
        """公开列表要吃到 ETag / ``Vary: Authorization``（证明接进了缓存中间件）。"""
        from app.api.cache import PUBLIC_MAX_AGE

        response = await client.get(PUBLIC_LIST)

        assert response.headers["etag"].startswith('W/"')
        assert response.headers["cache-control"] == f"public, max-age={PUBLIC_MAX_AGE}"
        assert "Authorization" in response.headers.get("vary", "")


class TestCreate:
    async def test_anonymous_message_defaults_to_pending(self, client: AsyncClient) -> None:
        created = await create_message(client, content="匿名留言")

        assert created["is_approved"] is False
        assert (await list_public(client))["total"] == 0

    @pytest.mark.parametrize("name", [None, "", "   "])
    async def test_guest_must_provide_name(self, client: AsyncClient, name: str | None) -> None:
        """游客必须填昵称：留言板没有能把匿名者串起来的关系，一堆无名氏没法回。

        空串与纯空白在 schema 层就被归一成 ``None``，所以三种写法都是 400
        （``BadRequestError``），而不是 422。
        """
        payload = {"content": "匿名说点什么"}
        if name is not None:
            payload["author_name"] = name

        response = await client.post(PUBLIC_LIST, json=payload)

        assert response.status_code == 400
        assert response.json()["detail"] == "请填写昵称"

    async def test_logged_in_user_name_is_filled_automatically(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """登录用户不必重复填名字；作者角色**不**享受站长的免审例外。"""
        created = await create_message(client, author_headers, author_name=None)

        assert created["author_name"] == "写手"
        assert created["is_approved"] is False

    async def test_admin_message_is_auto_approved(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """站长发的直接过审——否则「站长发了一条自己都看不到」很荒谬。"""
        created = await create_message(client, admin_headers, content="站长先写一条欢迎留言")

        assert created["is_approved"] is True
        assert created["id"] in [item["id"] for item in (await list_public(client))["items"]]

    @pytest.mark.parametrize("content", ["", "   ", "\n\t "])
    async def test_blank_content_rejected(self, client: AsyncClient, content: str) -> None:
        response = await client.post(PUBLIC_LIST, json=make_payload(content=content))

        assert response.status_code == 422
        assert await count_messages() == 0

    async def test_missing_content_rejected(self, client: AsyncClient) -> None:
        response = await client.post(PUBLIC_LIST, json={"author_name": "甲"})

        assert response.status_code == 422

    async def test_too_long_content_rejected(self, client: AsyncClient) -> None:
        response = await client.post(PUBLIC_LIST, json=make_payload(content="字" * 2001))

        assert response.status_code == 422

    @pytest.mark.parametrize("bad_email", ["not-an-email", "no-at-sign.com", "a@b@c"])
    async def test_invalid_email_rejected(self, client: AsyncClient, bad_email: str) -> None:
        """邮箱格式错了就直接拒收，而不是静默丢掉——不然留言者永远等不到回复通知。"""
        response = await client.post(PUBLIC_LIST, json=make_payload(author_email=bad_email))

        assert response.status_code == 422

    async def test_bare_domain_site_is_normalized(self, client: AsyncClient) -> None:
        """没写协议的输入补 ``https://``（判定与补全都在 utils/url.py，与评论/友链同一套）。"""
        created = await create_message(client, author_site="example.com")

        assert created["author_site"] == "https://example.com"

    async def test_http_site_kept_as_is(self, client: AsyncClient) -> None:
        created = await create_message(client, author_site="http://blog.example.com/about")

        assert created["author_site"] == "http://blog.example.com/about"

    async def test_blank_site_becomes_null(self, client: AsyncClient) -> None:
        """留空是「没填」，不是「填错了」。"""
        created = await create_message(client, author_site="   ")

        assert created["author_site"] is None

    @pytest.mark.parametrize(
        "evil",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "java\nscript:alert(1)",  # 浏览器会把换行剥掉，等价于 javascript:
            "//evil.com",  # 协议相对地址：补上 https 之后 netloc 为空，拒绝
        ],
    )
    async def test_dangerous_site_rejected(self, client: AsyncClient, evil: str) -> None:
        """伪协议一律 422。

        前端把这个值直接绑到 ``:href`` 上，而 Vue **不清洗**动态 href——
        放行一个就等于给出一条存储型 XSS 路径。拒绝而不是静默丢弃：
        库里的行数必须没变，扫的人也该在日志里看得见。
        """
        response = await client.post(PUBLIC_LIST, json=make_payload(author_site=evil))

        assert response.status_code == 422, f"{evil!r} 竟然被接受了"
        assert await count_messages() == 0

    async def test_ip_and_user_agent_are_recorded(self, client: AsyncClient) -> None:
        """反垃圾留痕要真的落库（响应里看不到，只能直连数据库验）。"""
        created = await create_message(client)
        row = await fetch_row(int(created["id"]))  # type: ignore[arg-type]

        # ASGITransport 的客户端地址固定是 127.0.0.1（与生产走 client_ip 同一函数）
        assert row.ip_address == "127.0.0.1"
        assert row.user_agent is not None

    async def test_user_agent_is_truncated_to_500(self, client: AsyncClient) -> None:
        """UA 是反垃圾线索、不是内容：超长截断而不是拒收（与评论同一口径）。"""
        response = await client.post(
            PUBLIC_LIST, json=make_payload(), headers={"User-Agent": "u" * 800}
        )
        assert response.status_code == 201

        row = await fetch_row(int(response.json()["id"]))
        assert row.user_agent is not None
        assert len(row.user_agent) == 500


class TestRateLimit:
    """限流是留言板「先审后发」之外的第二道防线。"""

    async def test_sixth_message_is_blocked(self, client: AsyncClient) -> None:
        for index in range(5):
            response = await client.post(PUBLIC_LIST, json=make_payload(content=f"第 {index} 条"))
            assert response.status_code == 201, response.text

        blocked = await client.post(PUBLIC_LIST, json=make_payload(content="第六条"))

        assert blocked.status_code == 429
        assert blocked.json()["code"] == "rate_limited"
        # Retry-After 必须给：没有它客户端会原地重试，限流反而变成放大攻击
        assert int(blocked.headers["Retry-After"]) >= 1

    async def test_blocked_request_does_not_reach_service(self, client: AsyncClient) -> None:
        for index in range(5):
            await client.post(PUBLIC_LIST, json=make_payload(content=f"第 {index} 条"))
        assert (await client.post(PUBLIC_LIST, json=make_payload())).status_code == 429

        assert await count_messages() == 5, "被限流的那条仍然写进了数据库"

    async def test_bucket_is_independent_from_comments(self, client: AsyncClient) -> None:
        """规则名参与 key：留言刷满不该把评论也一起封掉（反之亦然）。"""
        for index in range(5):
            await client.post(PUBLIC_LIST, json=make_payload(content=f"第 {index} 条"))
        assert (await client.post(PUBLIC_LIST, json=make_payload())).status_code == 429

        # 评论接口仍应可达（用不存在的文章拿 404，关键是「没有被 429 拦下」）
        response = await client.post(
            "/api/v1/comments/article/999999",
            json={"author_name": "访客", "content": "测试内容"},
        )
        assert response.status_code != 429

    async def test_reads_are_not_throttled(self, client: AsyncClient) -> None:
        """限流只上在写入口：翻页是正常浏览路径。"""
        for _ in range(8):
            assert (await client.get(PUBLIC_LIST)).status_code == 200

        limiter.reset()  # 显式还原，避免影响同文件后续用例的直觉


class TestManage:
    async def test_requires_login(self, client: AsyncClient) -> None:
        assert (await client.get(MANAGED_LIST)).status_code == 401

    async def test_forbidden_for_author(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """留言板是站点级空间：作者能发文章，但不能看别人的邮箱与 IP。"""
        assert (await client.get(MANAGED_LIST, headers=author_headers)).status_code == 403

    async def test_admin_sees_email_and_ip(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await create_message(client, author_email=GUEST_EMAIL)

        body = await list_manage(client, admin_headers)

        assert body["total"] == 1
        item = body["items"][0]
        assert item["author_email"] == GUEST_EMAIL
        assert item["ip_address"] == "127.0.0.1"
        # 公开字段 + 邮箱 + IP；UA 仍然不给（审核用不上它）
        assert set(item) == PUBLIC_FIELDS | {"author_email", "ip_address"}

    async def test_filter_by_approved(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        pending = await create_message(client, content="待审的")
        approved = await create_message(client, admin_headers, content="已过审的")

        only_pending = await list_manage(client, admin_headers, approved="false")
        assert [item["id"] for item in only_pending["items"]] == [pending["id"]]

        only_approved = await list_manage(client, admin_headers, approved="true")
        assert [item["id"] for item in only_approved["items"]] == [approved["id"]]

        everything = await list_manage(client, admin_headers)
        assert everything["total"] == 2

    async def test_manage_is_not_publicly_cached(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get(MANAGED_LIST, headers=admin_headers)

        assert response.status_code == 200
        assert "etag" not in response.headers

    @pytest.mark.parametrize(("path", "expected"), [(PUBLIC_LIST, True), (MANAGED_LIST, False)])
    def test_cache_whitelist_covers_public_but_not_manage(self, path: str, expected: bool) -> None:
        """白名单按前缀匹配，``/manage`` 必须靠 ``_EXCLUDED_MARKERS`` 挡掉。

        这一条与 HTTP 层那几条是**两层**证明：``/manage`` 带凭证时本来就不会被缓存，
        只有在「匿名请求」这一侧才看得出标记有没有生效——所以直接看判定函数。
        """
        from starlette.requests import Request

        from app.api.cache import is_cacheable_request

        request = Request(
            {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
        )
        assert is_cacheable_request(request) is expected


class TestModeration:
    async def test_approve_then_visible(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client)
        assert (await list_public(client))["total"] == 0

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"is_approved": True}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["is_approved"] is True
        assert (await list_public(client))["total"] == 1

    async def test_revoke_hides_it_again(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, admin_headers)
        assert (await list_public(client))["total"] == 1

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"is_approved": False}, headers=admin_headers
        )

        assert response.status_code == 200
        assert (await list_public(client))["total"] == 0
        # 撤回不是删除：后台仍然看得到它
        assert (await list_manage(client, admin_headers))["total"] == 1

    async def test_patch_without_flag_approves(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """不传 ``is_approved`` 视为放行（与 ``CommentModerate`` 同一口径）。"""
        created = await create_message(client)

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["is_approved"] is True

    async def test_missing_message_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"{PUBLIC_LIST}/999999", json={"is_approved": True}, headers=admin_headers
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "留言不存在"

    async def test_guest_cannot_moderate(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client)

        response = await client.patch(f"{PUBLIC_LIST}/{created['id']}", json={"is_approved": True})
        assert response.status_code == 401

    async def test_author_cannot_moderate(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        created = await create_message(client)

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}",
            json={"is_approved": True},
            headers=author_headers,
        )
        assert response.status_code == 403


class TestReply:
    async def test_reply_is_visible_in_public_response(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, content="第一次来，很喜欢这里")
        await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"is_approved": True}, headers=admin_headers
        )

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "  谢谢留言，常来  "},
            headers=admin_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["reply_content"] == "谢谢留言，常来"  # 前后空白被 strip
        assert body["replied_at"] is not None

        item = (await list_public(client))["items"][0]
        assert item["reply_content"] == "谢谢留言，常来"
        assert item["replied_at"] is not None

    async def test_empty_reply_clears_everything(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """空字符串（或纯空白）＝ 清除回复。

        ``replied_at`` / ``replied_by_id`` 必须一并清掉：留下「有回复时间、
        没回复内容」的半截状态，前端就得为它写一套没意义的展示分支。
        """
        created = await create_message(client, admin_headers)
        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "先回一句"},
            headers=admin_headers,
        )
        # 先确认真的写进去了，否则下面断言「被清空」可能只是因为它从来没被写过
        assert (await fetch_row(int(created["id"]))).replied_by_id is not None

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": ""},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["reply_content"] is None
        assert response.json()["replied_at"] is None
        row = await fetch_row(int(created["id"]))
        assert row.reply_content is None
        assert row.replied_at is None
        assert row.replied_by_id is None
        item = (await list_public(client))["items"][0]
        assert item["reply_content"] is None and item["replied_at"] is None

    async def test_blank_only_reply_also_clears(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, admin_headers)
        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "回复"},
            headers=admin_headers,
        )

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "   \n "},
            headers=admin_headers,
        )

        assert response.json()["reply_content"] is None

    async def test_omitting_reply_content_is_not_clearing(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """字段是必填：不传就是 422，而不是被当成「清空」。

        「不传」与「传空串」必须是两个不同的动作，否则一个手滑的 PATCH
        就会把站长写好的回复删掉。
        """
        created = await create_message(client, admin_headers)

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply", json={}, headers=admin_headers
        )

        assert response.status_code == 422

    async def test_reply_too_long_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, admin_headers)

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "字" * 2001},
            headers=admin_headers,
        )

        assert response.status_code == 422

    async def test_reply_to_pending_message_is_allowed(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """可以边审边回：回复先写在行上，过审后与留言一起出现在前台。"""
        created = await create_message(client)

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "先回一句，等下再放行"},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert (await list_public(client))["total"] == 0  # 没过审，前台仍然看不到

    async def test_missing_message_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.put(
            f"{PUBLIC_LIST}/999999/reply",
            json={"reply_content": "回复"},
            headers=admin_headers,
        )

        assert response.status_code == 404

    async def test_guest_cannot_reply(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client)

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply", json={"reply_content": "假冒站长"}
        )

        assert response.status_code == 401

    async def test_author_cannot_reply(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        created = await create_message(client)

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "作者来插一句"},
            headers=author_headers,
        )

        assert response.status_code == 403


class TestDelete:
    async def test_admin_can_delete(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, admin_headers)
        assert (await list_public(client))["total"] == 1

        response = await client.delete(f"{PUBLIC_LIST}/{created['id']}", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["detail"] == "留言已删除"
        # 两处都要核：公开列表与后台列表都不能再有它
        assert (await list_public(client))["total"] == 0
        assert (await list_manage(client, admin_headers))["total"] == 0

    async def test_delete_removes_the_reply_too(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """回复与留言在同一行，删留言必须连回复一起消失（不留孤儿数据）。"""
        created = await create_message(client, author_email=GUEST_EMAIL)
        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "回复"},
            headers=admin_headers,
        )

        await client.delete(f"{PUBLIC_LIST}/{created['id']}", headers=admin_headers)

        assert await count_messages() == 0

    async def test_delete_missing_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        assert (
            await client.delete(f"{PUBLIC_LIST}/999999", headers=admin_headers)
        ).status_code == 404

    async def test_guest_cannot_delete(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, admin_headers)

        assert (await client.delete(f"{PUBLIC_LIST}/{created['id']}")).status_code == 401

    async def test_author_cannot_delete(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        created = await create_message(client, admin_headers)

        response = await client.delete(f"{PUBLIC_LIST}/{created['id']}", headers=author_headers)
        assert response.status_code == 403


class MailBox:
    """替换 ``email_sender`` 的假发件箱：记下每封信，永远成功。

    与 ``tests/test_notifications.py`` 里的同形；本文件保持自包含，
    不 import 另一个测试模块（测试之间的隐式依赖比几行重复更贵）。
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, text: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "text": text})
        return True

    def to_list(self) -> list[str]:
        return [mail["to"] for mail in self.sent]


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> MailBox:
    """打开 SMTP 开关并把发件器换成假发件箱（照搬评论通知测试的手法）。"""
    box = MailBox()
    monkeypatch.setattr(settings, "smtp_enabled", True)
    monkeypatch.setattr(notification_service, "email_sender", box)
    return box


class TestNotifications:
    """通知矩阵：留言板只发两封信——给站长的新留言提醒、给留言者的回复通知。

    「过审」刻意不发信：对访客来说「我的留言公开了」不是他关心的事件。
    """

    async def test_create_notifies_admin(self, client: AsyncClient, mailbox: MailBox) -> None:
        """待审留言恰恰是最需要提醒站长的时刻，所以创建即通知。"""
        await create_message(client, author_email=GUEST_EMAIL, content="第一次来留言")

        await drain_notifications()

        assert mailbox.to_list() == [ADMIN_EMAIL]
        mail = mailbox.sent[0]
        assert "新留言" in mail["subject"]
        assert "路人甲" in mail["text"]
        assert "第一次来留言" in mail["text"]
        assert "/admin/guestbook" in mail["text"]

    async def test_new_reply_notifies_the_author(
        self, client: AsyncClient, admin_headers: dict[str, str], mailbox: MailBox
    ) -> None:
        created = await create_message(client, author_email=GUEST_EMAIL, content="我的留言")
        await drain_notifications()
        mailbox.sent.clear()  # 创建时那条给站长的提醒与本用例无关

        response = await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "欢迎常来"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        await drain_notifications()

        assert mailbox.to_list() == [GUEST_EMAIL]
        mail = mailbox.sent[0]
        assert "回复" in mail["subject"]
        assert "欢迎常来" in mail["text"]
        assert "我的留言" in mail["text"]  # 带上原文，几个月前的留言才对得上号
        assert f"{settings.site_base_url}/guestbook" in mail["text"]
        # token 在 fragment 而不是 query —— 见 test_notifications.py 的说明
        assert "/unsubscribe#token=" in mail["text"]

    async def test_clearing_reply_sends_nothing(
        self, client: AsyncClient, admin_headers: dict[str, str], mailbox: MailBox
    ) -> None:
        """清除回复不是「新回复」：留言者不该收到一封「回复被删了」的信。"""
        created = await create_message(client, author_email=GUEST_EMAIL)
        await drain_notifications()
        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "先回一句"},
            headers=admin_headers,
        )
        await drain_notifications()
        mailbox.sent.clear()

        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": ""},
            headers=admin_headers,
        )
        await drain_notifications()

        assert mailbox.sent == []

    async def test_editing_an_existing_reply_does_not_notify_again(
        self, client: AsyncClient, admin_headers: dict[str, str], mailbox: MailBox
    ) -> None:
        """改错别字走的是同一个 PUT 接口。

        若以「接口被调用」为准，站长每润色一次措辞，留言者就再收一封信——
        通知去重的闸门是「从没有回复变成有回复」，判定在 ``set_reply``。
        """
        created = await create_message(client, author_email=GUEST_EMAIL)
        await drain_notifications()
        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "有错别字的回复"},
            headers=admin_headers,
        )
        await drain_notifications()
        mailbox.sent.clear()

        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "改好的回复"},
            headers=admin_headers,
        )
        await drain_notifications()

        assert mailbox.sent == []

    async def test_reply_to_pending_message_still_notifies(
        self, client: AsyncClient, admin_headers: dict[str, str], mailbox: MailBox
    ) -> None:
        """待审留言的回复照样发信。

        「过审」本身不发通知，所以如果把回复通知也压到过审那一刻，站长「边审边回」
        时这封信就永远不会发出去了——宁可早一点，也不要静默丢掉。
        """
        created = await create_message(client, author_email=GUEST_EMAIL)
        await drain_notifications()
        mailbox.sent.clear()

        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "先回一句"},
            headers=admin_headers,
        )
        await drain_notifications()

        assert mailbox.to_list() == [GUEST_EMAIL]

    async def test_message_without_email_gets_no_reply_mail(
        self, client: AsyncClient, admin_headers: dict[str, str], mailbox: MailBox
    ) -> None:
        """没留邮箱就收不到信（这是留言板通知的唯一前提），但站长提醒照发。"""
        created = await create_message(client)
        await drain_notifications()
        assert mailbox.to_list() == [ADMIN_EMAIL]
        mailbox.sent.clear()

        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "回复一个没留邮箱的留言"},
            headers=admin_headers,
        )
        await drain_notifications()

        assert mailbox.sent == []

    async def test_unsubscribed_author_gets_no_reply_mail(
        self, client: AsyncClient, admin_headers: dict[str, str], mailbox: MailBox
    ) -> None:
        """复用既有的退订名单：退订过的人不再收到留言回复通知。"""
        created = await create_message(client, author_email=GUEST_EMAIL)
        await drain_notifications()
        mailbox.sent.clear()

        token = create_unsubscribe_token(GUEST_EMAIL)
        assert (
            await client.post("/api/v1/notifications/unsubscribe", json={"token": token})
        ).status_code == 200

        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "回复"},
            headers=admin_headers,
        )
        await drain_notifications()

        assert mailbox.sent == []

    async def test_smtp_disabled_sends_nothing(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SMTP 关闭（默认）：留言 + 回复全流程一封信也没有，接口一切正常。

        通知是旁路功能，绝不能拖垮主流程，更不能因为没配 SMTP 就报错。
        """
        box = MailBox()
        monkeypatch.setattr(settings, "smtp_enabled", False)
        monkeypatch.setattr(notification_service, "email_sender", box)

        created = await create_message(client, author_email=GUEST_EMAIL)
        await client.put(
            f"{PUBLIC_LIST}/{created['id']}/reply",
            json={"reply_content": "回复"},
            headers=admin_headers,
        )
        await drain_notifications()

        assert box.sent == []

    async def test_sender_failure_does_not_break_the_request(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """发信器抛异常也不能影响留言接口：那已经是 commit 之后的后台任务。"""

        class ExplodingSender:
            async def send(self, *, to: str, subject: str, text: str) -> bool:
                raise RuntimeError("SMTP 挂了")

        monkeypatch.setattr(settings, "smtp_enabled", True)
        monkeypatch.setattr(notification_service, "email_sender", ExplodingSender())

        response = await client.post(PUBLIC_LIST, json=make_payload(author_email=GUEST_EMAIL))

        assert response.status_code == 201
        await drain_notifications()  # 不抛异常即说明 _guard 生效
