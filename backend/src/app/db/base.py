"""SQLAlchemy 声明式基类与公共 Mixin。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UTCDateTime

# 统一约束命名，保证 Alembic autogenerate 出来的迁移脚本命名稳定可预测
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """统一的创建 / 更新时间戳。

    ``created_at`` / ``updated_at`` 同时给了 **Python 侧** 与 **数据库侧** 的默认值，
    这一点是刻意为之：

    - Python 侧的 ``lambda`` 让 ORM 明确知道写入的是什么值，flush 之后不需要再回查；
    - 数据库侧的 ``server_default`` 则保证任何人用裸 SQL 插入时也拿得到时间戳。

    为什么不用 ``onupdate=func.now()`` 让数据库算？因为 SQLAlchemy 无法预知
    数据库函数的结果，只能在 UPDATE 之后把这些列标记为过期、等下次访问再回查。
    而异步会话里的这种惰性回查会在事件循环中发起同步 IO，直接抛
    ``MissingGreenlet`` —— 表现为「改完数据序列化响应时报 500」，排查起来非常隐蔽。
    """

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
