"""create notification_opt_outs

Revision ID: c8d4e5f6a7b9
Revises: a7b3c9d2e4f1
Create Date: 2026-09-13 21:00:00.000000

实现说明：评论邮件通知退订表（ROADMAP 1.5）。全新建表，无存量数据负担。
email 归一小写后唯一——插入方（服务层 token 解码）负责归一；
唯一索引同时服务「发信前查是否退订」的高频点查。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c8d4e5f6a7b9"
down_revision: str | None = "a7b3c9d2e4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_opt_outs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_opt_outs")),
    )
    # 与全库约定一致：email 的 unique+index 生成**一条唯一索引**，不再额外建
    # unique 约束（对照 users.email → ix_users_email unique=True）。
    op.create_index(
        op.f("ix_notification_opt_outs_email"), "notification_opt_outs", ["email"], unique=True
    )
    # TimestampMixin.created_at 带 index=True，漏了它模型与库就会长期不一致。
    op.create_index(
        op.f("ix_notification_opt_outs_created_at"),
        "notification_opt_outs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_opt_outs_created_at"), table_name="notification_opt_outs")
    op.drop_index(op.f("ix_notification_opt_outs_email"), table_name="notification_opt_outs")
    op.drop_table("notification_opt_outs")
