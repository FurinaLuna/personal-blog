"""add refresh_sessions.first_rotated_at to stop sliding the reuse window

Revision ID: a4c9e2f71b38
Revises: f3b7c1a95d24
Create Date: 2026-09-27 22:35:00.000000

## 为什么需要这一列（修的是一条真实的绕过）

`AuthService.refresh` 用 `REFRESH_REUSE_GRACE_SECONDS`（30 秒）的宽容窗口容忍
「两个标签页同时刷新」。窗口原本按 `refresh_sessions.rotated_at` 计算，
而 `mark_rotated` **每次**调用都会把 `rotated_at` 重置成"现在"——
于是窗口会**滑动**：

    攻击者持有一枚已用过的 refresh token，每 <30 秒重放一次：
    每次都在窗口内 ⇒ 放行 ⇒ `mark_rotated` 把 rotated_at 推到当前
    ⇒ 下一次重放又落在窗口内……无限续期，而"复用即吊销整个会话族"的检测永不触发。

`first_rotated_at` 只在第一次轮换时写入、之后不再更新，用它算窗口就滑不动了：
从**第一次**轮换起算，超过 30 秒的重放一律按盗用处理并吊销整族。

## 可空，不需要 server_default

现存行（升级前就轮换过的会话）这一列是 NULL，服务层用
`anchor = record.first_rotated_at or record.rotated_at` 回退到旧字段，
因此旧数据不会报错、也不会被误判成盗用。
刻意不给 server_default：新行进入"已轮换"状态时必然由代码写入这个值，
给个假的默认时间只会掩盖问题。

## 回滚

`downgrade` 删列。删掉之后滑动窗口的绕过会重新出现，所以只在必须回到旧行为时用。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.types import UTCDateTime

revision: str = "a4c9e2f71b38"
down_revision: str | None = "f3b7c1a95d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 时间类型必须与模型一致（app.db.types.UTCDateTime）：SQLite 取出来是 naive、
    # PG 是 aware，混用会在比较时抛 "can't compare offset-naive and offset-aware"。
    op.add_column(
        "refresh_sessions",
        sa.Column("first_rotated_at", UTCDateTime(), nullable=True),
    )
    # 索引：这个值目前只用于单行比较，不需要索引；留个说明免得后人以为漏了。


def downgrade() -> None:
    op.drop_column("refresh_sessions", "first_rotated_at")
