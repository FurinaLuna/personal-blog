"""令牌代次（token versioning）。

## 防的是什么

refresh token 原本是**不可吊销**的：签名有效 + 用户存在就认，默认 7 天
（``REFRESH_TOKEN_EXPIRE_DAYS``）内一直可用。而「改密码」这个应急动作
原本只改了哈希 —— 攻击者手上那个还没过期的 refresh token 照样能换出新令牌，
等于**没有应急手段**。

现在签发时把用户的 ``token_version`` 写进载荷，校验时比对；改密码时 +1，
所有已签发的 access / refresh token 立刻失效。
"""

from __future__ import annotations

from httpx import AsyncClient

ME_URL = "/api/v1/auth/me"
REFRESH_URL = "/api/v1/auth/refresh"
PASSWORD_URL = "/api/v1/auth/me/password"


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestTokenVersionRevocation:
    async def test_changing_password_invalidates_access_token(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        """改密码后，旧的 access token 立刻失效。"""
        tokens = await _login(client, "writer", "author123456")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert (await client.get(ME_URL, headers=headers)).status_code == 200

        changed = await client.post(
            PASSWORD_URL,
            json={"old_password": "author123456", "new_password": "BrandNew123456"},
            headers=headers,
        )
        assert changed.status_code == 200, changed.text

        # 同一个 token 现在应当被拒
        assert (await client.get(ME_URL, headers=headers)).status_code == 401

    async def test_changing_password_invalidates_refresh_token(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        """**这是本次修复的核心**：refresh token 也必须被吊销。

        只吊销 access token 是不够的 —— 攻击者拿 refresh token 就能立刻换到
        一对新的，改密码形同虚设。
        """
        tokens = await _login(client, "writer", "author123456")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        await client.post(
            PASSWORD_URL,
            json={"old_password": "author123456", "new_password": "BrandNew123456"},
            headers=headers,
        )

        refreshed = await client.post(REFRESH_URL, json={"refresh_token": tokens["refresh_token"]})
        assert refreshed.status_code == 401, "改密码后 refresh token 仍然可用 = 没有止损能力"

    async def test_new_token_after_password_change_works(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        """吊销不能把用户自己锁在门外：用新密码登录要正常。"""
        tokens = await _login(client, "writer", "author123456")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        await client.post(
            PASSWORD_URL,
            json={"old_password": "author123456", "new_password": "BrandNew123456"},
            headers=headers,
        )

        fresh = await _login(client, "writer", "BrandNew123456")
        assert (
            await client.get(ME_URL, headers={"Authorization": f"Bearer {fresh['access_token']}"})
        ).status_code == 200

    async def test_old_token_still_rejected_after_relogin(
        self, client: AsyncClient, author: dict[str, object]
    ) -> None:
        """重新登录不会让旧代次的令牌「复活」。"""
        tokens = await _login(client, "writer", "author123456")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        await client.post(
            PASSWORD_URL,
            json={"old_password": "author123456", "new_password": "BrandNew123456"},
            headers=headers,
        )
        await _login(client, "writer", "BrandNew123456")

        assert (await client.get(ME_URL, headers=headers)).status_code == 401


class TestTokenVersionCompatibility:
    async def test_tokens_without_ver_claim_still_work(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """**升级兼容**：本次改动之前签发的 token 里没有 ``ver`` 字段。

        存量用户的 ``token_version`` 默认是 0，所以老令牌必须继续有效 ——
        否则一次发版会把所有人踢下线，而他们只会看到「登录已失效」。
        """
        from app.utils.security import create_access_token

        # 模拟一个老 token：不带 ver（token_version 默认 0）
        legacy = create_access_token(1, "admin")
        response = await client.get(ME_URL, headers={"Authorization": f"Bearer {legacy}"})
        assert response.status_code == 200, "升级把存量登录态踢掉了"

    async def test_token_version_defaults_to_zero(self, db_reset: None) -> None:
        """新用户的代次必须是 0，而不是 NULL。

        NULL 会让「代次比对」变成 ``0 != None`` 而误判为已吊销 ——
        所有新用户的令牌一签发就失效。
        """
        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models import User

        async with async_session_factory() as session:
            result = await session.execute(select(User))
            for user in result.scalars().all():
                assert user.token_version == 0


class TestTokenVersionPayload:
    def test_tokens_carry_current_version(self) -> None:
        from app.utils.security import create_access_token, decode_token

        token = create_access_token(7, "author", token_version=3)
        payload = decode_token(token, expected_type="access")
        assert payload.token_version == 3

    def test_version_survives_roundtrip_for_both_types(self) -> None:
        from app.utils.security import create_refresh_token, decode_token

        token = create_refresh_token(7, "author", token_version=5)
        assert decode_token(token, expected_type="refresh").token_version == 5
