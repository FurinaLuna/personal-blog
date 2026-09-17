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

    async def prune(self, *, retention_days: int | None = None) -> int:
        """删掉留存期之外的访问日志，返回删除行数。

        为什么必须有这个操作：``record()`` 每次详情访问写一行，而读取只覆盖近
        ``DAILY_STATS_MAX_DAYS`` 天。没有清理的话这张表只增不减——
        单篇热门文章就能贡献几万行，一年下来几十万行，代价体现在备份体积、
        迁移耗时和 ``VACUUM`` 时间上，而收益是零。

        留存期默认为配置里的 ``VISIT_LOG_RETENTION_DAYS``（180 天，是读取窗口的
        两倍，留出回头看的余地）；调用方也可以显式传值。
        只 flush 不 commit，事务边界交给调用方。
        """
        days = retention_days if retention_days is not None else settings.visit_log_retention_days
        if days <= 0:
            # 显式关掉清理（或配成 0）时不动数据，避免误把整张表删空
            return 0
        cutoff = datetime.now(UTC).date() - timedelta(days=days)
        return await self.visit_logs.delete_before(cutoff)
