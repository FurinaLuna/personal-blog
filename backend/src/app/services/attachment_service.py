"""附件服务：上传校验、缩略图、多尺寸变体、删除。

安全设计（这是全站最容易出事的地方，逐条说清楚）：

1. **不信任客户端声明**。``UploadFile.content_type`` 与文件名都能随便伪造，
   所以判定「是不是图片」靠 Pillow 真解一遍，不靠 ``Content-Type``。
2. **流式限流读**。边读边累加字节数，超限立刻中断，不会等整个文件进内存才发现太大。
3. **服务端生成文件名**。用 UUID 落盘，用户原始名只存进数据库用于展示，
   从根上消除路径穿越（``../../etc/passwd``）与重名覆盖。
4. **扩展名白名单**。普通附件只允许 pdf/zip/txt/md 这类，禁掉 html/js/svg——
   这些文件一旦被浏览器当同源脚本执行，就是存储型 XSS。
   白名单读 ``settings.allowed_file_types``（可用环境变量覆盖），不写死在代码里。
5. **像素炸弹防护**。拒绝超大像素图，避免 Pillow 解码时把内存吃光。
"""

from __future__ import annotations

import asyncio
import io
import re
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError, features
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Attachment, User, UserRole
from app.repositories import AttachmentRepository
from app.schemas.attachment import BackfillResult, ImageVariant, UploadResult
from app.schemas.common import Page, PageParams
from app.utils.exceptions import (
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
)
from app.utils.storage import storage
from app.utils.text import short_uuid, truncate

