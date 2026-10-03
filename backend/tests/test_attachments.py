"""附件上传测试：真实解码、白名单、限流、权限、多尺寸变体。"""

from __future__ import annotations

import io
import re
import struct
import zlib
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image, features

from app.config import settings
from app.db.session import async_session_factory
from app.models import Attachment
from app.services.attachment_service import MAX_IMAGE_PIXELS, AttachmentService
from app.utils.exceptions import UnsupportedMediaTypeError
from tests.factories import make_article_payload, make_jpeg_bytes, make_png_bytes, unique_suffix

BACKFILL_URL = "/api/v1/attachments/backfill-variants"
UPLOAD_URL = "/api/v1/attachments/upload"


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


async def _clear_variants(attachment_id: int) -> None:
    """把变体列抹回 NULL，模拟功能上线前的存量数据。"""
    async with async_session_factory() as session:
        record = await session.get(Attachment, attachment_id)
        assert record is not None
        record.variants = None
        await session.commit()


class TestUploadImage:
    async def test_png_upload_returns_dimensions_and_thumbnail(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("photo.png", make_png_bytes((120, 90)), "image/png"),
            headers=author_headers,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == "image"
        assert (body["width"], body["height"]) == (120, 90)
        assert body["url"].startswith("/media/uploads/")
        assert body["thumbnail_url"] and body["thumbnail_url"].endswith(".webp")
        assert body["markdown"] == f"![photo]({body['url']})"

    async def test_jpeg_extension_is_normalized(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("pic.JPEG", make_jpeg_bytes(), "image/jpeg"),
            headers=author_headers,
        )
        assert response.status_code == 201
        assert response.json()["url"].endswith(".jpg")

    async def test_uploaded_file_is_actually_served(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """上传完还要能访问到——只入库不落盘是最容易漏掉的一环。"""
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("serve.png", make_png_bytes(), "image/png"),
                headers=author_headers,
            )
        ).json()
        fetched = await client.get(body["url"])
        assert fetched.status_code == 200
        assert fetched.content == make_png_bytes()

    async def test_client_declared_type_is_not_trusted(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """把文本伪装成 image/png 上传，必须被 Pillow 解码这一步拦下。"""
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("fake.png", b"this is definitely not a png", "image/png"),
            headers=author_headers,
        )
        assert response.status_code == 415

    async def test_renamed_svg_is_rejected(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """SVG 即便改名成 .png 也拒——它会变成存储型 XSS。"""
        payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("evil.png", payload, "image/svg+xml"),
            headers=author_headers,
        )
        assert response.status_code == 415


