"""refresh token 改用 httpOnly Cookie 承载后的全套契约。

## 为什么要有这个文件

refresh token 以前放在 localStorage：任何一次 XSS（第三方依赖漏洞、``v-html``
误用、恶意浏览器扩展）都能把它读走，并顺着轮换链续期出新的 access token，
形成长期冒充。改到 httpOnly Cookie 之后 JS 读不到它，这个攻击面就消失了。

代价是它变成了**浏览器会自动带上**的凭证，于是多出三件 localStorage 时代
不存在、且每一件错了都表现为"登录 200、刷新必掉线"的事：

1. **Cookie 属性**（HttpOnly / Secure / SameSite / Path）要按环境算对；
2. **CSRF**：Cookie 会被自动附带，刷新接口必须自己校验来源；
3. **登出要真的删掉它**：服务端吊销只保证"旧凭证换不出新凭证"，
   Cookie 还在的话浏览器每次刷新仍会把一枚作废的令牌送上来。

这里守的就是这三条，外加"令牌确实从 Cookie 走通了整条刷新链"。
轮换与复用检测本身由 ``test_refresh_rotation.py`` 覆盖，不在这里重复。
"""

from __future__ import annotations

from http.cookies import Morsel, SimpleCookie
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.config import settings
from app.models import RefreshSession
from app.repositories.refresh_session_repository import hash_jti
from app.utils.security import decode_token
from tests.conftest import refresh_cookie_of
from tests.factories import ADMIN_PASSWORD

LOGIN_URL = "/api/v1/auth/login"
TOKEN_URL = "/api/v1/auth/token"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"


def _login_body() -> dict[str, str]:
    return {"username": "admin", "password": ADMIN_PASSWORD}


def _parsed_cookie(response: Response) -> Morsel[Any]:
    """把响应里那枚 refresh Cookie 解析成 ``Morsel``（能读到属性）。

    刻意解析原始 ``Set-Cookie`` 而不是用 ``response.cookies``：后者只留
    ``name=value``，把 HttpOnly / Secure / SameSite / Path **全丢掉了**——
    而属性正是这个文件要守的东西，用 jar 等于对这些属性一条都测不到。
    """
    raw = response.headers.get_list("set-cookie")
    name = settings.refresh_token_cookie_name
    matched = [item for item in raw if item.startswith(f"{name}=")]
    assert matched, f"响应没有下发 {name}：{raw}"
    jar: SimpleCookie = SimpleCookie()
    jar.load(matched[0])
    return jar[name]


async def _session_jti_hashes() -> list[str]:
    """库里所有 refresh 会话的 jti 哈希。"""
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(select(RefreshSession.jti_hash))
        return list(result.scalars().all())


def _jti_of(token: str) -> str:
    return decode_token(token, expected_type="refresh").jti


class TestBodyNoLongerCarriesRefreshToken:
    """响应体里不该再有 refresh token —— 页面 JS 读得到响应体，留一份等于白改。"""

    async def test_login_hides_it_and_sets_the_cookie(self, client: AsyncClient) -> None:
        response = await client.post(LOGIN_URL, json=_login_body())
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"] is None
        assert refresh_cookie_of(response)

    async def test_form_login_follows_the_same_rule(self, client: AsyncClient) -> None:
        """Swagger 的 Authorize 走表单入口：两个入口共用一套签发逻辑，不能各写一份。"""
        response = await client.post(TOKEN_URL, data=_login_body())
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"] is None
        assert refresh_cookie_of(response)

    async def test_refresh_response_hides_it_too(self, client: AsyncClient) -> None:
        """刷新是**频率最高**的入口（access token 只有 120 分钟），漏了它等于没改。"""
        await client.post(LOGIN_URL, json=_login_body())

        refreshed = await client.post(REFRESH_URL)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["access_token"]
        assert refreshed.json()["refresh_token"] is None
        assert refresh_cookie_of(refreshed)


class TestCookieCarriesTheLiveSession:
    """Cookie 里那枚必须就是落库那一枚——否则"会话吊销"根本管不到它。"""

    async def test_login_cookie_matches_the_row_in_the_database(self, client: AsyncClient) -> None:
        token = refresh_cookie_of(await client.post(LOGIN_URL, json=_login_body()))

        assert await _session_jti_hashes() == [hash_jti(_jti_of(token))]

    async def test_refresh_rotates_the_cookie_and_opens_a_new_row(
        self, client: AsyncClient
    ) -> None:
        first = refresh_cookie_of(await client.post(LOGIN_URL, json=_login_body()))
        second = refresh_cookie_of(await client.post(REFRESH_URL))

        assert second != first
        rows = await _session_jti_hashes()
        assert hash_jti(_jti_of(first)) in rows
        assert hash_jti(_jti_of(second)) in rows