# Pillow 格式名 -> (MIME, 落盘扩展名)。
#
# 这是**命名表**而不是白名单：真正允许哪些格式由 ``settings.allowed_image_types``
# 决定（见 ``AttachmentService._allowed_image_formats``）。之所以还要留一张表，
# 是因为落盘必须知道扩展名，而 Pillow 只给格式名（JPEG -> .jpg）。
# 表刻意比默认白名单大一圈，运维才能通过环境变量放行 TIFF / ICO 等格式；
# 表里没有的 MIME（例如 image/svg+xml）配了也不会生效——Pillow 解不出那种格式，
# 它本来就不会出现在判定路径上。
IMAGE_FORMAT_TABLE: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
    "AVIF": ("image/avif", ".avif"),
    "BMP": ("image/bmp", ".bmp"),
    "TIFF": ("image/tiff", ".tif"),
    "ICO": ("image/x-icon", ".ico"),
}
# 单张图片最大像素数（约 5000 万），超过直接拒绝
MAX_IMAGE_PIXELS = 50_000_000
READ_CHUNK = 512 * 1024
_EXT_PATTERN = re.compile(r"^\.[a-z0-9]{1,8}$")
# AVIF 的 quality=50 约相当于 JPEG 75 的体积，观感通常更好
VARIANT_QUALITY = 50
# 仅 AVIF 生效：编码速度档（1 最慢最狠压缩，10 最快），6 是体积/耗时的平衡点
VARIANT_AVIF_SPEED = 6
# 回填时单轮最多处理的图片数，防止一次请求跑太久
BACKFILL_BATCH_LIMIT = 100


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

        # 目录与 created_at 必须同源：变体 URL 读时按 record.created_at 推导子目录，
        # 若各自取当前时间，恰好跨过 UTC 月份边界（变体编码是秒级耗时）就会 404
        now = datetime.now(UTC)
        subdir = f"uploads/{now.strftime('%Y%m')}"
        stored_name = f"{_rand_token()}{extension}"
        await storage.save(subdir=subdir, stored_name=stored_name, data=data)

        thumbnail_name: str | None = None
        thumbnail_url: str | None = None
        variants: dict[str, str] = {}
        if kind == "image":
            thumb = self._make_thumbnail(data)
            if thumb is not None:
                thumbnail_name = f"thumb-{Path(stored_name).stem}.webp"
                await storage.save(subdir=subdir, stored_name=thumbnail_name, data=thumb)
                thumbnail_url = storage.url_for(subdir=subdir, stored_name=thumbnail_name)
            # 多尺寸变体：AVIF 编码是秒级 CPU 活，必须丢线程池防阻塞事件循环
            made = await asyncio.to_thread(self._make_variants, data)
            for width_label, (variant_name, payload) in made.items():
                await storage.save(subdir=subdir, stored_name=variant_name, data=payload)
                variants[width_label] = variant_name

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
            variants=variants or None,
            created_at=now,
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
                formats = AttachmentService._allowed_image_formats()
                if image_format not in formats:
                    raise UnsupportedMediaTypeError(f"不支持的图片格式：{image_format or '未知'}")
                if width * height > MAX_IMAGE_PIXELS:
                    raise UnsupportedMediaTypeError("图片分辨率过大，请压缩后再上传")
                return formats[image_format], width, height
        except UnidentifiedImageError:
            return None
        except OSError as exc:  # 损坏的图片文件
            raise UnsupportedMediaTypeError("图片文件已损坏，无法解析") from exc

    @staticmethod
    def _allowed_image_formats() -> dict[str, str]:
        """当前允许的图片格式 -> 落盘扩展名。

        白名单来自 ``settings.allowed_image_types``（MIME），每次调用都重读：
        服务层一旦在 import 期把白名单固化成常量，环境变量就再也改不动它了
        ——这正是这批白名单曾经沦为死配置的原因。
        """
        allowed = {str(mime).strip().lower() for mime in settings.allowed_image_types}
        return {
            fmt: extension
            for fmt, (mime, extension) in IMAGE_FORMAT_TABLE.items()
            if mime in allowed
        }

    @staticmethod
    def _allowed_file_extensions() -> frozenset[str]:
        """当前允许的非图片附件扩展名（同样每次都重读配置）。"""
        return frozenset(
            str(ext).strip().lower() for ext in settings.allowed_file_types if str(ext).strip()
        )

    @staticmethod
    def _validate_file(original_name: str) -> str:
        """校验普通附件的扩展名是否在白名单内，返回小写扩展名。

        Raises:
            UnsupportedMediaTypeError: 扩展名缺失、格式非法或不在白名单内。
        """
        allowed = AttachmentService._allowed_file_extensions()
        extension = Path(original_name).suffix.lower()
        if not _EXT_PATTERN.match(extension) or extension not in allowed:
            hint = "、".join(sorted(allowed))
            raise UnsupportedMediaTypeError(f"该类型不允许上传，仅支持：{hint}")
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
    def _make_variants(data: bytes) -> dict[str, tuple[str, bytes]]:
        """生成多尺寸变体，返回 ``{宽度档位: (文件名, 字节流)}``。

        规则：
        - GIF 跳过（变体会把动图压成静帧，语义就错了）；
        - 原图宽度不足的档位跳过（**不放大**：放大只会得到更糊且更大的文件）；
        - 编码优先 AVIF，运行环境没有 libavif 时降级 WEBP（两者压缩率都远好于原格式）；
        - 任何失败都返回空 dict——变体是优化项，绝不阻断上传主流程。

        纯 CPU 计算无 IO，调用方需经 ``asyncio.to_thread`` 包裹（AVIF 编码秒级）。
        """
        try:
            with Image.open(io.BytesIO(data)) as image:
                if (image.format or "").upper() == "GIF":
                    return {}
                if not features.check("avif"):
                    extension, out_format = ".webp", "WEBP"
                else:
                    extension, out_format = ".avif", "AVIF"
                stem = short_uuid()[:8]
                out: dict[str, tuple[str, bytes]] = {}
                for target in settings.image_variant_widths:
                    if image.width <= target:
                        continue
                    height = round(image.height * target / image.width)
                    resized = image.resize((target, height), Image.Resampling.LANCZOS)
                    # 调色板 / CMYK 等模式无法直接存 AVIF/WEBP，统一转 RGB(A)
                    if resized.mode not in ("RGB", "RGBA"):
                        resized = resized.convert("RGBA" if "A" in resized.getbands() else "RGB")
                    buffer = io.BytesIO()
                    save_kwargs: dict[str, object] = {"quality": VARIANT_QUALITY}
                    if out_format == "AVIF":
                        save_kwargs["speed"] = VARIANT_AVIF_SPEED
                    resized.save(buffer, format=out_format, **save_kwargs)  # type: ignore[arg-type]
                    out[str(target)] = (f"{stem}-{target}{extension}", buffer.getvalue())
                return out
        except (UnidentifiedImageError, OSError, ValueError):
            return {}

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
            variants=AttachmentService._variant_list(record),
            created_at=record.created_at,
            markdown=markdown,
        )

    @staticmethod
    def _variant_list(record: Attachment) -> list[ImageVariant]:
        """把 variants 列（宽度 -> stored_name）换算成带 URL 的升序列表。

        URL 读时计算而不是落库：换存储后端（本地磁盘 -> OSS）时不用刷数据库。
        """
        variants = record.variants or {}
        subdir = f"uploads/{record.created_at.strftime('%Y%m')}"
        return [
            ImageVariant(
                width=int(label),
                url=storage.url_for(subdir=subdir, stored_name=name),
            )
            for label, name in sorted(variants.items(), key=lambda item: int(item[0]))
        ]

    async def variant_map_by_urls(self, urls: list[str]) -> dict[str, list[ImageVariant]]:
        """把图片 URL 批量映射成变体列表（文章封面装配用）。

        封面以 URL 字符串存在 ``Article.cover_image`` 上，变体挂在 Attachment
        记录上，靠这层映射把两者接起来——一次 IN 查询，列表页不会逐条回表。
        映射不上（外链封面 / 上传早于该功能的图片）就不进 map，
        调用方取不到时落回空列表即可。
        """
        if not urls:
            return {}
        records = await self.attachments.get_by_urls(urls)
        return {record.url: self._variant_list(record) for record in records}

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
        for variant_name in (record.variants or {}).values():
            await storage.delete(subdir=subdir, stored_name=variant_name)
        await self.attachments.delete(record)

    async def backfill(self, *, limit: int = BACKFILL_BATCH_LIMIT) -> BackfillResult:
        """为存量图片补生成多尺寸变体（幂等：只挑 ``variants`` 为空的记录）。

        单条失败（原文件被手动清理 / 解不开）只跳过不中断——回填本来就是
        「能补多少补多少」的运维动作，不值得为一张坏图整批报错。
        权限（仅站长）由路由层的 ``AdminUser`` 依赖把关。
        """
        rows = await self.attachments.list_images_without_variants(limit=limit)
        processed = updated = skipped = 0
        for record in rows:
            processed += 1
            subdir = f"uploads/{record.created_at.strftime('%Y%m')}"
            try:
                data = await storage.read(subdir=subdir, stored_name=record.stored_name)
            except FileNotFoundError:
                skipped += 1
                continue
            made = await asyncio.to_thread(self._make_variants, data)
            if not made:
                skipped += 1
                continue
            for variant_name, payload in made.values():
                await storage.save(subdir=subdir, stored_name=variant_name, data=payload)
            # JSON 列必须整体赋值，原地改 dict 不会触发 UPDATE
            record.variants = {
                width_label: variant_name for width_label, (variant_name, _) in made.items()
            }
            updated += 1
        await self.session.flush()
        # 顺手统计"还剩多少"：让前端不必拿 processed 去猜后端的分批上限
        remaining = await self.attachments.count_images_without_variants()
        return BackfillResult(
            processed=processed, updated=updated, skipped=skipped, remaining=remaining
        )


def _rand_token() -> str:
    """生成 8 位随机文件名主体。

    刻意用纯 ASCII 随机串而不是「时间戳 + 中文原名」：中文文件名在不同操作系统
    与反向代理上的 URL 编码行为并不一致，而原始文件名本来就完整存在数据库里，
    不需要靠文件名来承载信息。
    """
    return short_uuid()[:8]
