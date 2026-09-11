"""站点配置 / 关于页 / 统计 / 健康检查路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.schemas.site import SiteProfileRead, SiteProfileUpdate, SiteStats
from app.services import SiteService

router = APIRouter(prefix="/site", tags=["站点"])


@router.get("/profile", response_model=SiteProfileRead, summary="站点档案（关于页数据源）")
async def get_profile(session: SessionDep) -> SiteProfileRead:
    """公开接口，前台「关于页」直接渲染它。"""
    profile = await SiteService(session).get_profile()
    return SiteService.to_read(profile)


@router.patch("/profile", response_model=SiteProfileRead, summary="修改站点档案（站长）")
async def update_profile(
    payload: SiteProfileUpdate, _: AdminUser, session: SessionDep
) -> SiteProfileRead:
    profile = await SiteService(session).update_profile(payload)
    return SiteService.to_read(profile)


@router.get("/stats", response_model=SiteStats, summary="全站统计（后台仪表盘）")
async def get_stats(_: AdminUser, session: SessionDep) -> SiteStats:
    """后台仪表盘一次拿全部数字，避免仪表盘并发打 8 个接口。"""
    return await SiteService(session).stats()
