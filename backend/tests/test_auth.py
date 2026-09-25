"""认证与权限测试。"""

from __future__ import annotations

import jwt
import pytest
from httpx import AsyncClient

from app.config import settings
from tests.conftest import login, refresh_cookie_of
from tests.factories import ADMIN_PASSWORD, AUTHOR_PASSWORD, make_user_payload


async def _login(client: AsyncClient, username: str, password: str) -> tuple[dict[str, str], str]:
    """登录并返回 ``(响应体, refresh token)``。

    refresh token 默认只在 httpOnly Cookie 里（响应体里那一份被抹掉了，
    页面 JS 读得到它）。登出/改密这类用例要验证"旧 refresh token 是否真的失效",
    所以必须把令牌一起带出来——只返回响应体是拿不到的。
    """
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json(), refresh_cookie_of(response)


class TestLogin:
    async def test_login_with_username(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        # 新契约：refresh token 走 httpOnly Cookie，响应体里**不带**它——
        # 页面 JS 读得到响应体，留一份等于把 XSS 那个口子又开回来。
        assert body["refresh_token"] is None
        assert refresh_cookie_of(response)
        assert body["expires_in"] > 0

    async def test_login_with_email(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin@example.com", "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 200

    async def test_wrong_password_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"

    async def test_unknown_user_returns_same_message(self, client: AsyncClient) -> None:
        """账号不存在与密码错误必须返回同一条文案，否则可以被用来枚举用户名。"""
        missing = await client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": "whatever123"}
        )
        wrong = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "whatever123"}
        )
        assert missing.status_code == wrong.status_code == 401
        assert missing.json()["detail"] == wrong.json()["detail"]

    async def test_oauth2_form_login_for_swagger(self, client: AsyncClient) -> None:
        """Swagger 的 Authorize 按钮走的是表单，两条入口必须都通。"""
        response = await client.post(
            "/api/v1/auth/token",
            data={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["access_token"]
        # 表单入口与 JSON 入口共用同一套签发逻辑：Cookie 同样要下发、响应体同样不带
        assert response.json()["refresh_token"] is None
        assert refresh_cookie_of(response)


class TestMe:
    async def test_me_requires_token(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_me_rejects_garbage_token(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401

    async def test_me_rejects_refresh_token_as_access_token(self, client: AsyncClient) -> None:
        """refresh token 不能当 access token 用——否则短期凭证的隔离就白做了。"""
        logged_in = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        )
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refresh_cookie_of(logged_in)}"},
        )
        assert response.status_code == 401

    async def test_me_returns_profile(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/auth/me", headers=admin_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "admin"
        assert body["role"] == "admin"
        assert "hashed_password" not in body

    async def test_update_self_cannot_escalate_role(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """作者尝试把自己提成站长：role 不在 UserSelfUpdate 里，必须被忽略。"""
        response = await client.patch(
            "/api/v1/auth/me", json={"nickname": "改个名", "role": "admin"}, headers=author_headers
        )
        assert response.status_code == 200
        assert response.json()["nickname"] == "改个名"
        assert response.json()["role"] == "author"


class TestRefresh:
    async def test_refresh_returns_new_pair(self, client: AsyncClient) -> None:
        logged_in = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        )
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_cookie_of(logged_in)}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]
        # 刷新同样只下发 Cookie，不给响应体
        assert response.json()["refresh_token"] is None
        assert refresh_cookie_of(response)

    async def test_access_token_cannot_be_used_to_refresh(self, client: AsyncClient) -> None:
        """拿 access token 去刷新必须 401。

        这条正是「显式 body 优先于 Cookie」存在的理由：登录已经把 refresh Cookie
        写进了 Jar，若这里退化成"取不到 body 就回落 Cookie"，请求会被自己的
        Cookie 救回来变成 200 —— 用例假绿，而"短期凭证换不出长期凭证"这条约束
        从此没人守。
        """
        logged_in = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        )
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": logged_in.json()["access_token"]}
        )
        assert response.status_code == 401

    async def test_expired_access_token_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """把有效期改成负数，签出来的 token 必须立刻过期。"""
        import app.utils.security as security

        monkeypatch.setattr(security.settings, "access_token_expire_minutes", -1, raising=False)
        stale = security.create_access_token(1, "admin")
        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {stale}"})
        assert response.status_code == 401
        assert "过期" in response.json()["detail"]


