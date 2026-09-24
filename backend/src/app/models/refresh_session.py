"""刷新令牌会话（refresh token session）。

## 为什么需要一张表

在此之前 refresh token 是**完全无状态**的：`jti` 生成了却从不落库，
「吊销」只能靠 `users.token_version += 1`（改密码 / 登出），
而那是**全端下线**——手机上登出，电脑上也得重新登录。

更关键的是**泄露后无法止损**：一个被偷走的 refresh token 在 7 天有效期内
可以反复换出新的 access token，服务端既看不见、也拦不住。
它还有一个更隐蔽的问题：每次 `refresh()` 都签发新令牌而旧令牌**继续有效**，
于是泄露面随时间单调增长（每刷新一次就多一枚可用的凭证）。

引入这张表之后：

1. **轮换**：每次换新令牌时把旧的标记为已用，旧令牌不再可用；
2. **复用检测**：如果一枚"已用过"的令牌又被拿来换（说明它被复制走了），
   把整个会话族吊销并拒绝——这是 OAuth 2.0 对 refresh token 的标准处理；
3. **单会话吊销**：登出可以只吊销当前这一条链路（`revoked_at`）。

## 只存哈希，不存令牌

`jti_hash` 是 jti 的 SHA-256（jti 本身是随机 hex，不需要加盐）。
即使数据库被拖走，也无法据此伪造出可用令牌——与「密码只存哈希」同一原则。

## 多标签页的宽容窗口

`REFRESH_REUSE_GRACE_SECONDS`（见 auth_service）：一个令牌在**刚被轮换后的
极短时间内**又被使用，更可能来自"两个标签页同时刷新"而不是盗用
（前端的单飞锁只在单页内生效，跨标签页无效）。因此窗口内按"再来一次"处理，
窗口外才判定为复用并吊销整族。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime


class RefreshSession(Base, TimestampMixin):
    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # jti 的 SHA-256（hex，64 字符）；一台设备一条会话
    jti_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # 用 UTCDateTime 而不是裸 DateTime(timezone=True)：SQLite 取出来是 naive、
    # PG 是 aware，直接比较会抛 "can't compare offset-naive and offset-aware"。
    # 这个坑在第一次跑测试时就撞上了（refresh 的过期判断），
    # 而 db/types.py 早就为它准备了统一的类型。
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    # 被轮换掉的时刻：这一枚已经不能再用了（复用检测的依据）
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 轮换后的下一枚（排障时能顺着链条看"这枚是从哪来的"）
    replaced_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def is_usable(self) -> bool:
        return self.revoked_at is None and self.rotated_at is None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RefreshSession id={self.id} user_id={self.user_id} used={not self.is_usable}>"