class TestCookieAttributes:
    """属性算错的症状全是"登录 200、刷新必掉线"，是最难查的一类。"""

    async def test_http_only_same_site_and_path_are_set(self, client: AsyncClient) -> None:
        cookie = _parsed_cookie(await client.post(LOGIN_URL, json=_login_body()))

        # HttpOnly 是这次改造的全部意义：少了它，JS 又能读到令牌了
        assert cookie["httponly"], "少了 HttpOnly 就等于把 refresh token 放回 localStorage"
        assert cookie["samesite"] == "lax"
        # Path 收窄到认证前缀：/media 与业务接口没必要带上登录凭证
        assert cookie["path"] == f"{settings.api_v1_prefix}/auth"

    async def test_no_secure_flag_outside_production(self, client: AsyncClient) -> None:
        """开发环境是 http://localhost，加 Secure 会被 Chrome **直接拒绝写入**。"""
        assert not settings.is_production, "这条用例的判据是测试环境非生产"
        cookie = _parsed_cookie(await client.post(LOGIN_URL, json=_login_body()))
        assert not cookie["secure"]

    async def test_secure_flag_appears_in_production(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生产自动加 Secure：明文链路上不得传输 refresh token。

        只改 ``app_env`` 就够触发——``cookie_secure`` 未显式配置时跟着
        ``is_production`` 走（见 ``Settings.cookie_secure_flag``）。
        """
        monkeypatch.setattr(settings, "app_env", "production")
        assert settings.cookie_secure_flag

        cookie = _parsed_cookie(await client.post(LOGIN_URL, json=_login_body()))
        assert cookie["secure"]

    async def test_explicit_cookie_secure_wins_over_the_environment(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式配置优先：TLS 由反向代理终结时应用看到的是 http，
        靠 ``is_production`` 推出来的结论会漏掉 Secure。"""
        assert not settings.is_production
        monkeypatch.setattr(settings, "cookie_secure", True)

        cookie = _parsed_cookie(await client.post(LOGIN_URL, json=_login_body()))
        assert cookie["secure"]


class TestBrowserRefreshPath:
    """浏览器真实路径：前端**不给** body，令牌由浏览器自动附带。"""

    async def test_refresh_without_a_body_still_works(self, client: AsyncClient) -> None:
        """这是迁移之后前端唯一的冷启动方式，断了就是"刷新页面必掉线"。"""
        await client.post(LOGIN_URL, json=_login_body())

        refreshed = await client.post(REFRESH_URL)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["access_token"]

    async def test_neither_body_nor_cookie_is_unauthorized(self, client: AsyncClient) -> None:
        """没有凭证 = 401（不是 403）。

        前端拦截器靠 401 判断"该重新登录了"，用错状态码会让页面卡在
        「每个请求都失败但就是不跳登录页」的状态。
        """
        # 显式清一遍 Jar：这条用例的价值在于"确实没有 Cookie"，
        # 不能靠"这个 client 是新建的所以恰好没有"这种隐式前提。
        client.cookies.clear()

        response = await client.post(REFRESH_URL)
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    async def test_refresh_cookie_is_not_sent_to_unrelated_paths(self, client: AsyncClient) -> None:
        """``Path=/api/v1/auth`` 的意义：文章列表这类接口不该平白带上**登录凭证**。

        注意别把它写成"没有 Cookie 头"：会话提示 Cookie（``blog_session``）的 path
        是 ``/``，会跟着所有请求发出去——那是**刻意的**（页面 JS 必须读得到它，
        见 ``test_session_hint_cookie.py``），而且它值恒为 ``"1"``、不含任何秘密。
        所以这里断言的是"业务接口上没有任何 refresh token"，不是"没有 Cookie"。
        """
        logged_in = await client.post(LOGIN_URL, json=_login_body())
        refresh_token = refresh_cookie_of(logged_in)

        listed = await client.get("/api/v1/articles")
        assert listed.status_code == 200, listed.text
        sent = listed.request.headers.get("cookie", "")
        assert refresh_token not in sent, "refresh token 不该被发到业务接口上"
        # 反向记录这个刻意的例外：hint Cookie 确实会带上（path=/），但无信息价值
        assert settings.session_hint_cookie_name in sent


class TestSameOriginGuard:
    """CSRF 防线：Cookie 会被浏览器自动附带，所以刷新接口要自己校验来源。"""

    async def test_request_without_origin_is_allowed(self, client: AsyncClient) -> None:
        """curl / 服务端脚本**无法**被 CSRF 利用（没有"浏览器自动带凭证"这回事），
        拦住它们只会让回归脚本莫名其妙 403。"""
        await client.post(LOGIN_URL, json=_login_body())

        response = await client.post(REFRESH_URL)
        assert response.status_code == 200, response.text

    async def test_trusted_origin_is_allowed(self, client: AsyncClient) -> None:
        await client.post(LOGIN_URL, json=_login_body())

        response = await client.post(REFRESH_URL, headers={"Origin": settings.cors_origins[0]})
        assert response.status_code == 200, response.text

    async def test_site_base_url_is_trusted_even_when_not_in_cors(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """站点的对外地址未必列在 CORS 里（同站部署时压根没有跨源），
        但它显然是"自己人"。"""
        monkeypatch.setattr(settings, "site_base_url", "https://blog.example.com")
        await client.post(LOGIN_URL, json=_login_body())

        response = await client.post(REFRESH_URL, headers={"Origin": "https://blog.example.com"})
        assert response.status_code == 200, response.text

    async def test_trailing_slash_still_matches(self, client: AsyncClient) -> None:
        """Origin 不带路径，但配置里的地址常被写成带尾斜杠——两边都要能配上。"""
        await client.post(LOGIN_URL, json=_login_body())

        response = await client.post(
            REFRESH_URL, headers={"Origin": f"{settings.cors_origins[0]}/"}
        )
        assert response.status_code == 200, response.text

    async def test_stranger_origin_is_forbidden(self, client: AsyncClient) -> None:
        """没有这道防线，任意站点都能让访客的浏览器悄悄换一枚 access token。"""
        await client.post(LOGIN_URL, json=_login_body())

        response = await client.post(REFRESH_URL, headers={"Origin": "http://evil.example.com"})
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"


class TestLogoutClearsTheCookie:
    """服务端吊销 ≠ Cookie 消失：不删，浏览器每次刷新仍会送一枚作废的令牌。"""

    async def test_logout_sends_a_deletion_instruction(self, client: AsyncClient) -> None:
        logged_in = await client.post(LOGIN_URL, json=_login_body())
        headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}

        out = await client.post(LOGOUT_URL, headers=headers)
        assert out.status_code == 200, out.text

        cookie = _parsed_cookie(out)
        assert cookie.value == ""
        assert cookie["max-age"] == "0"

    async def test_deletion_uses_the_same_path_as_the_write(self, client: AsyncClient) -> None:
        """path / domain 与写入时不一致 = 浏览器把它当成**另一枚** Cookie，
        结果是"登出后 Cookie 还在"，用户以为下线了而凭证其实还能用。"""
        logged_in = await client.post(LOGIN_URL, json=_login_body())
        written = _parsed_cookie(logged_in)

        out = await client.post(
            LOGOUT_URL,
            headers={"Authorization": f"Bearer {logged_in.json()['access_token']}"},
        )
        assert out.status_code == 200, out.text
        deleted = _parsed_cookie(out)

        assert deleted["path"] == written["path"] == f"{settings.api_v1_prefix}/auth"
        assert deleted["domain"] == written["domain"]


class TestRefreshTokenInBodyOptIn:
    """``REFRESH_TOKEN_IN_BODY``：给 curl / CI 这类拿不到 Cookie Jar 的客户端留的口子。"""

    async def test_opt_in_returns_it_in_the_body(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "refresh_token_in_body", True)

        response = await client.post(LOGIN_URL, json=_login_body())
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["refresh_token"]
        # 响应体里那份与 Cookie 里那份必须是同一枚
        assert body["refresh_token"] == refresh_cookie_of(response)

    async def test_opt_in_does_not_disable_the_cookie(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """开了口子只是"多给一份"，浏览器路径不能因此退化。"""
        monkeypatch.setattr(settings, "refresh_token_in_body", True)
        await client.post(LOGIN_URL, json=_login_body())

        refreshed = await client.post(REFRESH_URL)
        assert refreshed.status_code == 200, refreshed.text
        assert refresh_cookie_of(refreshed)
