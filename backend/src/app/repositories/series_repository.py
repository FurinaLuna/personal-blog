"""系列仓储。"""

from __future__ import annotations

from sqlalchemy import and_, func, select

from app.models import Article, ArticleStatus, Series
from app.repositories.base import BaseRepository


class SeriesRepository(BaseRepository[Series]):
    model = Series

    async def get_by_slug(self, slug: str) -> Series | None:
        return await self.get_by("slug", slug)

    async def get_by_name(self, name: str) -> Series | None:
        return await self.get_by("name", name)

    async def slug_exists(self, slug: str, *, exclude_id: int | None = None) -> bool:
        return await self.exists_by("slug", slug, exclude_id=exclude_id)

    async def list_all(self) -> list[Series]:
        result = await self.session.execute(select(Series).order_by(Series.name, Series.id))
        return list(result.scalars().all())

    async def list_with_counts(
        self, *, statuses: tuple[ArticleStatus, ...]
    ) -> list[tuple[Series, int]]:
        """系列 + 该系列下的文章数。

        统计口径与列表页保持一致（只算给定状态），否则会出现「系列显示 5 篇、
        点进去只有 3 篇」的不一致（与分类同策略）。
        """
        stmt = (
            select(Series, func.count(Article.id))
            .outerjoin(
                Article,
                and_(
                    Article.series_id == Series.id,
                    Article.status.in_(list(statuses)),
                ),
            )
            .group_by(Series.id)
            .order_by(Series.name, Series.id)
        )
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]
