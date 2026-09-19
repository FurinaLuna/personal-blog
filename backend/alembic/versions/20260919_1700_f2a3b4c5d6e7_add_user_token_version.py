"""add users.token_version

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-19 17:00:00.000000

实现说明（令牌代次 / token versioning）：

给 users 加一个整数代次，签发 JWT 时写进载荷，校验时与库里的当前值比对，
不一致即视为失效。**改密码时 +1，于是该用户所有已签发的 access / refresh
token 立刻作废。**

为什么必须做：refresh token 原本是不可吊销的（签名有效 + 用户存在就认），
7 天有效期内一旦泄露就无法止损；而「改密码」这个应急动作只改了哈希，
对已泄露的 token 毫无影响 —— 等于没有应急手段。

对存量数据：`server_default="0"` 让已有用户直接拿到 0，无需回填脚本；
它们手上已签发的 token 里没有 `ver` 字段，解析时按 0 处理，
因此**升级不会把所有登录态踢掉**（见 utils/security.py 的说明）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default 而不是 nullable：已有行需要一个确定的值，
    # 否则应用侧读到的 None 会让「代次比对」变成 None != 0 而误判为失效。
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
