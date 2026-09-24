"""友情链接模型（独立博客之间的互相推荐）。

友链在数据上几乎是最简单的一张表（六个业务字段），但它有三个容易做错的地方，
在这里一次性定下来：

1. **``url`` 唯一**。同一个站点被收录两次，前台就会出现两张一模一样的卡片；
   更麻烦的是站长在后台「又添加了一次」时看到的不是既有那条，于是他去编辑副本，
   两条记录从此各自漂移。唯一约束把重复挡在数据库层，服务层再补一句人话（409）。
2. **``is_active`` 与 ``sort_order`` 都建索引**。公开列表的查询是
   ``where is_active order by sort_order, id``：前者过滤、后者排序。
   ``is_active`` 只有两个取值，选择度很低，规划器在几十行的表上多半直接全表扫——
   保留它的理由不是当下的性能，而是**契约一致性**：这张表的访问模式写在索引定义里，
   等到真长到几千行（或它被当成联表条件）时不必回头补。
   ``sort_order`` 同理，但它同时服务于「站长手工调整顺序」这一读法。
3. **不做软删除**。临时下线的友链改用 ``is_active=False``——对方站点 502 了几天，
   站长不希望丢掉备注与排序位；而「真的不再友链」是一个明确动作，交给 DELETE。

字段上限卡在「够用」而不是「尽量大」：``name`` 50 / ``url`` 500 /
``description`` 200 / ``avatar_url`` 500，与 ``schemas/friend_link.py`` 的
校验上限一一对应，避免出现「schema 放行、数据库截断」的不一致。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class FriendLink(Base, TimestampMixin):
    __tablename__ = "friend_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 站点名（展示用），不唯一：同名不同域名的站点确实存在
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    # 站点地址。唯一约束是这张表的业务核心（见模块 docstring），
    # 与全库约定一致：unique + index 生成**一条唯一索引**，不额外建唯一约束。
    # 值由 schemas 层经 ``normalize_http_url`` 归一后写入（http/https 绝对地址）
    url: Mapped[str] = mapped_column(String(500), unique=True, index=True, nullable=False)
    # 一句话介绍，可空；空值表示站长没写，不是「空字符串」
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 站点头像 / 图标地址，可空；为空时前端用站名首字占位
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 展示顺序，升序；相同则按 id（保证分页与刷新之间顺序稳定）
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    # 是否在前台展示。默认启用：站长加友链的意图默认是「马上挂出去」
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FriendLink id={self.id} name={self.name!r} active={self.is_active}>"
