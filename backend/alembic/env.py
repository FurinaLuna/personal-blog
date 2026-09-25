"""Alembic 运行环境。

两个关键点：

1. **数据库地址从应用配置读取**，不写死在 alembic.ini 里。
   否则很容易出现"迁移连的是 A 库、应用连的是 B 库"，线上表现为
   "迁移明明成功了，但就是报表不存在"。
2. **必须 import 所有模型**。autogenerate 靠的是 ``Base.metadata``，
   而 metadata 只有在模型类被真正导入后才会有对应的表定义，
   少 import 一个模型，autogenerate 就会认为"这张表该删掉"。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.db.base import Base
from app.db.types import UTCDateTime

# 显式导入模型包，确保所有表都注册进 Base.metadata（见模块 docstring）
from app.models import Article, Attachment, Comment, SiteProfile, Tag, User  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# ── 迁移也过一遍生产门禁 ────────────────────────────────────────────
# ``check_production_safety()`` 原本只在 ``create_app()`` 里调用（main.py），
# 于是 Alembic、backup_db.py、e2e_run.py 这些入口**完全不过门禁** ——
# 而 Alembic 恰恰是拿着生产库权限、且最容易因为「配置还没配好就先跑迁移」
# 而第一个碰库的进程。带着默认密钥或 SQLite 上生产时，应用会拒绝启动，
# 迁移却照跑不误，表现出来就是「库建好了但服务起不来」这种最难查的形态。
# 这里复用同一个校验函数：门禁只有一个实现，不会出现两边规则漂移。
_migration_gate_problems = settings.check_production_safety()
if _migration_gate_problems:
    raise RuntimeError(
        "生产环境配置不合法，拒绝执行迁移：\n- " + "\n- ".join(_migration_gate_problems)
    )

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# FTS5 全文检索的虚拟表（以及 SQLite 自动生成的影子表）。
# 它们不在 Base.metadata 里——是**虚拟表**，由迁移或应用启动时用裸 DDL 建的。
_FTS_TABLE_PREFIX = "articles_fts"


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """告诉 autogenerate 忽略哪些对象。

    **这是一个防误删的护栏，不是洁癖。** ``articles_fts`` 是 FTS5 虚拟表，
    数据库里有、``Base.metadata`` 里没有，所以 autogenerate 会认为
    「这个表被删掉了」，于是在下次 ``make migration`` 时生成一条
    ``DROP TABLE articles_fts``——跑下去就是**搜索索引被静默删掉**，
    而且它出现在一个看起来完全无关的迁移里，review 时极容易漏过。

    SQLite 还会为虚拟表自动建 ``articles_fts_data`` / ``_idx`` / ``_docsize``
    / ``_config`` 这几张影子表，同样要一并排除。用前缀匹配，
    将来加别的 FTS 表也不用回来改。
    """
    # 等价于 return not (...)：写成提前 return 更贴合「默认全都要、只排除 FTS」的意图
    return not (type_ == "table" and name is not None and name.startswith(_FTS_TABLE_PREFIX))


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """自定义类型的迁移渲染方式。

    ``UTCDateTime`` 只在 **Python 侧** 做时区归一化，DDL 层面与
    ``DateTime(timezone=True)`` 完全一致。而 Alembic 默认会把它渲染成
    ``app.db.types.UTCDateTime(...)``，可迁移脚本里并没有 import app ——
    不处理的话 ``alembic upgrade`` 必然以 ``NameError: name 'app' is not defined``
    失败（注意：autogenerate 能成功、upgrade 才报错，很容易误以为是迁移写错了）。
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False  # False = 使用默认渲染


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库。

    用于「先生成 SQL 交给 DBA 审核」的流程：
    ``alembic upgrade head --sql > migration.sql``
    """
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_item=render_item,
        include_object=include_object,
        # SQLite 不支持 ALTER COLUMN，必须开启批处理模式
        render_as_batch=settings.database_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 让 autogenerate 能识别出"字段类型变了"和"默认值变了"，
        # 默认这两项是关闭的，会漏掉纯粹的列类型调整
        compare_type=True,
        compare_server_default=True,
        render_item=render_item,
        include_object=include_object,
        render_as_batch=settings.database_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
