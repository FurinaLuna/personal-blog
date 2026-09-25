"""「会话提示」Cookie（``blog_session``）的契约。

## 为什么要有这个文件

access token 改成只存前端内存后，前端在页面加载时**无法从 JS 侧判断这台浏览器
有没有会话**。于是连公开页面也会先打一次 ``POST /api/v1/auth/refresh``，
匿名访客每次进站白吃一个 401。这是本次安全改造的已知代价。

解法是登录 / 刷新时**额外**下发一个**非 httpOnly** 的提示 Cookie：值恒为 ``"1"``、
不含任何秘密，前端读它决定"值不值得去续期"。这个文件守的就是它——因为它身上
有一条**与直觉相反、且错了会静默失效**的约束：

    **它的 path 必须是 ``/``，不能跟着 refresh Cookie 收窄到 ``/api/v1/auth``。**

``document.cookie`` 只暴露「路径是当前页面路径前缀」的 cookie，而 SPA 页面在
``/``。一旦把 path 收成 ``/api/v1/auth``，页面 JS 就永远读不到它：匿名访客那次
多余的 401 没省掉，反而是**所有已登录用户都丢了公开页的登录态恢复**——一个比原
问题更糟的功能回归，而且因为它"看起来更像安全的做法"，很容易被人"顺手对齐"回去。
所以这里既正向钉住 ``path == "/"``，也钉住前端读的那一侧（见前端用例）。
"""

from __future__ import annotations

from http.cookies import Morsel, SimpleCookie
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response

from app.config import settings
from tests.factories import ADMIN_PASSWORD

LOGIN_URL = "/api/v1/auth/login"
TOKEN_URL = "/api/v1/auth/token"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"

# 前端硬编码这个名字的源文件（后端配置项与它必须一致）。
FRONTEND_HTTP_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "http.ts"


def _login_body() -> dict[str, str]:
    return {"username": "admin", "password": ADMIN_PASSWORD}


def _cookie(response: Response, name: str) -> Morsel[Any]:
    """按名字解析原始 ``Set-Cookie``（能读到属性）。

    刻意不用 ``response.cookies``：那个只留 ``name=value``，会把 HttpOnly /
    Secure / SameSite / Path **全丢掉**——而属性正是这个文件要守的东西。
    """
    raw = response.headers.get_list("set-cookie")
    matched = [item for item in raw if item.startswith(f"{name}=")]
    assert matched, f"响应没有下发 {name}：{raw}"
    jar: SimpleCookie = SimpleCookie()
    jar.load(matched[0])
    return jar[name]


def _hint(response: Response) -> Morsel[Any]:
    return _cookie(response, settings.session_hint_cookie_name)


def _refresh(response: Response) -> Morsel[Any]:
    return _cookie(response, settings.refresh_token_cookie_name)


