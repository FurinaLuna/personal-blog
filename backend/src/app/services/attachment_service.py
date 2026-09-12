"""附件服务：上传校验、缩略图、删除。

安全设计（这是全站最容易出事的地方，逐条说清楚）：

1. **不信任客户端声明**。``UploadFile.content_type`` 与文件名都能随便伪造，
   所以判定「是不是图片」靠 Pillow 真解一遍，不靠 ``Content-Type``。
2. **流式限流读**。边读边累加字节数，超限立刻中断，不会等整个文件进内存才发现太大。
3. **服务端生成文件名**。用 UUID 落盘，用户原始名只存进数据库用于展示，
   从根上消除路径穿越（``../../etc/passwd``）与重名覆盖。
4. **扩展名白名单**。普通附件只允许 pdf/zip/txt/md 这类，禁掉 html/js/svg——
   这些文件一旦被浏览器当同源脚本执行，就是存储型 XSS。
5. **像素炸弹防护**。拒绝超大像素图，避免 Pillow 解码时把内存吃光。
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Attachment, User, UserRole
from app.repositories import AttachmentRepository
from app.schemas.attachment import UploadResult
from app.schemas.common import Page, PageParams
from app.utils.exceptions import (
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
)
from app.utils.storage import storage
from app.utils.text import short_uuid, truncate

# Pillow 能识别并且我们允许的图片格式 -> 落盘扩展名
ALLOWED_IMAGE_FORMATS: dict[str, str] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "WEBP": ".webp",
    "AVIF": ".avif",
    "BMP": ".bmp",
}
# 普通附件允许的扩展名白名单（可执行/可被浏览器执行的一律不放进来）
ALLOWED_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".zip", ".txt", ".md", ".csv", ".json", ".docx", ".xlsx", ".pptx", ".epub"}
)
# 单张图片最大像素数（约 5000 万），超过直接拒绝
MAX_IMAGE_PIXELS = 50_000_000
READ_CHUNK = 512 * 1024
_EXT_PATTERN = re.compile(r"^\.[a-z0-9]{1,8}$")


class AttachmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.attachments = AttachmentRepository(session)

    # ---------------------------------------------------------------- 上传

    async def upload(self, *, upload, uploader: User) -> UploadResult:
        """处理一次上传。

        Args:
            upload: FastAPI 的 ``UploadFile``。
            uploader: 当前登录用户（作者或站长）。

        Raises:
            PayloadTooLargeError: 超过 ``MAX_UPLOAD_SIZE``。
            UnsupportedMediaTypeError: 图片解不开，或附件扩展名不在白名单内。
        """
        original_name = Path(upload.filename or "unnamed").name or "unnamed"
        data = await self._read_limited(upload)

        probed = self._probe_image(data)
        if probed is not None:
            extension, width, height = probed
            kind = "image"
        else:
            extension = self._validate_file(original_name)
            width = height = 0
            kind = "file"

        subdir = f"uploads/{datetime.now(UTC).strftime('%Y%m')}"
        stored_name = f"{_rand_token()}{extension}"
        await storage.save(subdir=subdir, stored_name=stored_name, data=data)

        thumbnail_name: str | None = None
        thumbnail_url: str | None = None
        if kind == "image":
            thumb = self._make_thumbnail(data)
            if thumb is not None:
                thumbnail_name = f"thumb-{Path(stored_name).stem}.webp"
                await storage.save(subdir=subdir, stored_name=thumbnail_name, data=thumb)
                thumbnail_url = storage.url_for(subdir=subdir, stored_name=thumbnail_name)

        url = storage.url_for(subdir=subdir, stored_name=stored_name)
        record = await self.attachments.create(
            stored_name=stored_name,
            original_name=truncate(original_name, 200),
            mime_type=upload.content_type or "application/octet-stream",
            size=len(data),
            kind=kind,
            width=width or None,
            height=height or None,
            thumbnail_name=thumbnail_name,
            url=url,
            thumbnail_url=thumbnail_url,
            uploader_id=uploader.id,
        )
        return self._to_result(record, kind=kind)

    async def _read_limited(self, upload) -> bytes:
        """边读边计数，超限立即抛错——避免先落盘再检查导致磁盘被写满。"""
        buffer = bytearray()
        limit = settings.max_upload_size
        while chunk := await upload.read(READ_CHUNK):
            buffer.extend(chunk)
            if len(buffer) > limit:
                raise PayloadTooLargeError(f"文件超过上限 {limit // 1024 // 1024} MB")
        if not buffer:
            raise UnsupportedMediaTypeError("上传内容为空")
        return bytes(buffer)

    @staticmethod
    def _probe_image(data: bytes) -> tuple[str, int, int] | None:
        """尝试按图片解码。

        Returns:
            ``(扩展名, 宽, 高)``；不是图片返回 ``None``。
        Raises:
            UnsupportedMediaTypeError: 是图片但格式不在白名单，或像素数超限。
        """
        try:
            with Image.open(io.BytesIO(data)) as image:
                image_format = (image.format or "").upper()
                width, height = image.size
                # 先读尺寸再校验，避免 verify() 之后对象不可用
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise UnsupportedMediaTypeError(f"不支持的图片格式：{image_format or '未知'}")
                if width * height > MAX_IMAGE_PIXELS:
                    raise UnsupportedMediaTypeError("图片分辨率过大，请压缩后再上传")
                return ALLOWED_IMAGE_FORMATS[image_format], width, height
        except UnidentifiedImageError:
            return None
        except OSError as exc:  # 损坏的图片文件
            raise UnsupportedMediaTypeError("图片文件已损坏，无法解析") from exc

    @staticmethod
    def _validate_file(original_name: str) -> str:
        """校验普通附件的扩展名是否在白名单内，返回小写扩展名。

        Raises:
            UnsupportedMediaTypeError: 扩展名缺失、格式非法或不在白名单内。
        """
        extension = Path(original_name).suffix.lower()
        if not _EXT_PATTERN.match(extension) or extension not in ALLOWED_FILE_EXTENSIONS:
            allowed = "、".join(sorted(ALLOWED_FILE_EXTENSIONS))
            raise UnsupportedMediaTypeError(f"该类型不允许上传，仅支持：{allowed}")
        return extension

    @staticmethod
    def _make_thumbnail(data: bytes) -> bytes | None:
        """生成 WEBP 缩略图。

        WEBP 同时支持透明通道与高压缩率，比「PNG 太大 / JPEG 丢透明」更合适。
        生成失败不阻断上传主流程——缩略图只是优化项。
        """
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.thumbnail(
                    (settings.thumbnail_max_width, settings.thumbnail_max_height),
                    resample=Image.Resampling.LANCZOS,
                )
                # 调色板 / CMYK 等模式无法直接存 WEBP，统一转 RGB(A)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                out = io.BytesIO()
                image.save(out, format="WEBP", quality=82, method=4)
                return out.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            return None

    @staticmethod
    def _to_result(record: Attachment, *, kind: str) -> UploadResult:
        markdown = (
            f"![{Path(record.original_name).stem}]({record.url})"
            if kind == "image"
            else f"[{record.original_name}]({record.url})"
        )
        return UploadResult(
            id=record.id,
            original_name=record.original_name,
            mime_type=record.mime_type,
            size=record.size,
            kind=record.kind,
            width=record.width,
            height=record.height,
            url=record.url,
            thumbnail_url=record.thumbnail_url,
            created_at=record.created_at,
            markdown=markdown,
        )

    # ---------------------------------------------------------------- 查询 / 删除

    async def list_paged(
        self, *, page_params: PageParams, kind: str | None = None, viewer: User
    ) -> Page[UploadResult]:
        """媒体库列表。作者只看自己的，站长看全站。"""
        uploader_id = None if viewer.role is UserRole.ADMIN else viewer.id
        total = await self.attachments.count(kind=kind, uploader_id=uploader_id)
        rows = await self.attachments.list_paged(
            offset=page_params.offset, limit=page_params.limit, kind=kind, uploader_id=uploader_id
        )
        items = [self._to_result(row, kind=row.kind) for row in rows]
        return Page.build(items, total, page_params.page, page_params.page_size)

    async def delete(self, attachment_id: int, *, operator: User) -> None:
        """删除附件：先删磁盘再删记录。

        顺序不能反——先删记录而磁盘文件删除失败的话，文件就成了永远找不到的
        垃圾（占空间且没人知道它是什么）；反过来最多留一条指向空文件的记录，
        可以在管理页里发现并清理。
        """
        record = await self.attachments.get(attachment_id)
        if record is None:
            raise NotFoundError("附件不存在")
        if operator.role is not UserRole.ADMIN and record.uploader_id != operator.id:
            raise PermissionDeniedError("只能删除自己上传的文件")

        subdir = f"uploads/{record.created_at.strftime('%Y%m')}"
        await storage.delete(subdir=subdir, stored_name=record.stored_name)
        if record.thumbnail_name:
            await storage.delete(subdir=subdir, stored_name=record.thumbnail_name)
        await self.attachments.delete(record)


def _rand_token() -> str:
    """生成 8 位随机文件名主体。

    刻意用纯 ASCII 随机串而不是「时间戳 + 中文原名」：中文文件名在不同操作系统
    与反向代理上的 URL 编码行为并不一致，而原始文件名本来就完整存在数据库里，
    不需要靠文件名来承载信息。
    """
    return short_uuid()[:8]
