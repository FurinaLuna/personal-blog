"""add series (collection) to articles

Revision ID: d724beff869e
Revises: a3dc652d83d8
Create Date: 2026-09-13 01:17:24.750914

实现说明：SQLite 上给 articles 加外键列**刻意不用 batch_alter_table**。
batch 模式会重建整张表（CREATE _alembic_tmp_articles → 拷贝 → 换名），
在已有多个外键的表上偶发失败并残留临时表（本机实测两次）。
SQLite 3.35+ 原生支持 ``ADD COLUMN ... REFERENCES``（默认值 NULL 即可）
与 ``DROP COLUMN``，直接手写 SQL 更稳。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d724beff869e"
down_revision: str | None = "a3dc652d83d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series")),
    )
    op.create_index(op.f("ix_series_created_at"), "series", ["created_at"], unique=False)
    op.create_index(op.f("ix_series_name"), "series", ["name"], unique=True)
    op.create_index(op.f("ix_series_slug"), "series", ["slug"], unique=True)

    # 原生 SQL 加列：SQLite 允许带 REFERENCES 的 ADD COLUMN（默认值 NULL），
    # 不触发整表重建
    op.execute(
        "ALTER TABLE articles ADD COLUMN series_id INTEGER "
        "REFERENCES series(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE articles ADD COLUMN series_order INTEGER NOT NULL DEFAULT 0"
    )
    op.create_index(op.f("ix_articles_series_id"), "articles", ["series_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_articles_series_id"), table_name="articles")
    # SQLite 3.35+ 支持 DROP COLUMN（Python 3.13 自带的 SQLite 满足）
    op.execute("ALTER TABLE articles DROP COLUMN series_order")
    op.execute("ALTER TABLE articles DROP COLUMN series_id")

    op.drop_index(op.f("ix_series_slug"), table_name="series")
    op.drop_index(op.f("ix_series_name"), table_name="series")
    op.drop_index(op.f("ix_series_created_at"), table_name="series")
    op.drop_table("series")
