"""留言板模型（站点级的「访客留言 + 站长回复」）。

留言板与评论区长得很像，但它是**第 4 个独立业务域**，差别恰好都落在数据模型上。
下面每条都是「不这么做会怎样」，而不是对字段的复述：

1. **刻意不做楼中楼**。一条留言最多一个回复：``reply_content`` 直接落在同一行，
   而不是像 ``Comment`` 那样用 ``parent_id`` 自引用。这样「无限嵌套」在结构上
   就不可能出现——不必再写一条「仅支持两级」的运行时校验去兜住一个本来可以
   被建模消除的问题，前台也永远不用递归渲染。代价是留言者之间无法互相回复，
   而这正是留言板想要的形态：它是**访客对站长**说话的地方。
2. **先审后发**（``is_approved`` 默认 False）。留言板是访客唯一能主动出现在
   站点首页附近的内容入口，垃圾投递动机比文章评论更高。``is_approved`` 建索引
   是因为公开列表的固定读法就是 ``where is_approved order by id desc``。
3. **``author_email`` 不公开**，只给站长看（用于回复通知；没留邮箱就收不到信）。
   这既是隐私问题，也是缓存正确性问题：公开响应会进 ``PublicCacheMiddleware``
   的共享缓存，里面不能有任何随人/随请求变化的字段（见 ``api/cache.py``）。
4. **``replied_by_id`` 不建 relationship**。这一列只用于审计（谁在什么时候回过），
   不进任何 API；给它配一个 relationship 等于给所有查询挂上一个潜在的惰性加载，
   而异步会话下惰性加载会直接抛 ``MissingGreenlet``。真要显示回复人名时再显式 join。
5. **时间列一律 ``UTCDateTime``**：SQLite 取出来是 naive、PostgreSQL 是 aware，
   不统一的话同一个字段在「刚创建」与「重新查询」两条路径上会序列化出不同的 JSON。

字段上限（``author_name`` 50 / ``author_email`` 255 / ``author_site`` 255 /
``content`` 2000）与 ``schemas/guestbook.py`` 的校验上限一一对应，
避免出现「schema 放行、数据库截断」的不一致。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class GuestbookMessage(Base, TimestampMixin):
    __tablename__ = "guestbook_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 昵称必填（服务层保证）：留言板没有「回复某条留言」这种能把匿名者串起来的场景，
    # 一堆无名氏会让站长完全无法判断该回谁
    author_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # 只有站长看得到：用于回复通知。不建索引——查它永远是「按 pk 取一行」的附带结果
    author_email: Mapped[str | None] = mapped_column(String(255))
    # 已归一的 http/https 绝对地址（归一规则见 app/utils/url.py，与评论/友链共用一套）
    author_site: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)

    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # 站长回复；NULL = 还没回。刻意只有一列，见模块 docstring 第 1 条
    reply_content: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # SET NULL 而不是 CASCADE：删掉一个后台账号不该顺手删掉留言记录。
    # 回复人只是审计信息，丢掉它留言本身仍然完整可读。
    replied_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # 反垃圾留痕：与评论同一口径（同样的 client_ip 解析、同样的 500 截断）
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GuestbookMessage id={self.id} approved={self.is_approved}>"