class TestPassword:
    async def test_change_password_then_login_with_new_one(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/me/password",
            json={"old_password": AUTHOR_PASSWORD, "new_password": "brand-new-pass-1"},
            headers=author_headers,
        )
        assert response.status_code == 200

        # 旧密码必须失效
        old = await client.post(
            "/api/v1/auth/login", json={"username": "writer", "password": AUTHOR_PASSWORD}
        )
        assert old.status_code == 401

        new = await client.post(
            "/api/v1/auth/login", json={"username": "writer", "password": "brand-new-pass-1"}
        )
        assert new.status_code == 200

    async def test_wrong_old_password_rejected(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/me/password",
            json={"old_password": "not-my-password", "new_password": "brand-new-pass-1"},
            headers=author_headers,
        )
        assert response.status_code == 400

    async def test_password_over_bcrypt_limit_is_422(self, client: AsyncClient) -> None:
        """bcrypt 只吃 72 字节。超限必须在入口拦掉，而不是让 500 冒出来。"""
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "a" * 100},
        )
        # 登录不做长度校验（长度校验在建号/改密时），这里验证的是改密接口
        assert response.status_code == 401

        tokens = (
            await client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
            )
        ).json()
        response = await client.post(
            "/api/v1/auth/me/password",
            json={"old_password": ADMIN_PASSWORD, "new_password": "汉" * 30},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 422


class TestUserAdministration:
    async def test_author_cannot_manage_users(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        assert (await client.get("/api/v1/auth/users", headers=author_headers)).status_code == 403
        response = await client.post(
            "/api/v1/auth/users", json=make_user_payload(), headers=author_headers
        )
        assert response.status_code == 403

    async def test_admin_creates_user(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        payload = make_user_payload()
        response = await client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
        assert response.status_code == 201
        assert response.json()["username"] == payload["username"]

    async def test_duplicate_username_conflicts(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        payload = make_user_payload()
        await client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
        again = await client.post(
            "/api/v1/auth/users",
            json={**payload, "email": f"other-{payload['email']}"},
            headers=admin_headers,
        )
        assert again.status_code == 409

    async def test_duplicate_email_conflicts(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        payload = make_user_payload()
        await client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
        again = await client.post(
            "/api/v1/auth/users",
            json={**payload, "username": f"another{payload['username']}"},
            headers=admin_headers,
        )
        assert again.status_code == 409

    async def test_short_password_rejected_by_schema(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/users",
            json=make_user_payload(password="123"),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_cannot_deactivate_last_admin(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """站点的自锁保护：把唯一的管理员停用掉，就再也没人能管理它了。"""
        me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
        response = await client.patch(
            f"/api/v1/auth/users/{me['id']}", json={"is_active": False}, headers=admin_headers
        )
        assert response.status_code == 400

    async def test_cannot_delete_self(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
        response = await client.delete(f"/api/v1/auth/users/{me['id']}", headers=admin_headers)
        assert response.status_code == 400

    async def test_deactivated_user_cannot_login(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = (
            await client.post("/api/v1/auth/users", json=make_user_payload(), headers=admin_headers)
        ).json()
        await client.patch(
            f"/api/v1/auth/users/{created['id']}", json={"is_active": False}, headers=admin_headers
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": created["username"], "password": AUTHOR_PASSWORD},
        )
        assert response.status_code == 403

    async def test_logout_returns_ok(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post("/api/v1/auth/logout", headers=admin_headers)
        assert response.status_code == 200


class TestLogout:
    """登出必须真的吊销凭证。

    此前 ``POST /auth/logout`` 只返回一句「请在客户端清除本地凭证」，服务端
    什么都不做：用户以为已登出，而旧 access token（120 分钟）与 refresh token
    （7 天）仍能访问所有受保护资源。
    """

    async def test_logout_invalidates_access_token(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        tokens, _ = await _login(client, "writer", AUTHOR_PASSWORD)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

        out = await client.post("/api/v1/auth/logout", headers=headers)
        assert out.status_code == 200, out.text

        after = await client.get("/api/v1/auth/me", headers=headers)
        assert after.status_code == 401, "登出后旧 access token 仍然可用 = 登出没有止损能力"
        assert after.json()["code"] == "unauthorized"

    async def test_logout_invalidates_refresh_token(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        """只吊销 access token 不够：refresh token 能立刻换出一对新的。"""
        tokens, refresh_token = await _login(client, "writer", AUTHOR_PASSWORD)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        await client.post("/api/v1/auth/logout", headers=headers)

        refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert refreshed.status_code == 401, "登出后旧 refresh token 仍能换发新令牌"

    async def test_can_login_again_after_logout(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        """吊销不能把用户锁在门外：重新登录必须照常可用。"""
        tokens, _ = await _login(client, "writer", AUTHOR_PASSWORD)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        await client.post("/api/v1/auth/logout", headers=headers)

        fresh, _ = await _login(client, "writer", AUTHOR_PASSWORD)
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh['access_token']}"}
        )
        assert response.status_code == 200


class TestTokenInternals:
    async def test_token_contains_expected_claims(self, client: AsyncClient) -> None:
        """直接解 token 检查声明，确认没有把敏感信息塞进载荷。

        JWT 的载荷是 **明文可读** 的（只签名不加密），所以这里要确保密码哈希、
        邮箱这类字段绝不出现在 token 里。
        """
        response = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        )
        payload = jwt.decode(
            response.json()["access_token"],
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        assert payload["type"] == "access"
        assert payload["role"] == "admin"
        # ver = 令牌代次。加它之后「改密码即全端下线」才有依据：
        # 校验时拿它与 users.token_version 比对，不一致就吊销。
        assert set(payload) == {"sub", "role", "type", "iat", "exp", "jti", "ver"}

    async def test_login_flow_helper(self, client: AsyncClient) -> None:
        token = await login(client, "admin", ADMIN_PASSWORD)
        assert token.count(".") == 2
