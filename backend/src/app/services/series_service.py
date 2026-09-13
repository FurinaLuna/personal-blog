"""系列服务：技术连载 / 合集的领域规则。

系列与分类同构（一对多、删系列文章变普通文章），因此大部分规则参照
``TaxonomyService``；与分类的差别是系列内的文章有**顺序**（``series_order``），
这是「连载阅读」语义的核心。
"""

from __future__ import annotations

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ArticleStatus, Series
from app.repositories import ArticleRepository, SeriesRepository
from app.schemas.article import ArticleSummary
from app.schemas.common import Page, PageParams
from app.schemas.series import SeriesCreate, SeriesRead, SeriesUpdate
from app.services.attachment_service import AttachmentService
from app.utils.exceptions import BadRequestError, ConflictError, NotFoundError
from app.utils.slug import unique_slug

# 前台统计口径：系列计数只算已发布文章，与列表页保持一致
PUBLISHED_ONLY: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED,)


class SeriesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.series = SeriesRepository(session)
        self.articles = ArticleRepository(session)
        # 「封面 URL -> 多尺寸变体」的映射是附件域知识，装配在这里组合使用
        self.attachments = AttachmentService(session)

    # ---------------------------------------------------------------- 查询

    async def list_series(self, *, with_counts: bool = True) -> list[SeriesRead]:
        """系列列表。

        Args:
            with_counts: 为 False 时跳过计数（例如后台下拉选择框）。
        """
        if not with_counts:
            return [self._out(item, 0) for item in await self.series.list_all()]

        rows = await self.series.list_with_counts(statuses=PUBLISHED_ONLY)
        return [self._out(series, count) for series, count in rows]

    async def get_series(self, slug_or_id: str) -> Series:
        series = await self._resolve(slug_or_id)
        if series is None:
            raise NotFoundError("系列不存在")
        return series

    async def list_articles_in_series(
        self, slug_or_id: str, *, page_params: PageParams
    ) -> tuple[SeriesRead, Page[ArticleSummary]]:
        """系列详情：系列信息 + 该系列的文章（分页，按系列内顺序）。"""
        series = await self.get_series(slug_or_id)
        # 系列内文章量级通常很小（连载个位数到十几篇），全量取再按顺序截断，
        # 比先 count 再查当页简单；分页语义仍与列表接口一致
        rows = await self.articles.list_by_series(series_id=series.id, statuses=PUBLISHED_ONLY)
        start = page_params.offset
        items = [
            ArticleSummary.model_validate(row) for row in rows[start : start + page_params.limit]
        ]
        # 系列页的文章卡片与首页列表同一渲染组件，封面变体口径必须一致
        covers = [item.cover_image for item in items if item.cover_image]
        variant_map = await self.attachments.variant_map_by_urls(covers)
        for item in items:
            if item.cover_image:
                item.cover_variants = variant_map.get(item.cover_image, [])
        return (
            self._out(series, len(rows)),
            Page.build(items, len(rows), page_params.page, page_params.page_size),
        )

    @staticmethod
    def _out(series: Series, article_count: int) -> SeriesRead:
        return SeriesRead(
            id=series.id,
            name=series.name,
            slug=series.slug,
            description=series.description,
            article_count=article_count,
            created_at=series.created_at,
            updated_at=series.updated_at,
        )

    # ---------------------------------------------------------------- 写入

    async def create_series(self, payload: SeriesCreate) -> SeriesRead:
        if await self.series.get_by_name(payload.name):
            raise ConflictError(f"系列「{payload.name}」已存在")
        slug = await unique_slug(
            payload.slug or payload.name,
            prefix="series",
            exists=self.series.slug_exists,
        )
        series = await self.series.create(
            name=payload.name,
            slug=slug,
            description=payload.description,
        )
        return self._out(series, 0)

    async def update_series(self, series_id: int, payload: SeriesUpdate) -> SeriesRead:
        series = await self.series.get(series_id)
        if series is None:
            raise NotFoundError("系列不存在")

        data = payload.model_dump(exclude_unset=True)
        new_name = data.get("name")
        if new_name and new_name != series.name and await self.series.get_by_name(new_name):
            raise ConflictError(f"系列「{new_name}」已存在")
        if new_slug := data.pop("slug", None):
            data["slug"] = await unique_slug(
                new_slug,
                prefix="series",
                # 更新自己时，自己的 slug 不该算冲突
                exists=partial(self.series.slug_exists, exclude_id=series_id),
            )

        # description 允许被清空为 null；其余字段收到 None 视为「不修改」
        for key, value in data.items():
            if value is not None or key == "description":
                setattr(series, key, value)
        await self.session.flush()
        return self._out(series, 0)

    async def delete_series(self, series_id: int) -> None:
        """删除系列。

        系列下的文章 **不删**：由数据库的 ``ON DELETE SET NULL`` 把它们变成
        普通文章。内容永远比结构重要——与分类同一策略。
        """
        series = await self.series.get(series_id)
        if series is None:
            raise NotFoundError("系列不存在")
        await self.series.delete(series)

    # ---------------------------------------------------------------- 供文章领域调用

    async def ensure_series(self, series_id: int | None) -> Series | None:
        """取系列对象（而不是只校验存在性），供 ``ArticleService`` 使用。"""
        if series_id is None:
            return None
        series = await self.series.get(series_id)
        if series is None:
            raise BadRequestError(f"系列 {series_id} 不存在")
        return series

    # ---------------------------------------------------------------- 工具

    async def _resolve(self, slug_or_id: str) -> Series | None:
        key = slug_or_id.strip()
        if key.isdigit():
            found = await self.series.get(int(key))
            if found is not None:
                return found
        return await self.series.get_by_slug(key)