class TestImageDecompressionBomb:
    """图片解压炸弹防护（``MAX_IMAGE_PIXELS``）的单元测试。

    为什么必须补这一层：这条防护此前只有 E2E 覆盖（8000x8000 的 PNG 被 415 拒）。
    E2E 不在常规回归里跑，等于「改坏了也没人知道」——防护逻辑一旦在重构中
    被删掉，pytest 照样全绿。

    构造手法是**只声明尺寸、不真的铺像素**：Pillow 的 ``Image.open`` 只解析
    IHDR 头就返回尺寸，内存分配发生在 ``load()``，所以整个用例耗时毫秒级、
    峰值内存和一张普通小图一样。
    """

    @staticmethod
    def _png_declaring_size(width: int, height: int) -> bytes:
        """手工拼一个「IHDR 声明超大尺寸、文件本身只有几十字节」的 PNG。

        **为什么它一定命中像素上限分支**（不是碰巧 415）：
        - Pillow 读 IHDR 得到 ``(width, height)`` 且 ``format == "PNG"``；
        - PNG 在默认图片白名单里，所以「不支持的图片格式」那一支**不会**触发；
        - 于是唯一可能抛 ``UnsupportedMediaTypeError`` 的就是
          ``width * height > MAX_IMAGE_PIXELS`` 这一支。

        尺寸必须卡在两者之间：大于本服务上限 ``MAX_IMAGE_PIXELS``（5000 万），
        但低于 Pillow 自带的 ``Image.MAX_IMAGE_PIXELS``（约 8948 万，超限会在
        ``Image.open`` 阶段直接抛 ``DecompressionBombError``）——
        否则被 Pillow 挡在前面，测的就不是本服务的防护了。
        """

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        # bit depth 8、颜色类型 0（灰度）、非隔行；IDAT 内容不合法无所谓，
        # 因为判定发生在解码之前，根本不会去读它
        header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00"))
            + chunk(b"IEND", b"")
        )

    def test_bomb_payload_is_tiny(self) -> None:
        """先自证构造物确实小：这条防护的测试本身不能变成资源炸弹。"""
        assert len(self._png_declaring_size(9000, 6000)) < 200

    def test_probe_rejects_image_over_pixel_limit(self) -> None:
        """5400 万像素（> 5000 万上限）必须被拒，且报错来自像素分支。"""
        data = self._png_declaring_size(9000, 6000)
        with pytest.raises(UnsupportedMediaTypeError, match="分辨率过大"):
            AttachmentService._probe_image(data)

    def test_pixel_limit_boundary_is_inclusive(self) -> None:
        """边界：恰好等于上限的图仍应放行（判定是 ``>`` 而不是 ``>=``）。

        用 5000x10000 = 50_000_000 正好等于 ``MAX_IMAGE_PIXELS``。
        """
        data = self._png_declaring_size(5000, MAX_IMAGE_PIXELS // 5000)
        probed = AttachmentService._probe_image(data)
        assert probed is not None, "恰好等于上限的图不应被拒"
        extension, width, height = probed
        assert (width, height) == (5000, MAX_IMAGE_PIXELS // 5000)
        assert extension == ".png"

    async def test_upload_api_rejects_pixel_bomb(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """端到端同样被 415，且 detail 指向分辨率——证明走的确实是这一支。"""
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("bomb.png", self._png_declaring_size(9000, 6000), "image/png"),
            headers=author_headers,
        )
        assert response.status_code == 415
        assert response.json()["code"] == "unsupported_media_type"
        assert "分辨率过大" in response.json()["detail"]


class TestUploadFile:
    async def test_pdf_is_accepted_as_generic_file(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("doc.pdf", b"%PDF-1.4\n%fake but fine", "application/pdf"),
            headers=author_headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "file"
        assert body["markdown"].startswith("[doc.pdf]")

    async def test_executable_extension_rejected(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("payload.exe", b"MZ\x90\x00", "application/octet-stream"),
            headers=author_headers,
        )
        assert response.status_code == 415

    async def test_html_extension_rejected(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """HTML 被同源直出就是 XSS，必须挡在扩展名白名单外。"""
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("page.html", b"<html><script>alert(1)</script></html>", "text/html"),
            headers=author_headers,
        )
        assert response.status_code == 415


class TestUploadLimits:
    async def test_oversize_rejected_with_413(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """测试环境把上限压到 2MB，这里传 3MB。"""
        oversized = b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024)
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("big.png", oversized, "image/png"),
            headers=author_headers,
        )
        assert response.status_code == 413

    async def test_empty_file_rejected(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("empty.png", b"", "image/png"),
            headers=author_headers,
        )
        assert response.status_code == 415

    async def test_guest_cannot_upload(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/attachments/upload",
            files=_files("x.png", make_png_bytes(), "image/png"),
        )
        assert response.status_code == 401


class TestMediaLibrary:
    async def test_list_shows_only_own_uploads(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author_headers: dict[str, str],
    ) -> None:
        await client.post(
            "/api/v1/attachments/upload",
            files=_files("admin.png", make_png_bytes(), "image/png"),
            headers=admin_headers,
        )
        mine = (await client.get("/api/v1/attachments", headers=author_headers)).json()
        assert mine["total"] == 0

        all_of_them = (await client.get("/api/v1/attachments", headers=admin_headers)).json()
        assert all_of_them["total"] == 1

    async def test_kind_filter(self, client: AsyncClient, author_headers: dict[str, str]) -> None:
        await client.post(
            "/api/v1/attachments/upload",
            files=_files("a.png", make_png_bytes(), "image/png"),
            headers=author_headers,
        )
        await client.post(
            "/api/v1/attachments/upload",
            files=_files("b.pdf", b"%PDF-1.4", "application/pdf"),
            headers=author_headers,
        )
        images = (await client.get("/api/v1/attachments?kind=image", headers=author_headers)).json()
        assert images["total"] == 1
        assert images["items"][0]["kind"] == "image"

    async def test_delete_removes_file_from_disk(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files(f"del-{unique_suffix()}.png", make_png_bytes(), "image/png"),
                headers=author_headers,
            )
        ).json()
        assert (await client.get(body["url"])).status_code == 200

        assert (
            await client.delete(f"/api/v1/attachments/{body['id']}", headers=author_headers)
        ).status_code == 200
        assert (await client.get(body["url"])).status_code == 404

    async def test_cannot_delete_others_upload(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author_headers: dict[str, str],
    ) -> None:
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("admin-owned.png", make_png_bytes(), "image/png"),
                headers=admin_headers,
            )
        ).json()
        response = await client.delete(f"/api/v1/attachments/{body['id']}", headers=author_headers)
        assert response.status_code == 403

    async def test_delete_missing_404(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.delete("/api/v1/attachments/999999", headers=author_headers)
        assert response.status_code == 404


class TestImageVariants:
    """多尺寸变体：上传即生成、宽度不足不放大、删除清理、存量回填。"""

    async def test_wide_image_generates_ascending_variants(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """1920px 宽的图应生成 480/800/1600 三档，且档位升序、文件真实存在。"""
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("wide.png", make_png_bytes((1920, 1080)), "image/png"),
                headers=author_headers,
            )
        ).json()
        widths = [variant["width"] for variant in body["variants"]]
        assert widths == [480, 800, 1600]
        for variant in body["variants"]:
            assert variant["url"].endswith((".avif", ".webp"))
            assert (await client.get(variant["url"])).status_code == 200

    async def test_small_image_has_no_variants(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """宽度不足最小档位的图不放大、不变体（GIF 同理不在此测，动图语义另算）。"""
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("small.png", make_png_bytes((120, 90)), "image/png"),
                headers=author_headers,
            )
        ).json()
        assert body["variants"] == []

    async def test_delete_removes_variant_files(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """删除附件必须连同变体文件一起清掉，否则磁盘只进不出。"""
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files(
                    f"delvar-{unique_suffix()}.png", make_png_bytes((1920, 1080)), "image/png"
                ),
                headers=author_headers,
            )
        ).json()
        variant_urls = [variant["url"] for variant in body["variants"]]
        assert variant_urls

        assert (
            await client.delete(f"/api/v1/attachments/{body['id']}", headers=author_headers)
        ).status_code == 200
        for url in variant_urls:
            assert (await client.get(url)).status_code == 404


