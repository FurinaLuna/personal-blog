"""自定义字段类型。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """时间字段：写库用 UTC，读出来一定带 UTC 时区信息。

    解决的是一个很隐蔽的**接口不一致**问题：

    - SQLite 底层把时间存成字符串，取出来的是 **naive**（无时区）datetime；
    - PostgreSQL 的 ``timestamptz`` 取出来是 **aware**（带时区）datetime；
    - 应用里新建对象时我们用的是 ``datetime.now(UTC)``，也是 aware。

    结果就是同一个字段在「刚创建」和「重新查询」两种路径下序列化出的 JSON 不一样
    （``...T14:33:07Z`` vs ``...T14:33:07``）。前端比较时间、做缓存去重时就会
    莫名其妙地失败。

    这里在读出来时统一补上 UTC —— 全站时间口径只有一种：**UTC，带时区**，
    展示时再由前端按用户本地时区格式化。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