class TestHintCookieIsIssued:
    """签发与刷新两条路径都要下发它——漏掉刷新那条等于"用一会儿就失效"。"""

    async def test_login_issues_it(self, client: AsyncClient) -> None:
        response = await client.post(LOGIN_URL, json=_login_body())
        assert response.status_code == 200, response.text
        assert _hint(response).value == "1"

    async def test_form_login_issues_it_too(self, client: AsyncClient) -> None:
        """Swagger 的表单入口共用 ``set_refresh_cookie``，不能有分叉。"""
        response = await client.post(TOKEN_URL, data=_login_body())
        assert response.status_code == 200, response.text
        assert _hint(response).value == "1"

    async def test_refresh_reissues_it(self, client: AsyncClient) -> None:
        """刷新是频率最高的入口（access token 只有 120 分钟）。"""
        await client.post(LOGIN_URL, json=_login_body())

        refreshed = await client.post(REFRESH_URL)
        assert refreshed.status_code == 200, refreshed.text
        assert _hint(refreshed).value == "1"

    async def test_opt_in_body_mode_still_issues_it(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``REFRESH_TOKEN_IN_BODY=true`` 只是"多给一份 refresh token"，
        与提示 Cookie 无关，浏览器路径不能因此退化。"""
        monkeypatch.setattr(settings, "refresh_token_in_body", True)

        response = await client.post(LOGIN_URL, json=_login_body())
        assert response.status_code == 200, response.text
        assert _hint(response).value == "1"


class TestHintCookieAttributes:
    """属性算错的表现是"优化静默失效"或"多了一个可读的凭证"，都很难被发现。"""

    async def test_it_is_not_http_only(self, client: AsyncClient) -> None:
        """**这是它存在的全部理由**：JS 要能读到它。

        少了这一条（即误加 HttpOnly），前端读不到 → ``hasSessionHint`` 恒为 false
        → 公开页面再也不恢复登录态。所以必须显式断言，不能只靠"属性看着对"。
        """
        hint = _hint(await client.post(LOGIN_URL, json=_login_body()))
        assert not hint["httponly"], (
            "提示 Cookie 被加上了 HttpOnly —— 前端将永远读不到它，"
            "公开页面的登录态恢复会整体失效（它唯一与 refresh Cookie 相反的一项就是这个）"
        )

    async def test_secure_samesite_maxage_domain_align_with_refresh(
        self, client: AsyncClient
    ) -> None:
        """除 path 外的属性与 refresh Cookie 对齐：两者会同时存在，口径不一致
        会带来"一个过期了另一个还在"之类的错位。"""
        response = await client.post(LOGIN_URL, json=_login_body())
        hint, refresh = _hint(response), _refresh(response)

        assert hint["secure"] == refresh["secure"]
        assert hint["samesite"] == refresh["samesite"]
        assert hint["max-age"] == refresh["max-age"]
        assert hint["domain"] == refresh["domain"]

    async def test_path_is_root_not_the_refresh_path(self, client: AsyncClient) -> None:
        """**本文件的核心护栏**：path 必须是 ``/``。

        收窄到 refresh Cookie 的 ``/api/v1/auth`` 会让 SPA 页面（在 ``/``）的
        ``document.cookie`` 读不到它——匿名访客那一次 401 没省掉，反而让所有
        已登录用户丢掉公开页的登录态恢复。谁"为了对齐"改回去，这条就红。
        """
        response = await client.post(LOGIN_URL, json=_login_body())
        hint, refresh = _hint(response), _refresh(response)

        assert hint["path"] == "/"
        assert refresh["path"] == f"{settings.api_v1_prefix}/auth"
        assert hint["path"] != refresh["path"], (
            "提示 Cookie 的 path 与 refresh Cookie 刻意不同：前者要能被页面 JS 读到，"
            "后者要收窄到认证接口"
        )

    async def test_value_carries_no_secret(self, client: AsyncClient) -> None:
        """值必须是常量 ``"1"``：一旦有人往里塞 token / 用户标识，它就变成
        一个 JS 可读的凭证，等于把 refresh token 搬回 localStorage 的老路。"""
        hint = _hint(await client.post(LOGIN_URL, json=_login_body()))
        assert hint.value == "1"


class TestLogoutClearsTheHint:
    """漏删它 = 登出后每次进公开页仍白跑一个注定 401 的续期请求。"""

    async def test_logout_deletes_it_with_the_same_path(self, client: AsyncClient) -> None:
        logged_in = await client.post(LOGIN_URL, json=_login_body())
        headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

        out = await client.post(LOGOUT_URL, headers=headers)
        assert out.status_code == 200, out.text

        written = _hint(logged_in)
        deleted = _hint(out)
        assert deleted.value == ""
        assert deleted["max-age"] == "0"
        # path 不一致时浏览器会把它当成另一枚 cookie，"登出后提示位还在"
        assert deleted["path"] == written["path"] == "/"


class TestFrontendAgreesOnTheName:
    """跨端契约：后端配置项与前端硬编码的名字必须一致。

    照抄 ``SITE_ARTICLE_PATH`` 与前端路由表的做法（``tests/test_feed.py``）：
    前端读不到后端配置，只能硬编码。没有这条比对，后端改个名字就会让提示 Cookie
    静默失效——前端永远读不到它，重新退回"匿名访客每次进站吃 401"。
    """

    def test_frontend_uses_the_backend_cookie_name(self) -> None:
        if not FRONTEND_HTTP_TS.exists():
            pytest.skip("前端不在当前检出中（例如只部署后端），跳过跨端契约检查")

        source = FRONTEND_HTTP_TS.read_text(encoding="utf-8")
        expected = f"'{settings.session_hint_cookie_name}'"
        assert expected in source, (
            f"后端 SESSION_HINT_COOKIE_NAME={settings.session_hint_cookie_name}，"
            f"但前端 frontend/src/api/http.ts 里找不到字面量 {expected}。"
            "改名字时请同步前端（它只能硬编码这个名字）。"
        )
