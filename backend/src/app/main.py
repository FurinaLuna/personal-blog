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

from app import __version__
from app.api.feed import router as feed_router
from app.api.middleware import RequestContextMiddleware
from app.api.v1 import api_router
from app.config import settings
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models import User  # noqa: F401 - 触发所有模型注册到 Base.metadata
from app.services.seed import ensure_seed
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

    async with async_session_factory() as session:
        try:
            await ensure_seed(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("初始化数据失败，应用继续启动但可能缺少默认账号")
            raise

    logger.info("%s 已启动（env=%s）", settings.app_name, settings.app_env)
    yield
    await engine.dispose()


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
        "（这些默认值都写在开源仓库里，等同于公开。逐条修正 backend/.env 后重启即可。）"
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

    # 请求 ID 与访问日志。放在 CORS 之前注册：中间件是「后注册先执行」，
    # 这样即使请求被 CORS 拒绝，也能在日志里留下带 request_id 的记录。
    app.add_middleware(RequestContextMiddleware)

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
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready", "database": "up", "env": settings.app_env},
        )

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
