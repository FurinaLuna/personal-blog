"""友情链接服务：友链领域规则唯一所在地。

规则只有三条，但每条都有个「不这么写会怎样」：

1. **地址唯一** → ``ConflictError``（409）。数据库上的唯一索引是最后一道闸，
   这里先查一次是为了给出人话文案；两者同时存在不是冗余——并发下
   「先查后插」的窗口必然存在，撞上时由 ``IntegrityError`` handler 兜成 409。
2. **不存在 → ``NotFoundError``（404）**，而不是静默返回 None。让调用方
   （路由）永远拿到一个真对象，不用在每个 handler 里再写一遍判空。
3. **排序口径**（``sort_order`` 升序 → ``id`` 升序）只在仓储里写一份，
   前台与后台共用：后台里看到的相对顺序就是前台的顺序。

事务：只 ``flush`` 不 ``commit``，边界交给 ``api/v1/links.py`` 与
``get_session`` 依赖收口——与本项目其余 service 一致。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FriendLink
from app.repositories import FriendLinkRepository
from app.schemas.friend_link import FriendLinkCreate, FriendLinkUpdate
from app.utils.exceptions import ConflictError, NotFoundError

# 允许被 PATCH 显式置为 null 的字段。
#
# 其余字段（name / url / sort_order / is_active）收到 None 一律视为「不修改」：
# 它们在库里是 NOT NULL，硬写下去只会得到一个 IntegrityError → 500，
# 而「把友链的名字清空」在语义上本来也不存在。这与 ``TaxonomyService``
# 处理 description 的口径一致（那里也是一份白名单式的判断）。
_CLEARABLE_FIELDS = frozenset({"description", "avatar_url"})


class FriendLinkService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.links = FriendLinkRepository(session)

    # ---------------------------------------------------------------- 查询

    async def list_active(self) -> list[FriendLink]:
        """前台列表：只有已启用的条目。"""
        return await self.links.list_active()

    async def list_all(self) -> list[FriendLink]:
        """后台列表：含未启用的条目，排序与前台一致。"""
        return await self.links.list_all()

    # ---------------------------------------------------------------- 写入

    async def create(self, payload: FriendLinkCreate) -> FriendLink:
        """新建友链。

        Raises:
            ConflictError: 该地址已被收录。
        """
        await self._ensure_url_available(payload.url)
        return await self.links.create(**payload.model_dump())

    async def update(self, link_id: int, payload: FriendLinkUpdate) -> FriendLink:
        """部分更新：只改请求里真的带了的字段。

        ``exclude_unset`` 区分「没传这个字段」与「传了 null」——这正是
        「只改传了的字段」这条语义的实现方式；不带它的话，一次只想改顺序的
        PATCH 会把没传的字段全部覆盖成 None。

        Raises:
            NotFoundError: 友链不存在。
            ConflictError: 改后的地址与其他友链重复。
        """
        link = await self._get_or_404(link_id)
        data = payload.model_dump(exclude_unset=True)

        new_url = data.get("url")
        # 地址没变时跳过查重：否则「改顺序」这种不碰地址的 PATCH 也要多查一次库
        if new_url is not None and new_url != link.url:
            await self._ensure_url_available(new_url, exclude_id=link_id)

        changed = {
            key: value
            for key, value in data.items()
            if value is not None or key in _CLEARABLE_FIELDS
        }
        return await self.links.update(link, **changed)

    async def delete(self, link_id: int) -> None:
        """删除友链（物理删除）。

        与文章/分类不同，友链没有级联引用、也没有「删了就找不回来」的内容，
        因此不引入软删除：临时下线用 ``is_active=False``。
        """
        link = await self._get_or_404(link_id)
        await self.links.delete(link)

    # ---------------------------------------------------------------- 工具

    async def _get_or_404(self, link_id: int) -> FriendLink:
        link = await self.links.get(link_id)
        if link is None:
            raise NotFoundError("友链不存在")
        return link

    async def _ensure_url_available(self, url: str, *, exclude_id: int | None = None) -> None:
        """地址是否可被占用。

        Args:
            url: 已归一的绝对地址。
            exclude_id: 更新场景下排除自己——不改地址的 PATCH 不该和自己冲突。

        Raises:
            ConflictError: 地址已被另一条友链占用。
        """
        if await self.links.get_by_url(url, exclude_id=exclude_id) is not None:
            raise ConflictError(f"友链地址「{url}」已被收录")
