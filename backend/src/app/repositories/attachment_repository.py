"""附件仓储。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Attachment
from app.repositories.base import BaseRepository


class AttachmentRepository(BaseRepository[Attachment]):
    model = Attachment

    async def list_paged(
        self, *, offset: int, limit: int, kind: str | None = None, uploader_id: int | None = None
    ) -> list[Attachment]:
        stmt = select(Attachment)
        if kind:
            stmt = stmt.where(Attachment.kind == kind)
        if uploader_id is not None:
            stmt = stmt.where(Attachment.uploader_id == uploader_id)
        stmt = stmt.order_by(Attachment.id.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, kind: str | None = None, uploader_id: int | None = None) -> int:
        stmt = select(func.count()).select_from(Attachment)
        if kind:
            stmt = stmt.where(Attachment.kind == kind)
        if uploader_id is not None:
            stmt = stmt.where(Attachment.uploader_id == uploader_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_by_stored_name(self, stored_name: str) -> Attachment | None:
        result = await self.session.execute(
            select(Attachment).where(Attachment.stored_name == stored_name)
        )
        return result.scalars().first()

    async def get_by_urls(self, urls: list[str]) -> list[Attachment]:
        """按 url 批量查（文章封面装配用）。

        封面存在 Article.cover_image 里是 URL 字符串，变体挂在 Attachment 上，
        靠这层映射把两者接起来。列表页一次 IN 查询，避免逐条回表。
        """
        if not urls:
            return []
        stmt = select(Attachment).where(Attachment.url.in_(urls))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_images_without_variants(self, *, limit: int) -> list[Attachment]:
        """还没生成过多尺寸变体的图片（回填用）。"""
        stmt = (
            select(Attachment)
            .where(Attachment.kind == "image", Attachment.variants.is_(None))
            .order_by(Attachment.id)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_images_without_variants(self) -> int:
        """还差多少张（回填接口用它告诉前端"要不要再跑一次"）。

        刻意由后端算：前端拿 processed 去和批次上限比较是在**猜**后端的分批策略，
        而这个常量一改（或将来改成按体积分批），前端的提示就会说谎。
        """
        stmt = (
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.kind == "image", Attachment.variants.is_(None))
        )
        return int((await self.session.execute(stmt)).scalar_one())
