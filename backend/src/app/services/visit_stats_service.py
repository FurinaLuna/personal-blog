"""访问统计服务：记录详情访问 + 按天聚合出趋势。"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.repositories import VisitLogRepository
from app.schemas.stats import DailyViewStats

DAILY_STATS_MAX_DAYS = 90


def _hash_ip(ip: str | None) -> str:
    """IP → HMAC-SHA256 摘要（十六进制，64 字符）。

    用 HMAC 而不是裸 SHA256：裸哈希下「遍历所有 IPv4 反查明文」在算力上
    是可行的（IPv4 空间只有 2^32），带密钥的 HMAC 让彩虹表攻击者必须
    先拿到 jwt_secret_key。
    取不到 IP（测试 ASGITransport / 极端部署形态）时归一成常量摘要：
    这类访问 UV 记 1，比整条丢掉（当天 UV=0）更接近真实。
    """
    return hmac.new(
        settings.jwt_secret_key.encode(),
        (ip or "unknown").encode(),
        hashlib.sha256,
    ).hexdigest()


class VisitStatsService:
    """记录与聚合访问日志。

    只在 ``get_article`` 路由里被调用（与 view_count 同口径：仅已发布文章、
    仅详情页），聚合只服务仪表盘——职责单一，article_service 零改动。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.visit_logs = VisitLogRepository(session)

    async def record(self, article_id: int, *, ip: str | None) -> None:
        """记一次访问。只 flush 不 commit——与请求事务同生共死。"""
        await self.visit_logs.create(
            article_id=article_id,
            date=datetime.now(UTC).date(),
            ip_hash=_hash_ip(ip),
        )

    async def daily_views(self, *, days: int) -> list[DailyViewStats]:
        """近 ``days`` 天的每日 PV/UV（含今天，缺日补零）。

        补零必须在服务层做：数据库 ``GROUP BY`` 天生只返回「有数据的日期」，
        而趋势曲线需要连续序列——前端拿到断点数组画出来就是锯齿断线图。
        """
        today = datetime.now(UTC).date()
        start = today - timedelta(days=days - 1)
        rows = await self.visit_logs.daily_views(start=start, end=today)
        by_date = {row_date: (views, uv) for row_date, views, uv in rows}
        stats: list[DailyViewStats] = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            views, uv = by_date.get(day, (0, 0))
            stats.append(DailyViewStats(date=day.isoformat(), views=views, unique_visitors=uv))
        return stats
