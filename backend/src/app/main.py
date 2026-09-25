"""应用入口。

装配顺序（顺序本身是有讲究的）：
1. 建目录 → 2. 建静态文件挂载（挂载时目录必须存在）→ 3. 注册异常处理 →
4. 注册中间件 → 5. 注册路由 → 6. lifespan 里建表 + 灌初始数据。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import __version__
from app.api.cache import PublicCacheMiddleware
from app.api.feed import router as feed_router
from app.api.middleware import HeadMethodMiddleware, RequestContextMiddleware
from app.api.v1 import api_router
from app.config import settings
from app.db.base import Base
from app.db.fulltext import (
    ensure_search_indexes,
    get_search_index_status,
    set_search_index_status,
)
from app.db.session import async_session_factory, engine
from app.models import User  # noqa: F401 - 触发所有模型注册到 Base.metadata
from app.services.auth_service import AuthService
from app.services.seed import ensure_seed
from app.services.visit_stats_service import VisitStatsService
from app.utils.exceptions import DomainError, UnauthorizedError
from app.utils.logging import get_request_id, setup_logging
from app.utils.storage import storage

logger = logging.getLogger("blog")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """启动 / 关闭钩子。"""
    storage.ensure_dirs()

    if settings.db_auto_create:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # 检索索引**不**跟着 db_auto_create 走：
    #   - SQLite 的 FTS5 是虚拟表，本来就不在 Base.metadata 里，create_all 建不出来；
    #   - PG 的 pg_trgm + GIN 由迁移建，但托管库常常不给 CREATE EXTENSION 权限，
    #     迁移里那一步会失败，而失败的表现只是"搜索慢"，没人会发现。
    # 所以两种部署形态都要探测一次：它是幂等的（IF NOT EXISTS），
    # 顺带还能把 FTS5 索引 rebuild 到与正文一致。
    #
    # 放在 db_auto_create 之外还有一个理由：生产是 DB_AUTO_CREATE=false，
    # 若跟着它走，恰好是**最需要这个信号的环境永远拿不到信号**。
    await _ensure_fulltext_index()

    async with async_session_factory() as session:
        try:
            await ensure_seed(session)
            await session.commit()
        except Exception:
            await session.rollback()
            # 日志必须与实际行为一致：这里是 raise，应用**不会**启动。
            # 之前写的是「应用继续启动但可能缺少默认账号」，一旦真出事，
            # 看日志的人会以为只是缺个账号，而进程其实已经退出了。
            logger.exception("初始化数据失败，应用将拒绝启动（见上方异常原因）")
            raise

    await _prune_visit_logs()
    await _prune_refresh_sessions()

    logger.info("%s 已启动（env=%s）", settings.app_name, settings.app_env)
    yield
    await engine.dispose()


async def _ensure_fulltext_index() -> None:
    """补建/修复检索索引（尽力而为，失败不影响启动）。

    按方言分流，两边的 DDL 都幂等：

    - **SQLite**：建 FTS5 虚拟表与三个触发器，末尾 rebuild 一次把索引对齐到
      articles 的当前内容——顺带修复「触发器曾经缺失导致索引与正文脱节」；
    - **PostgreSQL**：建 ``pg_trgm`` 扩展与三条 GIN 索引。此前 PG 分支是直接跳过，
      于是生产库上的搜索永远是三列 ``ILIKE '%kw%'`` 全表扫描。
      没有 CREATE EXTENSION 权限时会失败，此时只记日志：搜索依然正确，只是慢。

    同 ``_prune_visit_logs``：搜索索引是增强功能，不是服务可用性的前提。
    建不出来就退回不带索引的 LIKE，不能因为一个增强功能把站点挡在门外。
    """
    try:
        async with async_session_factory() as session:
            kind = await ensure_search_indexes(session)
            await session.commit()
        if kind == "fts5":
            set_search_index_status("fts5")
            logger.info("全文检索索引已就绪（SQLite FTS5）")
        elif kind == "pg_trgm":
            set_search_index_status("pg_trgm")
            logger.info("全文检索索引已就绪（PostgreSQL pg_trgm + GIN）")
        else:
            set_search_index_status("", "当前数据库未建立检索索引")
            logger.info("当前数据库未建立检索索引，搜索将使用无索引的 LIKE")
    except Exception:  # 增强功能不能成为启动的单点故障
        # 退化是**静默**的（结果仍然正确，只是全表扫），所以要把原因留下来：
        # /ready 会把它吐出去，否则"搜索突然变慢"只能靠用户抱怨才能发现。
        reason = "检索索引初始化失败（最常见的是 PG 没有 CREATE EXTENSION 权限）"
        set_search_index_status("", reason)
        logger.warning("%s；搜索将退回无索引 LIKE，不影响启动", reason, exc_info=True)


async def _prune_refresh_sessions() -> None:
    """启动时清理过期的刷新会话（尽力而为）。

    与访问日志同一类问题：`refresh_sessions` 每次登录/刷新都写一行，
    只增不减的话备份体积与迁移耗时都会慢慢变难看，而收益是零。
    同一个"尽力而为、失败不影响启动"的取舍。
    """
    try:
        async with async_session_factory() as session:
            removed = await AuthService(session).prune_expired_sessions()
            await session.commit()
        if removed:
            logger.info("已清理 %d 条过期的刷新会话", removed)
    except Exception:
        logger.warning("刷新会话清理失败（不影响启动）", exc_info=True)


async def _prune_visit_logs() -> None:
    """启动时清理过期的访问日志（尽力而为）。

    放在启动而不是定时任务里：个人博客是单实例部署，没有调度器；
    而这个操作是走索引的范围删除，在几十万行的量级上是毫秒级，
    对启动时间没有可感知影响。

    **失败绝不影响启动**：清理是维护动作，不是服务可用性的前提。
    数据库暂时不可用时，宁可带着旧日志启动，也不能因为一个维护任务
    把整个站点挡在门外。异常只记日志。
    """
    try:
        async with async_session_factory() as session:
            removed = await VisitStatsService(session).prune()
            await session.commit()
        if removed:
            logger.info("已清理 %d 条超过留存期的访问日志", removed)
    except Exception:
        logger.warning("访问日志清理失败（不影响启动）", exc_info=True)


def _register_exception_handlers(app: FastAPI) -> None:
    """把领域异常翻译成 HTTP 响应。

    这样 service 层就可以完全不知道 HTTP 的存在，只抛 ``NotFoundError`` 之类的
    领域概念，将来换成 CLI 或定时任务入口时不用改一行业务代码。
    """

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, UnauthorizedError) else {}
        # 异常自带的响应头优先（例如 429 的 Retry-After），其余用默认的
        headers.update(exc.headers)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "code": exc.code,
                "request_id": get_request_id(),
            },
            headers=headers or None,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
        """把数据库完整性约束冲突翻译成 409，而不是 500。

        「先查重、再插入」这个模式在本项目里到处都是（用户名 / 邮箱 / slug /
        分类名 / 标签名），而两步之间永远存在竞态窗口：两个请求同时通过查重，
        其中一个必然在 INSERT 时撞唯一键。**这不是 bug，是并发下的正常结果**，
        正确的回答是 409「已存在」，而不是 500「服务器开小差了」——
        500 会让用户以为是自己操作错了或者站点坏了。

        ``get_session`` 依赖已经回滚了事务，这里只负责把它翻译成合适的响应。
        原始异常写进日志（带上 request_id），但不回给客户端：
        数据库错误信息会暴露表名与列名。
        """
        logger.warning("数据库完整性约束冲突：%s", exc, exc_info=True)
        return JSONResponse(
            status_code=409,
            content={
                "detail": "该内容已存在（可能是同名的分类、标签、用户名、邮箱或链接别名）",
                "code": "conflict",
                "request_id": get_request_id(),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """把 Pydantic 的校验错误整理成人能看懂的结构。

        默认返回的 ``loc`` 形如 ``["body", "password"]``，前端得自己剥掉第一层，
        这里直接给出 ``field`` 字段，前端标红输入框时不用再猜。
        """
        errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"),
                "message": err.get("msg", "参数不合法"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "detail": errors,
                "code": "validation_error",
                "request_id": get_request_id(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        """兜底 500：把**任何**未捕获异常翻译成统一信封。

        没有它的时候，未捕获异常会落到 Starlette 自带的 ServerErrorMiddleware，
        返回纯文本 ``Internal Server Error``：
        前端拿到的是一段 HTML/纯文本，没法归一化成「服务器开小差了」的提示，
        运维也拿不到 ``request_id`` 去把用户报错对应到日志——
        线上排障时这等于只有一半信息（用户说 500，日志里找不到是哪一条）。

        FastAPI 会优先匹配更具体的处理器，所以上面三个
        （DomainError / IntegrityError / RequestValidationError）不受影响。

        文案刻意不回显异常细节：数据库错误、路径、乃至表结构都是信息泄露。
        细节只进日志（带堆栈），排查走 request_id。
        """
        logger.exception("未捕获异常（request_id=%s）：%s", get_request_id(), exc)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "服务器内部错误",
                "code": "internal_error",
                "request_id": get_request_id(),
            },
        )


def _enforce_production_safety() -> None:
    """生产启动门禁：带着开发默认值上生产就直接拒绝启动。

    这类问题的代价不可逆（密钥一旦公开就不再是秘密），所以选择「启动失败」
    而不是「打条警告继续跑」——警告在容器日志里没人看，而启动失败一定会被处理。

    Raises:
        RuntimeError: 生产环境下仍在使用不安全的默认配置。
    """
    problems = settings.check_production_safety()
    if not problems:
        return
    detail = "\n".join(f"  {index}. {text}" for index, text in enumerate(problems, start=1))
    raise RuntimeError(
        "检测到生产环境使用不安全的默认配置，已拒绝启动：\n"
        f"{detail}\n"
        "（这些默认值都写在开源仓库里，等同于公开。逐条修正后重启即可："
        "Docker 部署改**项目根目录**的 .env，裸机部署改 backend/.env。）"
    )


def create_app() -> FastAPI:
    """应用工厂。测试里也用它，保证测试环境的 app 与生产同构。"""
    _enforce_production_safety()

    setup_logging(
        json_output=settings.log_json,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "个人博客后端 API。全站 Markdown 存储、JWT 认证、分层架构"
            "（api / services / repositories / models）。"
        ),
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── 中间件注册顺序：**后注册的在外层** ───────────────────────────────
    #
    # 这条规则是实测出来的，不是推测：按「RequestContext → CORS」注册时，
    # 发一个来自非白名单 Origin 的预检请求，返回的 400 响应里**没有**
    # X-Request-ID —— 说明 CORS 在外面把请求截住了，RequestContext 没跑到。
    # 也就是说原先的注释（"放在 CORS 之前注册…即使被 CORS 拒绝也能留下记录"）
    # 与代码效果**恰好相反**：被 CORS 拒掉的请求在日志里完全消失。
    #
    # 因此这里刻意按「内 → 外」注册：
    #
    #   RequestContext（最外：所有请求都要留痕，包括被 CORS 拒的）
    #     └── Head（把 HEAD 改写成 GET 交给路由，最后丢正文）
    #           └── PublicCache（对**完整**正文取哈希算 ETag，把 200 折叠成 304）
    #                 └── CORS
    #                       └── 路由
    #
    # Head 与 PublicCache 的先后不能颠倒：PublicCache 的 ETag 是对响应体取
    # 哈希得到的，如果把 Head 放在它内层，正文会先被清空 ⇒ 所有资源的 ETag
    # 都变成"空正文的哈希"（同一个值），缓存语义直接失效。
    # Head 放在 RequestContext 内层则是为了让访问日志仍记录 HEAD 而不是 GET。

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        # X-Request-ID 要放开，否则浏览器读不到响应头，前端无法把它显示在错误提示里
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    # 公开读接口的 ETag / Cache-Control（详见 api/cache.py）
    app.add_middleware(PublicCacheMiddleware)

    # HEAD 复用 GET（FastAPI 不像 Starlette 那样给 GET 自动补 HEAD，见
    # HeadMethodMiddleware 的说明）。必须在 PublicCache 之外、RequestContext 之内。
    app.add_middleware(HeadMethodMiddleware)

    # 请求 ID 与访问日志（最外层）
    app.add_middleware(RequestContextMiddleware)

    _register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    # RSS / sitemap 挂在根路径：它们是给阅读器和搜索引擎的约定俗成地址
    app.include_router(feed_router)

    @app.get("/health", tags=["运维"], summary="健康检查")
    async def health() -> dict[str, str]:
        """给负载均衡 / 容器编排用的探活接口。

        只报进程存活与配置环境，不暴露版本细节之外的内部信息。
        """
        return {"status": "ok", "env": settings.app_env, "version": __version__}

    @app.get("/ready", tags=["运维"], summary="就绪检查")
    async def ready() -> JSONResponse:
        """就绪探针：除了进程存活，还要真的能查到数据库。

        ``/health`` 是存活探针（liveness），挂了就重启实例；
        ``/ready`` 是就绪探针（readiness），数据库连不上时应该先把流量摘掉，
        而不是重启——重启解决不了依赖故障，还会让故障面扩大。
        按约定用 503 表示「未就绪」，编排系统据此摘除实例。
        """
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:  # 探针语义就是「任何异常都算不就绪」，这里刻意宽捕获
            logger.warning("就绪检查失败：%s", exc)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "database": "down"},
            )
        # 检索索引状态一并报出来，但**不影响**就绪判定：
        # 它是增强项，索引缺失时搜索结果依然正确（只是全表扫），
        # 拿它摘流量等于"搜索慢 → 整站下线"，比问题本身严重得多。
        # 报出来的目的是让退化**可观测**，由监控去告警，而不是让编排系统去重启。
        content = {"status": "ready", "database": "up", "env": settings.app_env}
        content.update(get_search_index_status().as_payload())
        return JSONResponse(status_code=status.HTTP_200_OK, content=content)

    @app.get("/", tags=["运维"], summary="服务信息")
    async def root() -> dict[str, str]:
        return {"name": settings.app_name, "version": __version__, "docs": "/docs"}

    # 挂载媒体目录。必须在 ensure_dirs 之后，否则 StaticFiles 会因为目录不存在而启动失败。
    storage.ensure_dirs()
    app.mount(
        settings.media_url_prefix,
        StaticFiles(directory=str(settings.storage_dir)),
        name="media",
    )

    return app


app = create_app()
