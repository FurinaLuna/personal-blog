"""异步引擎 / 会话工厂 / FastAPI 依赖。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def enable_sqlite_pragmas(engine: AsyncEngine) -> None:
    """打开 SQLite 的外键约束与 WAL。

    SQLite 默认 **不** 强制外键约束（``PRAGMA foreign_keys`` 默认 OFF），
    这意味着 ``ON DELETE CASCADE`` 写了也不会生效，删文章时评论会全部变成孤儿行。
    必须每个连接显式打开，且要在 ``connect`` 事件里做——连接池里的连接是复用的。
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def build_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """按方言装配引擎参数。测试也会复用它，保证与生产同一套配置。"""
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if database_url.startswith("sqlite"):
        # SQLite 在多线程 / 异步场景下需要放开 check_same_thread
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=3600)
    return create_async_engine(database_url, **kwargs)


engine = build_engine(settings.database_url, echo=settings.db_echo)
enable_sqlite_pragmas(engine)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # commit 后仍可读对象属性，避免响应序列化时触发额外查询
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """请求级会话：异常回滚、安全网提交、无论如何关闭。

    事务的**正式提交**由写路由处理函数末尾显式执行（``await session.commit()``），
    一个 HTTP 请求仍对应一个事务，避免出现「文章存了但标签没存」这类半成品数据。

    为什么不能把唯一提交放在这里（依赖 teardown）：FastAPI 对 yield 依赖的收尾
    代码在**响应发送之后**才运行，浏览器连接池的下一个请求会在提交落地前开启
    读事务，偶发读到旧快照（read-your-writes 被破坏，双连接实测约 6% 复现率：
    create 后读不到 3/50、delete 后仍可读 2/50）。

    这里的 commit 保留为安全网：正常路径下写路由已提交，此处是空操作；
    未来若新增写路径漏了显式提交，数据仍不丢（代价仅是该路径偶发旧读）。
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
