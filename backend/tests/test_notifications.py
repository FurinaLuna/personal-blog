"""评论邮件通知测试。

核心契约（与 ROADMAP 1.5 对齐）：
1. 退订幂等、无效 token 拒绝；
2. 通知规则矩阵——谁在什么时机收到信（见 notification_service.py 的表格）；
3. 通知是 fire-and-forget：SMTP 关闭时评论主流程完全不受影响。

同步手段：路由在 commit 后 ``create_task``，测试里用 ``drain_notifications``
等后台任务跑完再断言——不 sleep，不轮询。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from jwt import InvalidTokenError

from app.config import settings
from app.services import notification_service
from app.services.notification_service import drain_notifications
from app.utils.security import create_unsubscribe_token, decode_unsubscribe_token

UNSUBSCRIBE_URL = "/api/v1/notifications/unsubscribe"
ADMIN_EMAIL = "admin@example.com"
PARENT_EMAIL = "parent@example.com"


def _guest_comment(name: str, email: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "author_name": name,
        "author_email": email,
        "content": "有内容的评论",
    }
    payload.update(overrides)
    return payload


class MailBox:
    """替换 email_sender 的假发件箱：记下每封信，永远成功。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, text: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "text": text})
        return True

    def to_list(self) -> list[str]:
        return [mail["to"] for mail in self.sent]


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> MailBox:
    """打开 SMTP 开关并把发件器换成假发件箱。"""
    box = MailBox()
    monkeypatch.setattr(settings, "smtp_enabled", True)
    monkeypatch.setattr(notification_service, "email_sender", box)
    return box


