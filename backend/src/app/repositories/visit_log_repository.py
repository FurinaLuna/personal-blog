"""访问日志仓储。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models import VisitLog
from app.repositories.base import BaseRepository


class VisitLogRepository(BaseRepository[VisitLog]):
    model = VisitLog

    async def daily_views(self, *, start: date, end: date) -> list[tuple[date, int, int]]:
        """按天聚合 ``[start, end]``（闭区间）的 PV 与 UV。

        返回原始行 ``(date, views, unique_visitors)``，**不含缺日补零**——
        「把查询结果对齐到完整日期序列」是展示语义，归服务层管。
        """
        stmt = (
            select(
                VisitLog.date,
                func.count().label("views"),
                func.count(func.distinct(VisitLog.ip_hash)).label("unique_visitors"),
            )
            .where(VisitLog.date >= start, VisitLog.date <= end)
            .group_by(VisitLog.date)
            .order_by(VisitLog.date)
        )
        result = await self.session.execute(stmt)
        return [(row.date, int(row.views), int(row.unique_visitors)) for row in result.all()]
