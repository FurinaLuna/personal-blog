"""create article_revisions

Revision ID: e1f2a3b4c5d6
Revises: d9e5f6a7b8c0
Create Date: 2026-09-17 19:30:00.000000

实现说明（文章版本历史）：全新建表，无存量数据负担。

关于存量文章：迁移**不**为已有文章补一条初始版本。理由是「版本」的语义是
「某次编辑前的样子」，凭空造一条与当前内容一模一样的「历史版本」只会让
版本列表里出现一条永远没人会去恢复的噪音。第一次真正编辑时才产生第一条快照。

索引与模型保持一致（对照 ArticleRevision.__table_args__ 与 TimestampMixin）：
- (article_id, created_at) 复合索引服务「某篇文章的全部版本按时间倒序」这个主查询；
- created_at 单列索引来自 TimestampMixin（漏了它模型与库会长期不一致）；
- article_id 单列索引由 index=True 生成。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d9e5f6a7b8c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # **幂等**：`DB_AUTO_CREATE=true`（开发默认）时这张表由 create_all 建出来，
    # 而 alembic_version 可能还停在旧版本；此时 upgrade 会因「表已存在」失败。
    if sa.inspect(op.get_bind()).has_table("article_revisions"):
        return

    op.create_table(
        "article_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        # 恢复来源：指向同表的另一条版本，所以只是普通整数而不是外键——
        # 用外键的话，清理旧版本时会连带把「谁从哪一版恢复的」也删掉或置空，
        # 而它在源版本被删之后依然是有意义的信息（「这版是恢复来的」）。
        sa.Column("restored_from_id", sa.Integer(), nullable=True),
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
        # 文章删除时版本跟着删：孤立版本无法恢复，只是垃圾
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name=op.f("fk_article_revisions_article_id_articles"),
            ondelete="CASCADE",
        ),
        # 用户被删时置空而不是级联：版本历史不该因为删了个账号就消失
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_article_revisions_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_article_revisions")),
    )
    op.create_index(
        op.f("ix_article_revisions_article_id"), "article_revisions", ["article_id"], unique=False
    )
    op.create_index(
        op.f("ix_article_revisions_created_at"), "article_revisions", ["created_at"], unique=False
    )
    op.create_index(
        "ix_article_revisions_article_created",
        "article_revisions",
        ["article_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_article_revisions_article_created", table_name="article_revisions")
    op.drop_index(op.f("ix_article_revisions_created_at"), table_name="article_revisions")
    op.drop_index(op.f("ix_article_revisions_article_id"), table_name="article_revisions")
    op.drop_table("article_revisions")
