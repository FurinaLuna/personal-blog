"""上传白名单「配置即真实行为」的回归测试。

## 防的是什么

``config.py`` 里的 ``allowed_image_types`` / ``allowed_file_types`` 曾经是全仓
**零引用**的死配置：真正生效的白名单硬编码在 ``attachment_service`` 里。后果有两层：

1. 运维改 ``ALLOWED_FILE_TYPES`` 环境变量毫无效果——配置看着生效，实际没接上；
2. 两处列表互相漂移：服务层允许 BMP 而 config 没有，config 里的
   ``application/octet-stream`` 又从来没有真正放行过任何扩展名。

改造后白名单统一由 ``settings`` 提供。这批用例锁死两件事：

1. **默认行为不变**：白名单内容与改造前的硬编码逐项一致（含此前被 config 漏掉的 BMP）；
2. **配置真的生效**：往配置里加一个类型，该类型的文件就必须能被接受——
   只断言「服务读到了 settings」是不够的，要落到真实的上传结果上。
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient, Response
from PIL import Image

from app.config import Settings, settings
from app.services.attachment_service import AttachmentService

# 改造前服务层硬编码的白名单，逐项照抄过来当基线
LEGACY_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "AVIF", "BMP"}
LEGACY_FILE_EXTENSIONS = {
    ".pdf",
    ".zip",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".docx",
    ".xlsx",
    ".pptx",
    ".epub",
}


def _files(name: str, content: bytes, mime: str) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, content, mime)}


def _tiff_bytes(size: tuple[int, int] = (120, 90)) -> bytes:
    buffer = io.BytesIO()
    with Image.new("RGB", size, (12, 34, 56)) as image:
        image.save(buffer, format="TIFF")
    return buffer.getvalue()


def _bmp_bytes(size: tuple[int, int] = (120, 90)) -> bytes:
    buffer = io.BytesIO()
    with Image.new("RGB", size, (200, 120, 60)) as image:
        image.save(buffer, format="BMP")
    return buffer.getvalue()


async def _upload(
    client: AsyncClient, headers: dict[str, str], name: str, content: bytes, mime: str
) -> Response:
    return await client.post(
        "/api/v1/attachments/upload", files=_files(name, content, mime), headers=headers
    )


class TestDefaultWhitelistUnchanged:
    """默认行为必须与改造前逐项一致（这是本次改动的安全底线）。"""

    def test_default_image_formats_match_legacy_hardcode(self) -> None:
        assert set(AttachmentService._allowed_image_formats()) == LEGACY_IMAGE_FORMATS

    def test_default_file_extensions_match_legacy_hardcode(self) -> None:
        assert set(AttachmentService._allowed_file_extensions()) == LEGACY_FILE_EXTENSIONS

    async def test_bmp_is_still_accepted(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """BMP 此前被服务层允许、却被 config 漏掉——统一到实际行为后仍然允许。"""
        response = await _upload(client, author_headers, "pic.bmp", _bmp_bytes(), "image/bmp")
        assert response.status_code == 201, response.text
        assert response.json()["url"].endswith(".bmp")

    async def test_octet_stream_never_widened_the_whitelist(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """旧 config 里的 ``application/octet-stream`` 是一张空头支票。

        它挂在 MIME 列表里，但真实判定只看扩展名，所以它从来没有放行过任何东西。
        改造后把它删掉，`.exe` 依旧被拒——行为不变。
        """
        response = await _upload(
            client, author_headers, "payload.exe", b"MZ\x90\x00", "application/octet-stream"
        )
        assert response.status_code == 415


class TestEnvVarReachesSettings:
    """环境变量 -> Settings 这一段必须通（CSV 写法由 ``_split_csv`` 解析）。"""

    def test_file_types_are_parsed_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOWED_FILE_TYPES", ".pdf, .log")
        assert Settings(_env_file=None).allowed_file_types == [".pdf", ".log"]

    def test_image_types_are_parsed_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOWED_IMAGE_TYPES", '["image/png","image/tiff"]')
        assert Settings(_env_file=None).allowed_image_types == ["image/png", "image/tiff"]


class TestConfigActuallyDrivesBehavior:
    """改配置必须改变上传结果——这才是「不是死配置」的判据。"""

    async def test_newly_allowed_extension_is_accepted(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """白名单里加 ``.log`` 后，.log 必须真的能传上去。"""
        denied = await _upload(client, author_headers, "notes.log", b"hello", "text/plain")
        assert denied.status_code == 415, "默认白名单不该放行 .log"

        monkeypatch.setattr(settings, "allowed_file_types", [*settings.allowed_file_types, ".log"])
        allowed = await _upload(client, author_headers, "notes.log", b"hello", "text/plain")
        assert allowed.status_code == 201, allowed.text
        assert allowed.json()["url"].endswith(".log")

    async def test_removed_extension_is_rejected(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """反向：从白名单里拿掉 .pdf 后，PDF 必须被拒（收紧也要真的生效）。"""
        monkeypatch.setattr(
            settings, "allowed_file_types", [".zip", ".txt", ".md", ".csv", ".json"]
        )
        response = await _upload(client, author_headers, "doc.pdf", b"%PDF-1.4", "application/pdf")
        assert response.status_code == 415

    async def test_newly_allowed_image_type_is_accepted(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """白名单里加 ``image/tiff`` 后，TIFF 必须真的能传上去。"""
        denied = await _upload(client, author_headers, "scan.tiff", _tiff_bytes(), "image/tiff")
        assert denied.status_code == 415, "默认白名单不该放行 TIFF"

        monkeypatch.setattr(
            settings, "allowed_image_types", [*settings.allowed_image_types, "image/tiff"]
        )
        allowed = await _upload(client, author_headers, "scan.tiff", _tiff_bytes(), "image/tiff")
        assert allowed.status_code == 201, allowed.text
        body = allowed.json()
        assert body["kind"] == "image"
        assert body["url"].endswith(".tif")

    async def test_client_content_type_still_not_trusted(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """放行 MIME 不等于相信客户端：把文本声明成 image/tiff 仍应被 Pillow 拦下。

        这条防的是「白名单改成配置驱动之后，有人顺手改成直接比对
        ``upload.content_type``」——那是把判型权交回给攻击者。
        """
        monkeypatch.setattr(
            settings, "allowed_image_types", [*settings.allowed_image_types, "image/tiff"]
        )
        response = await _upload(
            client, author_headers, "fake.tiff", b"not a tiff at all", "image/tiff"
        )
        assert response.status_code == 415
