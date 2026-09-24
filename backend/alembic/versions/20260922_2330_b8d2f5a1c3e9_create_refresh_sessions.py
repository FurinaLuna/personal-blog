"""create refresh_sessions

Revision ID: b8d2f5a1c3e9
Revises: a7c1e9d4b2f8
Create Date: 2026-09-22 23:30:00.000000

实现说明（刷新令牌轮换 / rotation）：

在此之前 refresh token 是**完全无状态**的——`jti` 生成了却从不落库，
"吊销"只能靠 `users.token_version += 1`（改密码 / 登出），那是全端下线。
两个后果：

1. **泄露后无法止损**：被偷走的 refresh token 在 7 天有效期内可以反复换出
   新的 access token，服务端既看不见也拦不住；
2. **泄露面单调增长**：每次 `refresh()` 都签发新令牌而旧令牌继续有效，
   于是每刷新一次就多一枚可用凭证。

这张表让 refresh token 变成"有状态"的：轮换（旧的用完即废）、
复用检测（已轮换的令牌再次出现 ⇒ 判定盗用 ⇒ 吊销整族）、
以及按行吊销（单会话下线）。

**对存量登录态的影响（重要）**：升级后，此前签发的 refresh token
在库里没有对应行 ⇒ `refresh()` 会拒绝 ⇒ 所有现存会话需要重新登录一次。
个人博客是单用户自用，这个代价可以接受；如果这是多用户系统，
应该做一个"识别为旧令牌则放行一次并补登记"的过渡逻辑。
access token 不受影响（它不查这张表），会在 120 分钟内自然过期。

**只存哈希**：`jti_hash` 是 jti 的 SHA-256。即使数据库被拖走，
也无法据此伪造出可用令牌（jti 本身是 128 bit 随机数，不需要加盐）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f5a1c3e9"
down_revision: str | None = "a7c1e9d4b2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("jti_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # 用户被删时会话必须跟着走（表里存的是凭证状态，孤儿行没有意义）
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 四条索引，各自对应一条真实查询路径：
    #   按 jti 找（每次刷新）/ 按用户吊销（登出、改密码）/ 按过期时间清理（启动时）/
    #   按创建时间（TimestampMixin 声明了 index=True，这里必须跟着建）
    #
    # jti_hash 用 **唯一索引** 而不是 `UniqueConstraint + 普通索引`：
    # 模型里写的是 `unique=True, index=True`，SQLAlchemy 据此生成的是**一条**唯一索引；
    # 迁移若改写成约束+索引，PostgreSQL 上就会出现两条功能重叠的索引
    # （约束自带索引），既浪费写入又让 `compare_metadata` 永远报漂移。
    # 全库统一口径：unique + index ⇒ 一条唯一索引。
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])
    op.create_index(
        "ix_refresh_sessions_jti_hash", "refresh_sessions", ["jti_hash"], unique=True
    )
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"])
    op.create_index("ix_refresh_sessions_created_at", "refresh_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_created_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_jti_hash", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
