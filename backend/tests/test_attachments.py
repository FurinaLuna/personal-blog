"""附件上传测试：真实解码、白名单、限流、权限。"""

from __future__ import annotations

from httpx import AsyncClient

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
