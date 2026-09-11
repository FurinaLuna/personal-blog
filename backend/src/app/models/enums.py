"""领域枚举。

全部使用 Python 3.11+ 的 ``StrEnum``：成员本身就是字符串，因此
``json.dumps(UserRole.ADMIN)`` 直接得到 ``"admin"``，不需要再写 ``.value``，
也不会出现 ``"UserRole.ADMIN"`` 这种把类名序列化进去的低级事故。

数据库侧统一以字符串存储（``native_enum=False``），跨 SQLite / PostgreSQL 行为一致，
Alembic 迁移也不会因为 PG 原生 ENUM 类型而需要额外处理。
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """用户角色。访客不是用户，是未认证请求，因此不在此枚举中。"""

    ADMIN = "admin"  # 站长：全站任意操作
    AUTHOR = "author"  # 作者：只能操作自己的文章 / 附件


class ArticleStatus(StrEnum):
    """文章状态机：draft -> published -> archived，均可逆。"""

    DRAFT = "draft"  # 草稿：仅作者与管理员可见
    PUBLISHED = "published"  # 已发布：所有人可见
    ARCHIVED = "archived"  # 已归档：直达链接可见，但不出现在列表


class ArticleSort(StrEnum):
    """文章列表排序枚举。

    做成枚举而不是「直接拼 ORDER BY 字符串」，是为了让排序字段走白名单：
    用户输入永远只会在这些成员里选，不存在把任意字符串拼进 SQL 的可能。
    """

    LATEST = "latest"
    OLDEST = "oldest"
    HOTTEST = "hottest"  # 按阅读量
    UPDATED = "updated"
    TITLE = "title"


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """SQLAlchemy ``Enum`` 的 ``values_callable``。

    SQLAlchemy 默认把枚举的**成员名**写进数据库（``ADMIN``），而我们想要的是
    **值**（``admin``）——这样库里存的东西与 API 输出一致，写裸 SQL 排查问题时
    不会看到一堆大写常量。所有枚举字段都复用它，保证全站口径统一。
    """
    return [member.value for member in enum_cls]
