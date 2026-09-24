"""友情链接仓储。

只有三条查询，都围绕同一张表的固定读法；业务规则（唯一性冲突、不存在）
一律在 ``services/friend_link_service.py``，这里不做判断。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import FriendLink
from app.repositories.base import BaseRepository


class FriendLinkRepository(BaseRepository[FriendLink]):
    model = FriendLink

    # 排序口径只写一次： ``sort_order`` 升序、再按 ``id`` 升序。
    # 补上 ``id`` 是必需的，不是洁癖：``sort_order`` 默认全是 0
    # （站长不调顺序时的常态），只按它排序的话，两次请求之间的行序由数据库
    # 自行决定，表现为「刷新一下友链顺序就变了」。有了 id 兜底，顺序稳定可预期。
    _ORDER = (FriendLink.sort_order, FriendLink.id)

    async def list_active(self) -> list[FriendLink]:
        """前台列表：只含已启用的条目。"""
        result = await self.session.execute(
            select(FriendLink).where(FriendLink.is_active.is_(True)).order_by(*self._ORDER)
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[FriendLink]:
        """后台列表：含未启用的条目，排序口径与前台一致。

        刻意与 ``list_active`` 共用同一个排序元组：后台里看到的相对顺序
        就是前台的真实顺序，站长调完 ``sort_order`` 不必切到前台去核对。
        """
        result = await self.session.execute(select(FriendLink).order_by(*self._ORDER))
        return list(result.scalars().all())

    async def get_by_url(self, url: str, *, exclude_id: int | None = None) -> FriendLink | None:
        """按地址取一条（用于查重）。

        Args:
            url: 已归一的绝对地址（调用方保证，见 schemas/friend_link.py）。
            exclude_id: 排除的记录 id，用于「更新时排除自己」——
                不改地址的 PATCH 不该判定成「和自己冲突」。
        """
        stmt = select(FriendLink).where(FriendLink.url == url)
        if exclude_id is not None:
            stmt = stmt.where(FriendLink.id != exclude_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()
