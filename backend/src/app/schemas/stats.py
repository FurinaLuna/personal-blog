"""统计趋势 Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class DailyViewStats(BaseModel):
    """某一天的访问聚合（仪表盘趋势曲线的一个数据点）。"""

    # ISO 日期字符串（"2026-09-13"）：JSON 没有原生 date 类型，
    # 序列化责任收在 schema 层，服务层继续用 date 对象算零填补齐
    date: str
    views: int
    unique_visitors: int


class PruneResult(BaseModel):
    """访问日志清理结果。"""

    removed: int
    retention_days: int
