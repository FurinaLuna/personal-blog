"""create visit_logs

Revision ID: a7b3c9d2e4f1
Revises: f3cd01e5305e
Create Date: 2026-09-13 20:40:00.000000

实现说明：访问日志表（统计趋势数据源），全新建表无存量数据迁移负担。
``ip_hash`` 是 HMAC-SHA256 十六进制定长 64；两个复合索引对应两类聚合查询：
(article_id, date) 单文章趋势、(date) 全站仪表盘曲线。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a7b3c9d2e4f1"
down_revision: str | None = "f3cd01e5305e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "visit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
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
            ["article_id"],
            ["articles.id"],
            name=op.f("fk_visit_logs_article_id_articles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_logs")),
    )
    op.create_index(
        op.f("ix_visit_logs_article_id_date"), "visit_logs", ["article_id", "date"], unique=False
    )
    op.create_index(op.f("ix_visit_logs_date"), "visit_logs", ["date"], unique=False)
    op.create_index(op.f("ix_visit_logs_created_at"), "visit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_visit_logs_created_at"), table_name="visit_logs")
    op.drop_index(op.f("ix_visit_logs_date"), table_name="visit_logs")
    op.drop_index(op.f("ix_visit_logs_article_id_date"), table_name="visit_logs")
    op.drop_table("visit_logs")
