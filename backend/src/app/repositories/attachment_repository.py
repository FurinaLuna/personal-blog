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
