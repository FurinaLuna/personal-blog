"""pytest 全局夹具。

关键设计：**在导入任何 app 模块之前**先把环境变量改掉。
``app.config.settings`` 是模块级单例，一旦 app 被 import 就固化了数据库地址，
事后再改环境变量是无效的——这是测试里最常见的一类"改了却没生效"。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

TEST_DB = Path(tempfile.gettempdir()) / "personal_blog_test.db"

os.environ["APP_ENV"] = "testing"
os.environ["DEBUG"] = "false"
os.environ["JWT_SECRET_KEY"] = "testing-secret-key-not-for-production"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "admin123456"
os.environ["MAX_UPLOAD_SIZE"] = str(2 * 1024 * 1024)
os.environ["STORAGE_DIR"] = (Path(tempfile.gettempdir()) / "personal_blog_media").as_posix()

# 上面的环境变量必须早于下面这些 import，故有意忽略 E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import ensure_seed  # noqa: E402
from app.utils.ratelimit import limiter  # noqa: E402
from tests.factories import ADMIN_PASSWORD, AUTHOR_PASSWORD  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _clean_database_file() -> None:
    """整个测试会话开始前删掉上一次残留的库文件。"""
    TEST_DB.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """每个用例前清空限流计数。

    必须存在：测试里用同一个 ASGI client 反复登录，配额（5 次/分）会被跨用例耗尽，
    表现是一批完全无关的用例接连失败——看起来像业务被改坏了，实际只是计数器没重置。
    """
    limiter.reset()


@pytest.fixture
async def db_reset() -> AsyncGenerator[None, None]:
    """每个用例一张干净的表结构 + 只有站长的基础数据。

    刻意不灌演示文章：用例自己造数据，断言才不会被"示例内容"干扰，
    也不会出现"A 用例删了文章导致 B 用例失败"的顺序依赖。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        await ensure_seed(session)
        await session.commit()
    yield


@pytest.fixture
async def client(db_reset: None) -> AsyncGenerator[AsyncClient, None]:
    """直连 ASGI 的异步客户端。

    不走真实端口、不需要起 uvicorn，跑得快也不占资源。
    注意 ASGITransport 不会触发 lifespan，建表由 ``db_reset`` 负责——
    这是刻意的：测试不应依赖应用的启动副作用。
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def login(client: AsyncClient, username: str, password: str) -> str:
    """登录并返回 access token。"""
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def admin_token(client: AsyncClient) -> str:
    return await login(client, "admin", ADMIN_PASSWORD)


@pytest.fixture
async def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
async def author(client: AsyncClient, admin_headers: dict[str, str]) -> dict[str, object]:
    """创建作者账号，返回 id 与已带 token 的请求头。"""
    from tests.factories import make_user_payload

    response = await client.post(
        "/api/v1/auth/users",
        json=make_user_payload(username="writer", email="writer@example.com", nickname="写手"),
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    token = await login(client, "writer", AUTHOR_PASSWORD)
    return {"id": created["id"], "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
async def author_headers(author: dict[str, object]) -> dict[str, str]:
    return author["headers"]  # type: ignore[return-value]


@pytest.fixture
async def author_id(author: dict[str, object]) -> int:
    return int(author["id"])  # type: ignore[arg-type]


@pytest.fixture
async def published_article(
    client: AsyncClient, author_headers: dict[str, str]
) -> dict[str, object]:
    """一篇已发布的文章，作为详情/评论/点赞等用例的现成素材。"""
    from tests.factories import make_article_payload

    response = await client.post(
        "/api/v1/articles", json=make_article_payload(), headers=author_headers
    )
    assert response.status_code == 201, response.text
    return response.json()
