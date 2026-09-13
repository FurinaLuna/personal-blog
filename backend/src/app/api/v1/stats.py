"""访问统计路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.schemas.stats import DailyViewStats
from app.services import VisitStatsService

router = APIRouter(prefix="/stats", tags=["统计"])


@router.get("/views/daily", response_model=list[DailyViewStats], summary="每日访问趋势（站长）")
async def daily_views(
    _: AdminUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=90, description="统计天数（含今天），缺日补零")] = 30,
) -> list[DailyViewStats]:
    """近 N 天 PV/UV 曲线数据，仅站长可见（访客量对博客是敏感经营数据）。"""
    return await VisitStatsService(session).daily_views(days=days)
