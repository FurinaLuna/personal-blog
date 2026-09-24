"""create friend_links

Revision ID: c1f4b8e2d6a3
Revises: b8d2f5a1c3e9
Create Date: 2026-09-24 19:43:00.000000

实现说明（友情链接）：

全新建表，没有存量数据要搬运，因此这次迁移只需要把三件事定下来。

1. **``url`` 唯一**。同一个站点被收录两次，前台就会出现两张一模一样的卡片；
   更麻烦的是站长在后台「又添加了一次」时，看到的不是既有那条，于是他去编辑副本，
   两条记录从此各自漂移。唯一性由**唯一索引** ``ix_friend_links_url`` 承担，
   不再额外建 UNIQUE 约束——这是全库约定（对照 ``users.email`` →
   唯一索引 ``ix_users_email``）：模型侧写 ``unique=True, index=True`` 时，
   autogenerate 只生成一条唯一索引，两边长期保持一致，不会每次 diff 都冒出「缺约束」。

2. **``sort_order`` / ``is_active`` 各自建索引**。公开列表的固定读法是
   ``where is_active order by sort_order, id``：一个过滤键、一个排序键。
   数据量小时规划器多半仍走全表扫，这两个索引当下的价值是**把访问模式写进 schema**，
   详见 ``models/friend_link.py``。

3. **``created_at`` 也建索引**。``TimestampMixin.created_at`` 带 ``index=True``，
   而开发态的 ``create_all`` 会真的把它建出来——迁移里漏掉，就会让
   「迁移建出来的库」与「模型建出来的库」长期不一致，下次 autogenerate
   又把这条索引当成待补的 diff。（``b8d2f5a1c3e9`` 那条就漏了，
   属于历史遗留，不在这里返工。）

降级干净回滚：先删索引再删表，顺序与 upgrade 相反。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f4b8e2d6a3"
down_revision: str | None = "b8d2f5a1c3e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "friend_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_friend_links")),
    )
    # 一条唯一索引同时承担「唯一性」与「按 url 查重」两个职责（见模块 docstring）
    op.create_index(op.f("ix_friend_links_url"), "friend_links", ["url"], unique=True)
    op.create_index(op.f("ix_friend_links_sort_order"), "friend_links", ["sort_order"])
    op.create_index(op.f("ix_friend_links_is_active"), "friend_links", ["is_active"])
    op.create_index(op.f("ix_friend_links_created_at"), "friend_links", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_friend_links_created_at"), table_name="friend_links")
    op.drop_index(op.f("ix_friend_links_is_active"), table_name="friend_links")
    op.drop_index(op.f("ix_friend_links_sort_order"), table_name="friend_links")
    op.drop_index(op.f("ix_friend_links_url"), table_name="friend_links")
    op.drop_table("friend_links")