async def _approve(client: AsyncClient, comment_id: int, admin_headers: dict[str, str]) -> None:
    """审核通过并等通知任务跑完。"""
    response = await client.patch(
        f"/api/v1/comments/{comment_id}", json={"is_approved": True}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    await drain_notifications()


async def _post_comment(
    client: AsyncClient, article_id: int, payload: dict, headers: dict | None = None
) -> dict:
    """发评论并等通知任务跑完，返回创建结果。"""
    response = await client.post(
        f"/api/v1/comments/article/{article_id}", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    await drain_notifications()
    return response.json()


async def _seed_root_comment(
    client: AsyncClient, article: dict, admin_headers: dict[str, str]
) -> int:
    """造一条已过审的顶级评论（作为被回复者），返回 id。"""
    created = await _post_comment(client, article["id"], _guest_comment("甲", PARENT_EMAIL))
    await _approve(client, created["id"], admin_headers)
    return created["id"]


class TestUnsubscribe:
    async def test_valid_token_unsubscribes(self, client: AsyncClient) -> None:
        """大小写混合的邮箱也要归一成小写入库。"""
        token = create_unsubscribe_token("Reader@Example.COM")
        response = await client.post(UNSUBSCRIBE_URL, json={"token": token})
        assert response.status_code == 200
        assert "reader@example.com" in response.json()["detail"]

    async def test_unsubscribe_is_idempotent(self, client: AsyncClient) -> None:
        """用户重复点链接、邮件客户端重发请求都必须无感成功。"""
        token = create_unsubscribe_token("dup@example.com")
        for _ in range(2):
            response = await client.post(UNSUBSCRIBE_URL, json={"token": token})
            assert response.status_code == 200

    async def test_garbage_token_rejected(self, client: AsyncClient) -> None:
        response = await client.post(UNSUBSCRIBE_URL, json={"token": "not-a-jwt"})
        assert response.status_code == 400

    async def test_wrong_token_type_rejected(self, client: AsyncClient, admin_token: str) -> None:
        """拿登录 access token 冒充退订 token：类型不符，拒绝。"""
        response = await client.post(UNSUBSCRIBE_URL, json={"token": admin_token})
        assert response.status_code == 400


class TestNotificationRules:
    """通知矩阵：每封信的收件人和时机都必须是对的。"""

    async def test_pending_reply_notifies_admin_only(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        mailbox: MailBox,
    ) -> None:
        """待审回复：被回复者还收不到信（没过审不通知），只有站长收到新评论提醒。"""
        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        mailbox.sent.clear()  # 只看这一步产生的信

        await _post_comment(
            client,
            published_article["id"],
            _guest_comment("乙", "child@example.com", parent_id=parent_id),
        )
        assert mailbox.to_list() == [ADMIN_EMAIL]  # 没有发给 parent@example.com

    async def test_approval_notifies_replied_commenter(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        mailbox: MailBox,
    ) -> None:
        """审核通过的那一刻，被回复者收到含退订链接的信。"""
        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        mailbox.sent.clear()

        reply = await _post_comment(
            client,
            published_article["id"],
            _guest_comment("乙", "child@example.com", parent_id=parent_id),
        )
        mailbox.sent.clear()  # 创建时给站长的提醒与本用例无关，只看过审这封信

        await _approve(client, reply["id"], admin_headers)

        assert mailbox.to_list() == [PARENT_EMAIL]
        mail = mailbox.sent[0]
        assert mail["subject"] == "您的评论收到了新回复"
        # token 在 fragment 而不是 query —— 见 TestUnsubscribeLinkFormat 的说明
        assert "/unsubscribe#token=" in mail["text"]

    async def test_unsubscribed_email_gets_no_reply_mail(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        mailbox: MailBox,
    ) -> None:
        """退订后：新评论提醒照发（那是给站长的），回复通知不再发。"""
        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        mailbox.sent.clear()

        token = create_unsubscribe_token(PARENT_EMAIL)
        assert (await client.post(UNSUBSCRIBE_URL, json={"token": token})).status_code == 200

        reply = await _post_comment(
            client,
            published_article["id"],
            _guest_comment("乙", "child@example.com", parent_id=parent_id),
        )
        await _approve(client, reply["id"], admin_headers)

        assert mailbox.to_list() == [ADMIN_EMAIL]

    async def test_admin_reply_notifies_replied_but_not_admin_self(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        mailbox: MailBox,
    ) -> None:
        """站长回复（自动过审）：被回复者立刻收到信，站长不给自己发新评论提醒。"""
        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        mailbox.sent.clear()

        await _post_comment(
            client,
            published_article["id"],
            {"content": "站长亲自回复", "parent_id": parent_id},
            headers=admin_headers,
        )
        assert mailbox.to_list() == [PARENT_EMAIL]

    async def test_reply_to_own_comment_no_self_mail(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        mailbox: MailBox,
    ) -> None:
        """自己接自己话：不给自己写信（站长提醒照常）。"""
        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        mailbox.sent.clear()

        await _post_comment(
            client,
            published_article["id"],
            _guest_comment("甲", PARENT_EMAIL, parent_id=parent_id),
        )
        assert mailbox.to_list() == [ADMIN_EMAIL]

    async def test_smtp_disabled_sends_nothing(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SMTP 关闭（默认）：创建 + 过审全流程一封信也没有，接口一切正常。"""
        box = MailBox()
        monkeypatch.setattr(settings, "smtp_enabled", False)
        monkeypatch.setattr(notification_service, "email_sender", box)

        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        reply = await _post_comment(
            client,
            published_article["id"],
            _guest_comment("乙", "child@example.com", parent_id=parent_id),
        )
        await _approve(client, reply["id"], admin_headers)
        assert box.sent == []

    async def test_reply_mail_contains_article_link(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        mailbox: MailBox,
    ) -> None:
        """信里的「查看与回复」链接指向该文章详情页。"""
        parent_id = await _seed_root_comment(client, published_article, admin_headers)
        mailbox.sent.clear()

        reply = await _post_comment(
            client,
            published_article["id"],
            _guest_comment("乙", "child@example.com", parent_id=parent_id),
        )
        mailbox.sent.clear()

        await _approve(client, reply["id"], admin_headers)

        assert f"/article/{published_article['slug']}" in mailbox.sent[0]["text"]


class TestUnsubscribeStorage:
    async def test_unsubscribe_row_is_stored(self, client: AsyncClient) -> None:
        """退订记录确实落库——换个角度读同一张表，避免测试与实现共享查询。"""
        from app.db.session import async_session_factory
        from app.models import NotificationOptOut

        token = create_unsubscribe_token("again@example.com")
        assert (await client.post(UNSUBSCRIBE_URL, json={"token": token})).status_code == 200

        async with async_session_factory() as session:
            stored = await session.get(NotificationOptOut, 1)
            assert stored is not None
            assert stored.email == "again@example.com"


class TestTokenRoundTrip:
    def test_token_normalizes_email_to_lowercase(self) -> None:
        """大小写混合的邮箱，签发-验签后必须是小写——插入方依赖这条约定。"""
        assert decode_unsubscribe_token(create_unsubscribe_token("MiXeD@Example.COM")) == (
            "mixed@example.com"
        )

    def test_token_type_mismatch_rejected(self) -> None:
        """拿别处签的 JWT 冒充退订 token：类型不符，拒绝。"""
        from app.utils.security import create_access_token

        with pytest.raises(InvalidTokenError):
            decode_unsubscribe_token(create_access_token(1, "admin"))


class TestSendFailureIsIsolated:
    async def test_comment_still_created_when_sender_raises(
        self,
        client: AsyncClient,
        published_article: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """发信器抛异常也不能影响评论接口：那已经是 commit 之后的后台任务。

        同时验证 ``_guard`` 兜住了任务异常——否则这里 drain 会把异常抛出来。
        """

        class ExplodingSender:
            async def send(self, *, to: str, subject: str, text: str) -> bool:
                raise RuntimeError("SMTP 挂了")

        monkeypatch.setattr(settings, "smtp_enabled", True)
        monkeypatch.setattr(notification_service, "email_sender", ExplodingSender())

        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_guest_comment("甲", PARENT_EMAIL),
        )
        assert response.status_code == 201
        await drain_notifications()  # 不抛异常即说明 _guard 生效


class TestUnsubscribeLinkFormat:
    """退订链接里 token 的位置。

    token 必须放在 **URL fragment**（``#`` 之后）而不是 query string：
    fragment 由浏览器保留在本地、不随请求发给服务器，因此不会落进
    nginx 访问日志、Referer 头或中间代理日志。query string 则会 ——
    而这是一个有效期 10 年、凭它就能为任意邮箱退订的凭证。
    """

    def test_token_is_in_fragment_not_query(self) -> None:
        from app.services.notification_service import _unsubscribe_link

        link = _unsubscribe_link("someone@example.com")

        assert "#token=" in link, f"token 不在 fragment 里：{link}"
        assert "?token=" not in link, f"token 出现在 query string 里，会进访问日志：{link}"

    def test_link_points_at_frontend_route(self) -> None:
        from app.config import settings
        from app.services.notification_service import _unsubscribe_link

        link = _unsubscribe_link("someone@example.com")
        assert link.startswith(settings.site_base_url)
        assert "/unsubscribe#token=" in link

    def test_emitted_token_is_accepted_by_the_decoder(self) -> None:
        """端到端：链接里那个 token 必须真的能被退订接口解出邮箱。

        只断言字符串格式是不够的——格式对了但 token 本身有问题，
        用户点进去只会看到「链接无效」。
        """
        from app.services.notification_service import _unsubscribe_link
        from app.utils.security import decode_unsubscribe_token

        link = _unsubscribe_link("Someone@Example.COM")
        token = link.split("#token=", 1)[1]
        assert decode_unsubscribe_token(token) == "someone@example.com"
