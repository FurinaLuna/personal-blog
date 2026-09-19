"""功能第三波补充验证：1.6 多尺寸图片 / 1.4 统计趋势 / 1.5 评论通知的边界与异常分支。

本文件是本次"全面测试验证"的临时用例集，专门覆盖既有测试
（tests/test_attachments.py 的 TestImageVariants/TestVariantBackfill、
tests/test_stats.py、tests/test_notifications.py）未覆盖的边界、异常与集成分支：

* 1.6：GIF 跳过、中间档位、等宽不放大、命名规则、AVIF 优先与 WEBP 降级强制分支、
       backfill 的 limit 边界与 skipped 计数、原文件缺失的容错、封面 cover_variants 装配；
* 1.4：days 的 1/90 合法边界与非整数 422、404 不记、多文章聚合、不同 IP 的 UV、
       文章删除后访问日志的 CASCADE 清理、日期格式与升序；
* 1.5：**本地真实 SMTP 服务器收信**（真实 aiosmtplib 传输 + 真实退订链接闭环）、
       连接被拒/认证失败/服务端异常断开时的隔离性、token 过期/换密钥/篡改/空值。

注意：本文件依赖 conftest 的 ASGITransport 客户端与夹具。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import re
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.policy import default as email_policy
from pathlib import Path

import jwt
import pytest
from httpx import AsyncClient
from PIL import Image, features

from app.config import settings
from app.db.session import async_session_factory
from app.models import Attachment, NotificationOptOut
from app.services import notification_service
from app.services.notification_service import drain_notifications
from app.utils.security import create_unsubscribe_token
from tests.factories import make_article_payload, make_png_bytes, unique_suffix

ADMIN_EMAIL = "admin@example.com"
PARENT_EMAIL = "parent@example.com"
BACKFILL_URL = "/api/v1/attachments/backfill-variants"
UPLOAD_URL = "/api/v1/attachments/upload"
STATS_URL = "/api/v1/stats/views/daily"
UNSUBSCRIBE_URL = "/api/v1/notifications/unsubscribe"


def _files(name: str, content: bytes, mime: str) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, content, mime)}


def make_gif_bytes(size: tuple[int, int] = (1920, 1080)) -> bytes:
    """真实 GIF 字节流（走同样的真实解码校验）。"""
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(30, 200, 120)).save(buffer, format="GIF")
    return buffer.getvalue()


async def upload_image(
    client: AsyncClient, headers: dict[str, str], *, name: str, data: bytes, mime: str = "image/png"
) -> dict:
    response = await client.post(UPLOAD_URL, files=_files(name, data, mime), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _today_row(client: AsyncClient, admin_headers: dict[str, str]) -> dict:
    rows = (await client.get(STATS_URL, headers=admin_headers)).json()
    return rows[-1]


# ======================================================================
# 1.6 多尺寸图片
# ======================================================================


class TestImageVariantBoundaries:
    async def test_gif_skips_variants_entirely(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """GIF 是动图：抽帧生成静态变体会丢失动画，必须整体跳过。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"anim-{unique_suffix()}.gif",
            data=make_gif_bytes((1920, 1080)),
            mime="image/gif",
        )
        assert body["kind"] == "image"
        assert body["variants"] == []

    async def test_middle_tiers_only_for_1000px(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """1000px 宽：只生成 480/800 两档（1600 会放大，必须跳过）。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"mid-{unique_suffix()}.png",
            data=make_png_bytes((1000, 600)),
        )
        assert [v["width"] for v in body["variants"]] == [480, 800]

    async def test_width_equal_to_tier_is_not_upscaled(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """800px 宽：等于档位也跳过（`width <= target` 边界），只剩 480 一档。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"eq-{unique_suffix()}.png",
            data=make_png_bytes((800, 400)),
        )
        assert [v["width"] for v in body["variants"]] == [480]

    async def test_variant_naming_shares_one_stem(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """三档共用同一 stem、仅宽度后缀不同——便于按一组资产统一管理。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"stem-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        stems = set()
        for variant in body["variants"]:
            filename = variant["url"].rsplit("/", 1)[-1]
            match = re.fullmatch(r"([0-9a-f]{8})-(\d+)\.(avif|webp)", filename)
            assert match, f"变体命名不符合 {{stem}}-{{width}}{{ext}}：{filename}"
            assert int(match.group(2)) == variant["width"]
            stems.add(match.group(1))
        assert len(stems) == 1, f"三档应共用同一 stem，实际 {stems}"

    async def test_variant_prefers_avif_when_encoder_available(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """有 AVIF 编码器时必须用 AVIF，且文件真实可解码、宽度与档位一致。"""
        has_avif = features.check("avif")
        body = await upload_image(
            client,
            author_headers,
            name=f"avif-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        expected_ext = ".avif" if has_avif else ".webp"
        for variant in body["variants"]:
            assert variant["url"].endswith(expected_ext), variant["url"]
            response = await client.get(variant["url"])
            assert response.status_code == 200
            decoded = Image.open(io.BytesIO(response.content))
            assert decoded.width == variant["width"]
        if not has_avif:
            pytest.skip("本机 Pillow 无 AVIF 编码器（降级分支由下一个用例强制覆盖）")

    async def test_variant_falls_back_to_webp_without_avif_encoder(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """强制关闭 AVIF 能力：必须降级为 WEBP 而不是报错或产出坏文件。"""
        monkeypatch.setattr("app.services.attachment_service.features.check", lambda _name: False)
        body = await upload_image(
            client,
            author_headers,
            name=f"webp-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        assert [v["width"] for v in body["variants"]] == [480, 800, 1600]
        for variant in body["variants"]:
            assert variant["url"].endswith(".webp")
            response = await client.get(variant["url"])
            assert response.status_code == 200
            assert Image.open(io.BytesIO(response.content)).format == "WEBP"

    async def test_delete_also_removes_thumbnail(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """删除要把缩略图一起清掉，否则磁盘只进不出。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"thumb-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        assert (await client.get(body["thumbnail_url"])).status_code == 200
        assert (
            await client.delete(f"/api/v1/attachments/{body['id']}", headers=author_headers)
        ).status_code == 200
        assert (await client.get(body["thumbnail_url"])).status_code == 404


class TestBackfillBoundaries:
    @staticmethod
    async def _clear_variants(attachment_id: int) -> None:
        async with async_session_factory() as session:
            record = await session.get(Attachment, attachment_id)
            assert record is not None
            record.variants = None
            await session.commit()

    @staticmethod
    async def _original_path(attachment_id: int) -> Path:
        """推导附件原文件在磁盘上的路径。"""
        async with async_session_factory() as session:
            record = await session.get(Attachment, attachment_id)
            assert record is not None
            return (
                settings.upload_dir
                / record.created_at.strftime("%Y%m")
                / Path(record.stored_name).name
            )

    @classmethod
    async def _rm_original(cls, attachment_id: int) -> Path:
        """删掉磁盘上的原文件（模拟存储损坏/外部清理）。"""
        path = await cls._original_path(attachment_id)
        path.unlink(missing_ok=True)
        return path

    async def test_limit_validation_bounds(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        for bad in (0, 101):
            response = await client.post(f"{BACKFILL_URL}?limit={bad}", headers=admin_headers)
            assert response.status_code == 422, f"limit={bad} 应被拒绝"
        assert (
            await client.post(f"{BACKFILL_URL}?limit=1", headers=admin_headers)
        ).status_code == 200

    async def test_skips_when_original_file_missing(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """原文件缺失：记 skipped、不 500、记录保持 NULL 以便下次重试。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"missing-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        await self._clear_variants(body["id"])
        removed = await self._rm_original(body["id"])
        assert not removed.exists()

        result = (await client.post(BACKFILL_URL, headers=admin_headers)).json()
        assert result["skipped"] >= 1
        assert result["processed"] >= 1

        async with async_session_factory() as session:
            record = await session.get(Attachment, body["id"])
            assert record is not None and record.variants is None

    async def test_skips_when_original_file_corrupted(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """原文件存在但内容已损坏：跳过并保持 NULL，绝不能 500 打断整轮回填。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"corrupt-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        await self._clear_variants(body["id"])
        path = await self._original_path(body["id"])
        path.write_bytes(b"this is not a real image at all")

        response = await client.post(BACKFILL_URL, headers=admin_headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["skipped"] >= 1
        async with async_session_factory() as session:
            record = await session.get(Attachment, body["id"])
            assert record is not None and record.variants is None

    async def test_backfill_never_converges_while_small_images_exist(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """小图/GIF 的 variants 落库即 NULL（永远填不上），因此每轮都会被重新「处理」。

        这直接推翻前端提示「可反复调用直到 processed 为 0」——只要库里存在小图
        或 GIF，processed 永远不会归零。
        """
        small = await upload_image(
            client,
            author_headers,
            name=f"tiny-{unique_suffix()}.png",
            data=make_png_bytes((120, 90)),
        )
        assert small["variants"] == []

        first = (await client.post(BACKFILL_URL, headers=admin_headers)).json()
        second = (await client.post(BACKFILL_URL, headers=admin_headers)).json()
        assert first["processed"] >= 1 and first["updated"] == 0
        assert second["processed"] >= 1, "第二轮仍被重复处理：回填不收敛"

    async def test_fixed_limit_can_starve_real_legacy_image(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """批额度被「永远回填不了」的记录占满时，真正的存量大图会被饿死。

        ``list_images_without_variants`` 是 ``WHERE variants IS NULL ORDER BY id LIMIT n``，
        而小图/GIF 的 variants 恒为 NULL，会一直占据每批的名额。
        """
        small = await upload_image(
            client,
            author_headers,
            name=f"starve-small-{unique_suffix()}.png",
            data=make_png_bytes((120, 90)),
        )
        wide = await upload_image(
            client,
            author_headers,
            name=f"starve-wide-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        assert small["id"] < wide["id"]  # 小图 id 更小，必然先被选中
        await self._clear_variants(wide["id"])  # 大图模拟成「功能上线前的存量图」

        result = (await client.post(f"{BACKFILL_URL}?limit=1", headers=admin_headers)).json()
        assert result["processed"] == 1 and result["updated"] == 0
        async with async_session_factory() as session:
            record = await session.get(Attachment, wide["id"])
            assert record is not None
            assert record.variants is None, "存量大图被小图永久挤占名额（饿死）"

    async def test_gif_is_selected_then_skipped(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """GIF 落库时 variants 为 NULL，必然被回填筛选命中，然后因不支持而 skipped。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"gifback-{unique_suffix()}.gif",
            data=make_gif_bytes((1600, 900)),
            mime="image/gif",
        )
        async with async_session_factory() as session:
            record = await session.get(Attachment, body["id"])
            assert record is not None and record.variants is None

        result = (await client.post(BACKFILL_URL, headers=admin_headers)).json()
        assert result["processed"] >= 1
        assert result["skipped"] >= 1

    async def test_list_api_exposes_ascending_variant_urls(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """媒体库列表也必须带变体 URL（前端靠它渲染），且按宽度升序、前缀为 /media。"""
        body = await upload_image(
            client,
            author_headers,
            name=f"listv-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        library = (await client.get("/api/v1/attachments", headers=author_headers)).json()
        mine = next(item for item in library["items"] if item["id"] == body["id"])
        widths = [v["width"] for v in mine["variants"]]
        assert widths == sorted(widths) == [480, 800, 1600]
        for variant in mine["variants"]:
            assert variant["url"].startswith("/media/uploads/")
            assert (await client.get(variant["url"])).status_code == 200


class TestCoverVariantsIntegration:
    """封面变体与文章域的集成：列表/详情都要带 cover_variants，外链要安全跳过。"""

    async def test_article_list_and_detail_carry_cover_variants(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        image = await upload_image(
            client,
            author_headers,
            name=f"cover-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        created = await client.post(
            "/api/v1/articles",
            json=make_article_payload(cover_image=image["url"]),
            headers=author_headers,
        )
        assert created.status_code == 201, created.text
        article = created.json()

        listing = (await client.get("/api/v1/articles?page_size=50")).json()
        in_list = next(item for item in listing["items"] if item["id"] == article["id"])
        assert [v["width"] for v in in_list["cover_variants"]] == [480, 800, 1600]

        detail = (await client.get(f"/api/v1/articles/{article['slug']}")).json()
        assert [v["width"] for v in detail["cover_variants"]] == [480, 800, 1600]
        for variant in detail["cover_variants"]:
            assert (await client.get(variant["url"])).status_code == 200

    async def test_external_cover_image_has_no_variants(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """外链封面不属于本站存储，不应去查库、也不能报错。"""
        created = await client.post(
            "/api/v1/articles",
            json=make_article_payload(cover_image="https://cdn.example.com/cover.png"),
            headers=author_headers,
        )
        assert created.status_code == 201, created.text
        article = created.json()
        detail = (await client.get(f"/api/v1/articles/{article['slug']}")).json()
        assert detail["cover_variants"] == []


# ======================================================================
# 1.4 统计趋势
# ======================================================================


class TestStatsBoundaries:
    async def test_days_lower_and_upper_bounds_are_valid(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        assert len((await client.get(f"{STATS_URL}?days=1", headers=admin_headers)).json()) == 1
        assert len((await client.get(f"{STATS_URL}?days=90", headers=admin_headers)).json()) == 90

    async def test_days_non_integer_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        for bad in ("abc", "1.5", ""):
            response = await client.get(f"{STATS_URL}?days={bad}", headers=admin_headers)
            assert response.status_code == 422, f"days={bad!r} 应被拒绝"

    async def test_missing_article_is_not_recorded(
        self, client: AsyncClient, published_article: dict, admin_headers: dict[str, str]
    ) -> None:
        """404 详情页不产生访问日志——统计口径是"成功阅读"。"""
        assert (await client.get("/api/v1/articles/no-such-slug-xyz")).status_code == 404
        await client.get(f"/api/v1/articles/{published_article['slug']}")
        today = await _today_row(client, admin_headers)
        assert today["views"] == 1

    async def test_multiple_articles_aggregate_into_one_day(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        first = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=author_headers
            )
        ).json()
        second = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=author_headers
            )
        ).json()
        for _ in range(2):
            await client.get(f"/api/v1/articles/{first['slug']}")
        await client.get(f"/api/v1/articles/{second['slug']}")

        today = await _today_row(client, admin_headers)
        assert today["views"] == 3
        assert today["unique_visitors"] == 1

    async def test_distinct_ips_count_distinct_uv(
        self,
        client: AsyncClient,
        published_article: dict,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """两个不同来源 IP：PV=3、UV=2（验证 ip_hash 的 distinct 真的生效）。

        ``trust_proxy_headers`` 默认关闭（防伪造），这里显式打开来注入
        不同来源 IP——这正是该开关存在的意义。
        """
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        for ip in ("203.0.113.7", "203.0.113.9", "203.0.113.7"):
            response = await client.get(
                f"/api/v1/articles/{published_article['slug']}",
                headers={"X-Forwarded-For": ip},
            )
            assert response.status_code == 200

        today = await _today_row(client, admin_headers)
        assert today["views"] == 3
        assert today["unique_visitors"] == 2

    async def test_visit_logs_cascade_when_article_deleted(
        self, client: AsyncClient, published_article: dict, admin_headers: dict[str, str]
    ) -> None:
        """删文章必须连带清掉它的访问日志（FK ON DELETE CASCADE + PRAGMA foreign_keys=ON）。"""
        await client.get(f"/api/v1/articles/{published_article['slug']}")
        assert (await _today_row(client, admin_headers))["views"] == 1

        assert (
            await client.delete(
                f"/api/v1/articles/{published_article['id']}", headers=admin_headers
            )
        ).status_code == 204
        assert (await _today_row(client, admin_headers))["views"] == 0

    async def test_series_is_iso_dates_ascending_ending_today(
        self, client: AsyncClient, published_article: dict, admin_headers: dict[str, str]
    ) -> None:
        await client.get(f"/api/v1/articles/{published_article['slug']}")
        rows = (await client.get(f"{STATS_URL}?days=30", headers=admin_headers)).json()
        assert len(rows) == 30
        dates = [row["date"] for row in rows]
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates)
        assert dates == sorted(dates)
        assert dates[-1] == datetime.now(UTC).date().isoformat()
        assert dates[-1] != dates[0]


# ======================================================================
# 1.5 评论通知：token 边界与异常
# ======================================================================


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


# ======================================================================
# 1.5 评论通知：真实本地 SMTP 服务器（真实网络传输 + 真实邮件内容）
# ======================================================================


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
        await self._reply(writer, b"220 wave3-capture ESMTP ready")
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
                        b"250-wave3-capture greets you\r\n"
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


def _guest_comment(name: str, email: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "author_name": name,
        "author_email": email,
        "content": "有内容的评论",
    }
    payload.update(overrides)
    return payload


async def _post_comment(
    client: AsyncClient, article_id: int, payload: dict, headers: dict | None = None
) -> dict:
    response = await client.post(
        f"/api/v1/comments/article/{article_id}", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    await drain_notifications()
    return response.json()


async def _approve(client: AsyncClient, comment_id: int, admin_headers: dict[str, str]) -> None:
    response = await client.patch(
        f"/api/v1/comments/{comment_id}", json={"is_approved": True}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    await drain_notifications()


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
            # token 在 fragment（#）而不是 query —— 见
            # tests/test_notifications.py::TestUnsubscribeLinkFormat 的说明
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
        from tests.test_notifications import MailBox

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


# ======================================================================
# 跨功能集成
# ======================================================================


class TestCrossFeatureIntegration:
    async def test_backfill_after_variant_column_reset_keeps_urls_consistent(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author_headers: dict[str, str],
    ) -> None:
        """回填出的变体与文章封面 cover_variants 必须来自同一份数据（同一 URL 空间）。"""
        image = await upload_image(
            client,
            author_headers,
            name=f"integr-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        async with async_session_factory() as session:
            record = await session.get(Attachment, image["id"])
            assert record is not None
            record.variants = None
            await session.commit()

        await client.post(BACKFILL_URL, headers=admin_headers)

        created = await client.post(
            "/api/v1/articles",
            json=make_article_payload(cover_image=image["url"]),
            headers=author_headers,
        )
        article = created.json()
        detail = (await client.get(f"/api/v1/articles/{article['slug']}")).json()
        assert [v["width"] for v in detail["cover_variants"]] == [480, 800, 1600]
        for variant in detail["cover_variants"]:
            assert (await client.get(variant["url"])).status_code == 200
