"""未捕获异常的统一信封（兜底 500）。

## 防的是什么

没有 ``Exception`` 处理器时，任何漏网的异常都会落到 Starlette 自带的处理器，
返回纯文本 ``Internal Server Error``：

- 前端拿到的是 ``text/plain`` 的一段英文，既没法归一化成「服务器开小差了」
  这样的本地文案，也读不到 ``request_id``；
- 运维手里只有一个 500 状态码，用户报障时无法把它对应到日志里的那一行。

这条路径不是假想的：``site_profile.email`` 一旦含 RFC 保留域（写入时不校验，
读取时才被 ``SiteProfileRead`` 的 EmailStr 拒绝），``GET /site/profile``
就会抛未捕获的 ``ValidationError``。

## 关于 ``raise_app_exceptions=False``

Starlette 用自定义 handler 生成 500 响应之后仍会把异常往外抛（让服务器记日志、
让测试客户端按需 raise）。这是它设计的部分，**不是**这次修复没兜住。
所以测试里必须显式关掉 httpx 的这个开关，否则拿到的不是响应而是一句异常。
生产链路（uvicorn）不受影响：响应已经发出去了。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.middleware import RequestContextMiddleware
from app.db.session import async_session_factory
from app.main import _register_exception_handlers, app
from app.utils.exceptions import NotFoundError
from app.utils.logging import REQUEST_ID_HEADER


def _probe_app() -> FastAPI:
    """最小可跑的 app：只装中间件与异常处理器，外加两个会抛异常的路由。"""
    probe = FastAPI()
    probe.add_middleware(RequestContextMiddleware)
    _register_exception_handlers(probe)

    @probe.get("/boom")
    async def boom() -> None:
        """必然抛异常：模拟服务里任何一条没考虑到的分支。"""
        raise RuntimeError("数据库里有一行谁也没想到的脏数据 kaboom")

    @probe.get("/domain")
    async def domain() -> None:
        raise NotFoundError("查无此人")

    return probe


@pytest.fixture
async def tolerant_client() -> AsyncGenerator[AsyncClient, None]:
    """能拿到 500 响应体的客户端（见模块 docstring 的说明）。"""
    transport = ASGITransport(app=_probe_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://probe") as http:
        yield http


class TestFallbackEnvelope:
    async def test_unhandled_exception_is_structured_500(
        self, tolerant_client: AsyncClient
    ) -> None:
        """核心契约：必须有 code + request_id，而不是纯文本 Internal Server Error。"""
        response = await tolerant_client.get("/boom")
        assert response.status_code == 500

        body = response.json()
        assert body["code"] == "internal_error"
        assert body["detail"] == "服务器内部错误"
        assert body["request_id"], "500 响应必须带 request_id，否则无法与日志对应"
        assert body["request_id"] != "-"

    async def test_client_trace_id_is_echoed_into_body(self, tolerant_client: AsyncClient) -> None:
        """客户端自带追溯 ID 时要带进信封——跨系统排障靠的就是它能对上。"""
        response = await tolerant_client.get("/boom", headers={REQUEST_ID_HEADER: "trace-abc-123"})
        assert response.json()["request_id"] == "trace-abc-123"

    async def test_internal_error_does_not_leak_details(self, tolerant_client: AsyncClient) -> None:
        """堆栈、表结构、路径都不能出响应体——那是一份给攻击者的地图。"""
        body = (await tolerant_client.get("/boom")).text
        for leaked in ("kaboom", "Traceback", "RuntimeError", "site_profile", ".py"):
            assert leaked not in body, f"响应体泄漏了内部细节：{leaked}"

    async def test_domain_error_handler_still_wins(self, tolerant_client: AsyncClient) -> None:
        """更具体的处理器必须优先：加兜底不能把 404 变成 500。"""
        response = await tolerant_client.get("/domain")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert body["detail"] == "查无此人"


class TestRealisticTrigger:
    async def test_dirty_profile_email_returns_structured_500(self, db_reset: None) -> None:
        """本次修复的真实触发场景：库里已有脏邮箱时，读取接口不能返回纯文本。

        ``ensure_seed`` 写库时不做邮箱校验（这正是 ``ADMIN_EMAIL`` 改成 EmailStr
        的原因之一），所以「手工改过库 / 旧版本写入的数据」随时可能出现。
        这里直接把 ``site_profile.email`` 改成保留域，复现那个现场。
        """
        async with async_session_factory() as session:
            await session.execute(text("UPDATE site_profile SET email = 'broken@e2e.test'"))
            await session.commit()

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            response = await http.get("/api/v1/site/profile")

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "internal_error"
        assert body["request_id"], "真实触发路径下 request_id 也不能丢"
        # 校验失败的原因属于内部细节，不能出现在响应体里
        assert "ValidationError" not in response.text
