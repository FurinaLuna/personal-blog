"""附件上传测试：真实解码、白名单、限流、权限、多尺寸变体。"""

from __future__ import annotations

import struct
import zlib

import pytest
from httpx import AsyncClient

from app.db.session import async_session_factory
from app.models import Attachment
from app.services.attachment_service import MAX_IMAGE_PIXELS, AttachmentService
from app.utils.exceptions import UnsupportedMediaTypeError
from tests.factories import make_jpeg_bytes, make_png_bytes, unique_suffix


def _files(name: str, content: bytes, mime: str) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, content, mime)}


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

    @staticmethod
    async def _clear_variants(attachment_id: int) -> None:
        """把变体列抹回 NULL，模拟功能上线前的存量数据。"""
        async with async_session_factory() as session:
            record = await session.get(Attachment, attachment_id)
            assert record is not None
            record.variants = None
            await session.commit()

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
        await self._clear_variants(body["id"])

        result = (
            await client.post("/api/v1/attachments/backfill-variants", headers=admin_headers)
        ).json()
        assert result["processed"] >= 1
        assert result["updated"] >= 1

        library = (await client.get("/api/v1/attachments", headers=author_headers)).json()
        mine = next(item for item in library["items"] if item["id"] == body["id"])
        assert [variant["width"] for variant in mine["variants"]] == [480, 800, 1600]

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
        await self._clear_variants(body["id"])

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
