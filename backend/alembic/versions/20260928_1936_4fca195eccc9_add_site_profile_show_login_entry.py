"""add site_profile.show_login_entry

Revision ID: 4fca195eccc9
Revises: c3d7a1f4e8b2
Create Date: 2026-09-28 19:36:10.469440

## 为什么需要这一列

站长想「前台不露出登录入口」时，此前只能改代码 —— 而这件事本来就是个**站点策略**，
和 `comment_need_approval` / `allow_guest_comment` 同类，因此放进 `site_profile`
这一张单行表，由后台设置页勾选。

## server_default=TRUE 是必要的，而且要保留

这一列是 `NOT NULL`，而生产库里 `site_profile` **已经有那一行数据**了。
没有 server_default 的 `ADD COLUMN ... NOT NULL` 在已有数据上会直接失败
（PG 与 SQLite 都是），所以默认值必须由数据库侧给。

值取 `TRUE`：升级后前台行为**与升级前完全一致**（登录入口照旧显示）。
这是刻意的——迁移不改变任何可见行为，站长想关再去后台关。

保留 server_default（不像某些迁移那样回填后就 drop）：裸 SQL 插入
`site_profile` 时这一列也该有合理默认值，而"新站点默认显示登录入口"正是它。

## 回滚

`downgrade` 删列。删掉之后该开关自然消失，前台回到「永远显示登录入口」，
与升级前的行为一致，不会留下半开状态。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4fca195eccc9"
down_revision: str | None = "c3d7a1f4e8b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # sa.true() 会按方言渲染（PG 是 TRUE，SQLite 是 1），不要写死字面量
    op.add_column(
        "site_profile",
        sa.Column(
            "show_login_entry",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("site_profile", "show_login_entry")
