"""站点配置仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models import SiteProfile
from app.repositories.base import BaseRepository


class SiteRepository(BaseRepository[SiteProfile]):
    model = SiteProfile

    async def get_profile(self) -> SiteProfile | None:
        """站点档案是单行表（id=1），找不到说明数据库还没初始化。"""
        result = await self.session.execute(select(SiteProfile).where(SiteProfile.id == 1))
        return result.scalars().first()

    async def get_or_create_profile(self) -> SiteProfile:
        profile = await self.get_profile()
        if profile is None:
            profile = await self.create(id=1)
        return profile
