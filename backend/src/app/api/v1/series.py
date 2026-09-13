"""系列路由（技术连载 / 合集）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AdminUser, AuthorUser, SessionDep
from app.api.pagination import PageParamsDep
from app.schemas.article import SeriesWithArticles
from app.schemas.common import Message
from app.schemas.series import SeriesCreate, SeriesRead, SeriesUpdate
from app.services import SeriesService

router = APIRouter(prefix="/series", tags=["系列"])


@router.get("", response_model=list[SeriesRead], summary="系列列表（前台）")
async def list_series(
    session: SessionDep,
    with_counts: Annotated[bool, Query(description="是否带各系列文章数")] = True,
) -> list[SeriesRead]:
    """前台系列聚合页的数据源；计数口径 = 已发布文章。"""
    return await SeriesService(session).list_series(with_counts=with_counts)


@router.get("/{slug_or_id}", response_model=SeriesWithArticles, summary="系列详情（含文章列表）")
async def get_series(
    session: SessionDep,
    page_params: PageParamsDep,
    slug_or_id: str,
) -> SeriesWithArticles:
    """系列信息 + 该系列的文章（按系列内顺序分页）。"""
    series, page = await SeriesService(session).list_articles_in_series(
        slug_or_id, page_params=page_params
    )
    return SeriesWithArticles(series=series, articles=page)


# ------------------------------------------------------------------ 后台管理（作者/站长）


@router.post(
    "", response_model=SeriesRead, status_code=status.HTTP_201_CREATED, summary="新建系列（作者）"
)
async def create_series(payload: SeriesCreate, _: AuthorUser, session: SessionDep) -> SeriesRead:
    series = await SeriesService(session).create_series(payload)
    await session.commit()
    return series


@router.patch("/{series_id}", response_model=SeriesRead, summary="修改系列（作者）")
async def update_series(
    series_id: int, payload: SeriesUpdate, _: AuthorUser, session: SessionDep
) -> SeriesRead:
    series = await SeriesService(session).update_series(series_id, payload)
    await session.commit()
    return series


@router.delete("/{series_id}", response_model=Message, summary="删除系列（站长）")
async def delete_series(series_id: int, _: AdminUser, session: SessionDep) -> Message:
    """删除系列：其下文章变为普通文章（FK SET NULL），文章内容不受影响。"""
    await SeriesService(session).delete_series(series_id)
    await session.commit()
    return Message(detail="系列已删除")
