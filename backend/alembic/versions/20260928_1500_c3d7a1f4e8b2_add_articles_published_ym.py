"""add articles.published_ym for month archive queries

Revision ID: c3d7a1f4e8b2
Revises: b8e4d1c7a920
Create Date: 2026-09-28 15:00:00.000000

## 为什么需要

按月归档的谓词原来是**列的函数**：

    substr(cast(coalesce(published_at, created_at) as text), 1, 7) == '2026-09'

任何索引都服务不了它，于是「取某个月的条目」只能全表扫描 + 回表取整行；
``archive_groups`` 又逐月查一次（月份数个位数的前提下那个 N+1 是有意选择），
3 年博客就是 1 + 36 次全表扫描，而端点公开、无鉴权、无限流。

这里把「有效发布时间的 YYYY-MM」materialise 成 ``published_ym``，谓词退化成裸列
比较，``(status, published_ym)`` 索引才用得上。见
``repositories/article_query_repository.py`` 的 ``list_archive`` / ``list_by_month``
与 ``tests/test_indexes.py`` 的查询计划断言。

## 回填规则必须与代码一致

``COALESCE(published_at, created_at)`` —— 与 ``models/article.py`` 的
``derive_published_ym()`` 逐字同规则：已发布用 ``published_at``（含定时发布的
未来时间），草稿回退到 ``created_at``。规则两边写两份是因为**迁移不能 import
应用代码**（那样「以后改了应用代码」会反过来改掉历史迁移的行为），
所以这里刻意留一份字面量，改任何一边都要同时改另一边。

只回填 ``published_ym IS NULL`` 的行：迁移与「带新代码的实例」可能同时在跑，
新代码写进去的值一定是对的，回填不该覆盖它。

## 为什么可空、为什么不在迁移里加 NOT NULL

SQLite 的 ``ALTER TABLE`` 加不了 NOT NULL 约束（``ADD COLUMN`` 要求常量默认值），
而「模型与迁移两条建表路径结构必须一致」是这个项目反复踩过的坑
（见 ``models/article.py`` 里 ``series_order`` 的注释），所以模型侧同样是
``nullable=True``。语义上 NULL 只代表「这一行还没有任何可用时间」，
落库的行总有值（``created_at`` 非空）。

## 方言边界

``substr(CAST(... AS TEXT), 1, 7)`` 是 SQLite 与 PostgreSQL 都能跑的写法
（这正是原谓词选它的理由）。一个已知的、可接受的差异：PG 把 timestamptz 转文本时
按**会话时区**渲染，所以恰好落在月初/月末零点几小时内的历史行，回填出的月份可能
与「Python 侧按 UTC 计算」的新行差一个月。上一版谓词由数据库自己算，同样是这个口径，
所以这不是本次引入的漂移；真要消除得改成按 UTC 显式格式化，代价是两套方言两套写法。

## 回滚

``downgrade`` 先删索引再删列 —— 顺序不能反：SQLite 不允许 ``DROP COLUMN``
于「被索引引用的列」。``DROP COLUMN`` 需要 SQLite ≥ 3.35（Python 3.11 自带的
远高于此）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d7a1f4e8b2"
down_revision: str | None = "b8e4d1c7a920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "articles"
_COLUMN = "published_ym"
_INDEX = "ix_articles_status_published_ym"
_INDEX_COLUMNS = ["status", _COLUMN]

# 与 ``models/article.py::derive_published_ym`` 同规则（刻意不复用：迁移要能在任意
# 历史版本的代码上重放，见模块 docstring）。
_BACKFILL = sa.text(
    f"UPDATE {_TABLE} "
    f"SET {_COLUMN} = substr(CAST(COALESCE(published_at, created_at) AS TEXT), 1, 7) "
    f"WHERE {_COLUMN} IS NULL"
)


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _index_names() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(_TABLE)}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        # 空库（例如只跑了 initial schema 之前的迁移）：没有可加列的表，跳过而不是报错
        return

    if _COLUMN not in _column_names():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=7), nullable=True))

    # 存量行回填。幂等：只看 NULL，新代码已经写好的值不动。
    op.get_bind().execute(_BACKFILL)

    if _INDEX not in _index_names():
        op.create_index(_INDEX, _TABLE, _INDEX_COLUMNS, unique=False)


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return

    if _INDEX in _index_names():
        op.drop_index(_INDEX, table_name=_TABLE)
    if _COLUMN in _column_names():
        op.drop_column(_TABLE, _COLUMN)