class TestVariantBackfill:
    """存量图片回填：幂等筛选、权限、真实落盘。"""

    async def test_backfill_regenerates_for_legacy_image(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("legacy.png", make_png_bytes((1920, 1080)), "image/png"),
                headers=author_headers,
            )
        ).json()
        await _clear_variants(body["id"])

        result = (
            await client.post("/api/v1/attachments/backfill-variants", headers=admin_headers)
        ).json()
        assert result["processed"] >= 1
        assert result["updated"] >= 1

        library = (await client.get("/api/v1/attachments", headers=author_headers)).json()
        mine = next(item for item in library["items"] if item["id"] == body["id"])
        assert [variant["width"] for variant in mine["variants"]] == [480, 800, 1600]

    async def test_backfill_reports_how_many_are_left(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """回填要如实报告"还剩多少"。

        前端拿这个数决定提示文案（"还剩 N 张可再次运行" vs "已全部处理完"）。
        以前没有这个字段，前端只能固定说"还剩待处理"—— 已经跑完时那句话是错的，
        用户会白跑一趟；或者拿 processed 去猜后端的分批上限，改一次上限就说谎。
        """
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("left.png", make_png_bytes((1920, 1080)), "image/png"),
                headers=author_headers,
            )
        ).json()
        await _clear_variants(body["id"])

        first = (
            await client.post("/api/v1/attachments/backfill-variants", headers=admin_headers)
        ).json()
        assert first["updated"] >= 1
        # 本轮已经处理掉它，所以没有剩下的（这个测试库里只有这一张缺变体的图）
        assert first["remaining"] == 0

        second = (
            await client.post("/api/v1/attachments/backfill-variants", headers=admin_headers)
        ).json()
        assert second["processed"] == 0
        assert second["remaining"] == 0

    async def test_backfill_is_idempotent(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        """回填后再调一轮：已生成过变体的记录不再被处理。"""
        body = (
            await client.post(
                "/api/v1/attachments/upload",
                files=_files("idem.png", make_png_bytes((1920, 1080)), "image/png"),
                headers=author_headers,
            )
        ).json()
        await _clear_variants(body["id"])

        first = (
            await client.post("/api/v1/attachments/backfill-variants", headers=admin_headers)
        ).json()
        assert first["updated"] >= 1

        second = (
            await client.post("/api/v1/attachments/backfill-variants", headers=admin_headers)
        ).json()
        assert second["processed"] == 0
        assert second["updated"] == 0

    async def test_backfill_requires_admin(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/attachments/backfill-variants", headers=author_headers
        )
        assert response.status_code == 403

    async def test_guest_cannot_backfill(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/attachments/backfill-variants")
        assert response.status_code == 401


# ----------------------------------------------------------------------
# 以下用例自「wave3 全面验证批」归并而来（原 tests/test_wave3_verify.py）：
# 覆盖 TestImageVariants / TestVariantBackfill 之外的边界、异常与集成分支。


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
    """回填的边界与异常分支（基础行为见上面的 ``TestVariantBackfill``）。"""

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
        await _clear_variants(body["id"])
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
        await _clear_variants(body["id"])
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
        await _clear_variants(wide["id"])  # 大图模拟成「功能上线前的存量图」

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


class TestCrossFeatureIntegration:
    """跨功能集成：回填出的变体与文章封面必须来自同一份 URL 空间。"""

    async def test_backfill_after_variant_column_reset_keeps_urls_consistent(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author_headers: dict[str, str],
    ) -> None:
        image = await upload_image(
            client,
            author_headers,
            name=f"integr-{unique_suffix()}.png",
            data=make_png_bytes((1920, 1080)),
        )
        await _clear_variants(image["id"])

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
