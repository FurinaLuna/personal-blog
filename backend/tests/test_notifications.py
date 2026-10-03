"""评论邮件通知测试。

核心契约（与 ROADMAP 1.5 对齐）：
1. 退订幂等、无效 token 拒绝；
2. 通知规则矩阵——谁在什么时机收到信（见 notification_service.py 的表格）；
3. 通知是 fire-and-forget：SMTP 关闭时评论主流程完全不受影响。

同步手段：路由在 commit 后 ``create_task``，测试里用 ``drain_notifications``
等后台任务跑完再断言——不 sleep，不轮询。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.policy import default as email_policy

import jwt
import pytest
from httpx import AsyncClient
from jwt import InvalidTokenError

from app.config import settings
from app.db.session import async_session_factory
from app.models import NotificationOptOut
from app.services import notification_service
from app.services.notification_service import drain_notifications
from app.utils.security import create_unsubscribe_token, decode_unsubscribe_token
from tests.factories import unique_suffix

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


# ----------------------------------------------------------------------
# 以下用例自「wave3 全面验证批」归并而来（原 tests/test_wave3_verify.py）：
# token 边界、真实本地 SMTP 服务器（真实网络传输）与发信失败的隔离性。


class TestUnsubscribeTokenBoundaries:
    async def test_empty_token_rejected(self, client: AsyncClient) -> None:
        assert (await client.post(UNSUBSCRIBE_URL, json={"token": ""})).status_code == 422

    async def test_expired_token_rejected(self, client: AsyncClient) -> None:
        """过期链接必须失效（10 年期是上限，不是豁免）。"""
        past = datetime.now(UTC) - timedelta(days=1)
        expired = jwt.encode(
            {
                "sub": "expired@example.com",
                "type": "unsubscribe",
                "iat": past - timedelta(days=1),
                "exp": past,
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        assert (await client.post(UNSUBSCRIBE_URL, json={"token": expired})).status_code == 400

    async def test_token_signed_with_other_secret_rejected(self, client: AsyncClient) -> None:
        forged = jwt.encode(
            {
                "sub": "attacker@example.com",
                "type": "unsubscribe",
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(days=1),
            },
            "attacker-secret-key-0123456789-0123456789",
            algorithm="HS256",
        )
        assert (await client.post(UNSUBSCRIBE_URL, json={"token": forged})).status_code == 400

    async def test_tampered_signature_rejected(self, client: AsyncClient) -> None:
        token = create_unsubscribe_token("tamper@example.com")
        head, payload, signature = token.split(".")
        flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
        tampered = f"{head}.{payload}.{flipped}"
        assert (await client.post(UNSUBSCRIBE_URL, json={"token": tampered})).status_code == 400

    async def test_case_variants_share_one_opt_out_row(self, client: AsyncClient) -> None:
        """同一邮箱的不同大小写必须归一到一行（否则退订会被绕过）。"""
        email = f"Case{unique_suffix()}@Example.COM"
        for variant in (email.upper(), email.lower()):
            token = create_unsubscribe_token(variant)
            assert (await client.post(UNSUBSCRIBE_URL, json={"token": token})).status_code == 200

        async with async_session_factory() as session:
            record = await session.get(NotificationOptOut, 1)
            assert record is not None
            assert record.email == email.lower()


class SmtpCaptureServer:
    """极简本地 SMTP 收信服务器（仅测试用，零第三方依赖）。

    支持 EHLO / AUTH PLAIN / AUTH LOGIN / MAIL FROM / RCPT TO / DATA / QUIT，
    把每封原始邮件（RFC822 字节）存下来。用于验证**真实** aiosmtplib 客户端
    链路，而不是 mock 掉发信器。
    """

    def __init__(self, *, auth_ok: bool = True) -> None:
        self.auth_ok = auth_ok
        self.raw_messages: list[bytes] = []
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> SmtpCaptureServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    # ---------- 解析出的邮件（收件人/主题/正文） ----------
    def mailbox(self) -> list[dict[str, str]]:
        parsed: list[dict[str, str]] = []
        for raw in self.raw_messages:
            # policy=default 才会把 =?utf-8?b?...?= 解码成可读文本
            message = message_from_bytes(raw, policy=email_policy)
            body = message.get_payload(decode=True) or b""
            parsed.append(
                {
                    "to": str(message["To"] or ""),
                    "from": str(message["From"] or ""),
                    "subject": str(message["Subject"] or ""),
                    "text": body.decode("utf-8", "replace"),
                }
            )
        return parsed

    # ---------- 协议实现 ----------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await self._reply(writer, b"220 capture ESMTP ready")
        envelope_to: list[str] = []
        data_lines: list[bytes] = []
        in_data = False
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if in_data:
                    if line.strip() == b".":
                        in_data = False
                        self.raw_messages.append(b"".join(data_lines))
                        data_lines = []
                        envelope_to = []
                        await self._reply(writer, b"250 2.0.0 Ok: queued")
                    else:
                        data_lines.append(line[1:] if line.startswith(b"..") else line)
                    continue

                command = line.decode("utf-8", "replace").strip()
                upper = command.upper()
                if upper.startswith(("EHLO", "HELO")):
                    writer.write(
                        b"250-capture greets you\r\n"
                        b"250-AUTH PLAIN LOGIN\r\n"
                        b"250-8BITMIME\r\n"
                        b"250 SIZE 10485760\r\n"
                    )
                    await writer.drain()
                elif upper.startswith("AUTH"):
                    await self._handle_auth(upper, reader, writer)
                elif upper.startswith("MAIL FROM"):
                    await self._reply(writer, b"250 2.1.0 Sender ok")
                elif upper.startswith("RCPT TO"):
                    envelope_to.append(command)
                    await self._reply(writer, b"250 2.1.5 Recipient ok")
                elif upper == "DATA":
                    in_data = True
                    await self._reply(writer, b"354 End data with <CR><LF>.<CR><LF>")
                elif upper == "RSET":
                    data_lines, envelope_to = [], []
                    await self._reply(writer, b"250 2.0.0 Ok")
                elif upper == "NOOP":
                    await self._reply(writer, b"250 2.0.0 Ok")
                elif upper == "QUIT":
                    await self._reply(writer, b"221 2.0.0 Bye")
                    break
                else:
                    await self._reply(writer, b"250 2.0.0 Ok")
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionResetError, OSError):
                await writer.wait_closed()

    async def _handle_auth(
        self, upper: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if not self.auth_ok:
            await self._reply(writer, b"535 5.7.8 Authentication credentials invalid")
            return
        if "LOGIN" in upper:
            await self._reply(writer, b"334 VXNlcm5hbWU6")
            await reader.readline()
            await self._reply(writer, b"334 UGFzc3dvcmQ6")
            await reader.readline()
        elif not upper.split("AUTH", 1)[1].strip():
            # AUTH 无参数：PLAIN 交互式
            await self._reply(writer, b"334 ")
            await reader.readline()
        else:
            # AUTH PLAIN <base64> 内联：仅校验可解码
            blob = upper.split("AUTH", 1)[1].strip().split()[-1]
            try:
                base64.b64decode(blob.encode(), validate=False)
            except Exception:  # 测试服务器：任何解码失败都算认证失败
                await self._reply(writer, b"535 5.7.8 Authentication credentials invalid")
                return
        await self._reply(writer, b"235 2.7.0 Authentication successful")

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, payload: bytes) -> None:
        writer.write(payload + b"\r\n")
        await writer.drain()


def _point_settings_at(monkeypatch: pytest.MonkeyPatch, server: SmtpCaptureServer) -> None:
    monkeypatch.setattr(settings, "smtp_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", server.port)
    monkeypatch.setattr(settings, "smtp_use_tls", False)
    monkeypatch.setattr(settings, "smtp_username", "blog@example.com")
    monkeypatch.setattr(settings, "smtp_password", "smtp-password")
    monkeypatch.setattr(settings, "smtp_from", "blog@example.com")


class TestRealSmtpDelivery:
    """真实 SMTP 传输：真实 aiosmtplib → 本地服务器，断言信封与正文内容。"""

    async def test_full_notification_journey_over_real_smtp(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async with SmtpCaptureServer() as server:
            _point_settings_at(monkeypatch, server)

            # 1) 顶级评论（待审）→ 应给站长发信（含审核链接）
            parent = await _post_comment(
                client, published_article["id"], _guest_comment("甲", PARENT_EMAIL)
            )
            mails = server.mailbox()
            assert [m["to"] for m in mails] == [ADMIN_EMAIL]
            admin_mail = mails[0]
            assert admin_mail["from"] == "blog@example.com"
            assert "新评论提醒" in admin_mail["subject"]
            assert "/admin/comments?approved=false" in admin_mail["text"]
            assert "待审核" in admin_mail["text"]

            await _approve(client, parent["id"], admin_headers)
            assert len(server.mailbox()) == 1  # 过审一条顶级评论：无被回复者，不发信

            # 2) 回复该评论（待审）→ 只提醒站长，被回复者还没到通知时机
            server.raw_messages.clear()
            reply = await _post_comment(
                client,
                published_article["id"],
                _guest_comment("乙", "child@example.com", parent_id=parent["id"]),
            )
            assert [m["to"] for m in server.mailbox()] == [ADMIN_EMAIL]

            # 3) 审核通过 → 被回复者收到含退订链接的复信通知
            server.raw_messages.clear()
            await _approve(client, reply["id"], admin_headers)
            mails = server.mailbox()
            assert [m["to"] for m in mails] == [PARENT_EMAIL]
            reply_mail = mails[0]
            assert reply_mail["subject"] == "您的评论收到了新回复"
            assert "乙" in reply_mail["text"]
            assert f"/article/{published_article['slug']}" in reply_mail["text"]

            # 4) 从真实邮件正文里抠出退订链接并真的点它 → 退订生效
            # token 在 fragment（#）而不是 query —— 见 TestUnsubscribeLinkFormat 的说明
            match = re.search(r"/unsubscribe#token=([\w.\-]+)", reply_mail["text"])
            assert match, f"正文缺少退订链接：{reply_mail['text']}"
            token = match.group(1)
            assert (await client.post(UNSUBSCRIBE_URL, json={"token": token})).status_code == 200
            async with async_session_factory() as session:
                record = await session.get(NotificationOptOut, 1)
                assert record is not None and record.email == PARENT_EMAIL

            # 5) 退订后新的回复过审 → 该邮箱不再收信（站长提醒照常）
            server.raw_messages.clear()
            second = await _post_comment(
                client,
                published_article["id"],
                _guest_comment("丙", "child2@example.com", parent_id=parent["id"]),
            )
            await _approve(client, second["id"], admin_headers)
            assert [m["to"] for m in server.mailbox()] == [ADMIN_EMAIL]

    async def test_chinese_subject_is_rfc2047_encoded(
        self,
        client: AsyncClient,
        published_article: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """中文主题必须编码传输，否则部分客户端显示乱码。"""
        async with SmtpCaptureServer() as server:
            _point_settings_at(monkeypatch, server)
            await _post_comment(
                client, published_article["id"], _guest_comment("甲", "cn@example.com")
            )
            raw = server.raw_messages[0]
            header_line = next(
                line for line in raw.split(b"\r\n") if line.lower().startswith(b"subject:")
            )
            assert b"=?" in header_line and b"?utf-8?" in header_line.lower()
            assert "新评论提醒" in server.mailbox()[0]["subject"]


class TestRealSmtpFailures:
    """SMTP 侧异常：任何失败都不能影响评论主流程（fire-and-forget 的底线）。"""

    @staticmethod
    def _closed_port() -> int:
        """拿一个刚刚释放的端口，连接它必然被拒。"""
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def test_connection_refused_does_not_break_comment(
        self,
        client: AsyncClient,
        published_article: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "smtp_enabled", True)
        monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
        monkeypatch.setattr(settings, "smtp_port", self._closed_port())
        monkeypatch.setattr(settings, "smtp_use_tls", False)
        monkeypatch.setattr(settings, "smtp_username", "blog@example.com")
        monkeypatch.setattr(settings, "smtp_password", "pw")

        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json=_guest_comment("甲", "refused@example.com"),
        )
        assert response.status_code == 201
        await drain_notifications()  # 不抛异常即为通过

    async def test_auth_failure_does_not_break_comment(
        self,
        client: AsyncClient,
        published_article: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async with SmtpCaptureServer(auth_ok=False) as server:
            _point_settings_at(monkeypatch, server)
            response = await client.post(
                f"/api/v1/comments/article/{published_article['id']}",
                json=_guest_comment("甲", "auth@example.com"),
            )
            assert response.status_code == 201
            await drain_notifications()
            assert server.raw_messages == []  # 认证失败，一封信也发不出去

    async def test_server_drops_connection_does_not_break_comment(
        self,
        client: AsyncClient,
        published_article: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """服务端接受连接后立刻断开：客户端必须失败得干净。"""

        async def _drop(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.close()

        server = await asyncio.start_server(_drop, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(settings, "smtp_enabled", True)
        monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
        monkeypatch.setattr(settings, "smtp_port", port)
        monkeypatch.setattr(settings, "smtp_use_tls", False)
        monkeypatch.setattr(settings, "smtp_username", "blog@example.com")
        monkeypatch.setattr(settings, "smtp_password", "pw")
        try:
            response = await client.post(
                f"/api/v1/comments/article/{published_article['id']}",
                json=_guest_comment("甲", "drop@example.com"),
            )
            assert response.status_code == 201
            await drain_notifications()
        finally:
            server.close()
            await server.wait_closed()

    async def test_notify_for_missing_comment_is_noop(
        self,
        client: AsyncClient,
        published_article: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """极端时序：评论已被删，后台任务按 id 查不到时必须安静退出。"""
        monkeypatch.setattr(settings, "smtp_enabled", True)
        notification_service.notify_comment_created(published_article["id"] + 10_000_000)
        await drain_notifications()  # 不抛异常即为通过
        assert (await client.get("/api/v1/articles")).status_code == 200


class TestReplyRecipientBoundaries:
    """收件人判定的边界：没有邮箱、站长本人、父评论为站长所写。"""

    async def test_reply_to_admin_comment_sends_nothing_to_admin(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        box = MailBox()
        monkeypatch.setattr(settings, "smtp_enabled", True)
        monkeypatch.setattr(notification_service, "email_sender", box)

        parent = await _post_comment(
            client,
            published_article["id"],
            {"content": "站长发言"},
            headers=admin_headers,
        )
        box.sent.clear()

        # 游客回复站长的评论：站长评论没有 email，且是站长所写 → 不发复信通知
        await _post_comment(
            client,
            published_article["id"],
            _guest_comment("游客", "guest@example.com", parent_id=parent["id"]),
        )
        assert box.to_list() == [ADMIN_EMAIL]  # 仅"新评论提醒"，没有复信通知
