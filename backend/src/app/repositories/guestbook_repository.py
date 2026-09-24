"""留言板仓储。

只有两条查询，都围绕同一张表的固定读法：``where is_approved order by id desc``。
公开列表（``approved=True``）、待审队列（``approved=False``）、后台全量（``None``）
走的是同一个 ``_scoped``，筛选口径只写一份——分页总数与实际返回的行因此不可能对不上。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select

from app.models import GuestbookMessage
from app.repositories.base import BaseRepository


class GuestbookRepository(BaseRepository[GuestbookMessage]):
    model = GuestbookMessage

    async def list_paged(
        self, *, offset: int, limit: int, approved: bool | None = None
    ) -> list[GuestbookMessage]:
        """分页取留言。

        ``approved`` 为 ``None`` 表示不过滤（后台全量），``True`` 只取已过审（公开列表），
        ``False`` 只取待审（站长的待办队列）。
        """
        stmt = self._scoped(select(GuestbookMessage), approved=approved)
        # 按 id 倒序而不是 created_at：自增主键单调递增，天然等价于「最新在前」，
        # 而且没有并列问题——同一秒创建的多条留言如果只按 created_at 排序，
        # 两次请求之间的行序由数据库自行决定，分页会出现重复/漏项
        stmt = stmt.order_by(GuestbookMessage.id.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, approved: bool | None = None) -> int:
        stmt = self._scoped(select(func.count()).select_from(GuestbookMessage), approved=approved)
        return int((await self.session.execute(stmt)).scalar_one())

    @staticmethod
    def _scoped(stmt: Select, *, approved: bool | None) -> Select:
        """``count`` 与 ``list_paged`` 共用的过滤口径——两边必须一致，
        否则分页信封里的 ``total`` 与页面上真实条数会对不上。
        """
        if approved is not None:
            stmt = stmt.where(GuestbookMessage.is_approved.is_(approved))
        return stmt
