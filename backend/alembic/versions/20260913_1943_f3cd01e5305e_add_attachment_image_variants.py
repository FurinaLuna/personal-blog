"""add attachment image variants column

Revision ID: f3cd01e5305e
Revises: d724beff869e
Create Date: 2026-09-13 19:43:00.000000

实现说明：给 attachments 表加 ``variants`` JSON 列，存多尺寸变体的
``{宽度档位: stored_name}``（值是文件名而不是 URL——换存储后端时不用刷库）。

SQLite 上**刻意不用 batch_alter_table**：batch 模式会重建整张表，
在已有外键的表上偶发失败并残留 ``_alembic_tmp_*`` 临时表（本机实测两次）。
该列可空、无索引、无外键，原生 ``ADD COLUMN`` 一条语句即可；
降级用 SQLite 3.35+ 的 ``DROP COLUMN``。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f3cd01e5305e"
down_revision: str | None = "d724beff869e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 可空无默认值：存量行为 NULL（表示"还没有变体"，回填接口以此筛选）
    op.execute("ALTER TABLE attachments ADD COLUMN variants JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE attachments DROP COLUMN variants")
