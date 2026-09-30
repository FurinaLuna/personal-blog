"""add site_profile.contact_qrcodes

Revision ID: d5e9b2a7c418
Revises: 4fca195eccc9
Create Date: 2026-09-28 21:00:12.331907

## 为什么需要这一列

前台文章详情页要加「联系站长」二维码弹层，每张卡片需要四个值：``kind``
（决定微信 / QQ 图标）、``label``（展示名）、``image_url``（二维码图）、
``value``（微信号 / QQ 号，供一键复制）。这是一个**站点级**的展示配置，
和 ``social_links`` / ``skills`` 同类，所以放进 ``site_profile`` 这张单行表，
而不是为四五个展示字段另开一张 ``contact_qrcodes`` 表 —— 后者要多一套
仓储、外键和排序字段，换来的只是「更像关系模型」这一条好处。

存 JSON 而不是四条并排的列，理由是**条数不固定**：站长可能只配一个微信，
也可能微信 + QQ 各一个（计划 §3.7 的弹层上限就是 2 条，但那是前端的排布
取舍，不是数据层的约束）。与 ``social_links`` 完全同构，写读两侧可以
照抄同一套模式。

## 为什么可以空、为什么不给 server_default

这一列是 ``nullable=True``，也**刻意不给 server_default**：
``ADD COLUMN`` 一个可空列时，已有行的值天然就是 NULL，不需要回填 ——
``server_default`` 在这里只会变成一个永久留在表结构上的 ``'[]'``，
让「从没配过」和「配过又被清空」在库里呈现两种不同的存储形态，
而展示层两者完全等价（都是「这一块不渲染」）。少一个默认值就少一处
需要判断的分支。

## 回滚

``downgrade`` 删列。删掉之后二维码配置整体消失，前台「联系站长」弹层
退回到不存在这个能力的状态 —— 与升级前一致，不会留下"列没了但代码还在写"
的半开状态（回滚本来就要求同时回滚代码）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e9b2a7c418"
down_revision: str | None = "4fca195eccc9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "site_profile",
        sa.Column("contact_qrcodes", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("site_profile", "contact_qrcodes")
