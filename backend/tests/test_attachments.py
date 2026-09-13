"""附件上传测试：真实解码、白名单、限流、权限、多尺寸变体。"""

from __future__ import annotations

from httpx import AsyncClient

from app.db.session import async_session_factory
from app.models import Attachment
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
