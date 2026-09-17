"""访问统计路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.config import settings
from app.schemas.stats import DailyViewStats, PruneResult
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


@router.post("/visit-logs/prune", response_model=PruneResult, summary="清理过期访问日志（站长）")
async def prune_visit_logs(
    _: AdminUser,
    session: SessionDep,
    retention_days: Annotated[
        int | None,
        Query(ge=1, le=3650, description="保留多少天；不传则用配置的 VISIT_LOG_RETENTION_DAYS"),
    ] = None,
) -> PruneResult:
    """手动清理访问日志。

    启动时已经会自动清一次（见 ``main._prune_visit_logs``）。这个端点存在的意义是
    「不等下次重启也能立刻回收」，以及「把清理结果显式返回出来便于核对」——
    维护动作如果完全不可观测，运维就只能靠猜。
    """
    effective = retention_days if retention_days is not None else settings.visit_log_retention_days
    removed = await VisitStatsService(session).prune(retention_days=effective)
    await session.commit()
    return PruneResult(removed=removed, retention_days=effective)
