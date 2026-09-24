"""create guestbook_messages

Revision ID: ec88a7148baa
Revises: c1f4b8e2d6a3
Create Date: 2026-09-24 22:45:00.000000

实现说明（留言板）：

全新建表，没有存量数据要搬运。三个决定值得写下来：

1. **一条留言一个回复**：``reply_content`` / ``replied_at`` / ``replied_by_id``
   三列直接落在本表，刻意不像 ``comments`` 那样做 ``parent_id`` 自引用。
   「不做楼中楼」因此是**结构上**成立的，而不是靠一条运行时校验拦住——
   少一个可以被建模消除的失败模式，前台也永远不必递归渲染。

2. **``is_approved`` 建索引**。公开列表的固定读法就是
   ``where is_approved order by id desc``：一个过滤键 + 一个排序键。
   几十行的表上规划器多半仍走全表扫，这个索引当下的价值是**把访问模式写进
   schema**（与 ``friend_links`` 的 ``is_active`` 同一理由）。

3. **``replied_by_id`` 用 ``SET NULL`` 而不是 ``CASCADE``**。它是纯审计列：
   删掉一个后台账号不该顺手把留言（以及它的回复内容）一起删掉。
   外键名沿用全库命名约定（见 ``db/base.py`` 的 ``NAMING_CONVENTION``）。

``created_at`` 也要建索引：``TimestampMixin.created_at`` 带 ``index=True``，
开发态的 ``create_all`` 会真的把它建出来；迁移里漏掉就会让「迁移建出来的库」
与「模型建出来的库」长期不一致，下次 autogenerate 又把它当成待补的 diff。
（``b8d2f5a1c3e9`` 那条漏过一次，属于历史遗留，不在这里返工。）

降级干净回滚：先删索引再删表，顺序与 upgrade 相反。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ec88a7148baa"
down_revision: str | None = "c1f4b8e2d6a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guestbook_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_name", sa.String(length=50), nullable=False),
        sa.Column("author_email", sa.String(length=255), nullable=True),
        sa.Column("author_site", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_approved", sa.Boolean(), nullable=False),
        sa.Column("reply_content", sa.Text(), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_by_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["replied_by_id"],
            ["users.id"],
            name=op.f("fk_guestbook_messages_replied_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guestbook_messages")),
    )
    op.create_index(
        op.f("ix_guestbook_messages_is_approved"), "guestbook_messages", ["is_approved"]
    )
    op.create_index(op.f("ix_guestbook_messages_created_at"), "guestbook_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_guestbook_messages_created_at"), table_name="guestbook_messages")
    op.drop_index(op.f("ix_guestbook_messages_is_approved"), table_name="guestbook_messages")
    op.drop_table("guestbook_messages")
