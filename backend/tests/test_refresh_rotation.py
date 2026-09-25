"""刷新令牌轮换、复用检测与会话吊销。

存在意义：在此之前 refresh token 是**完全无状态**的（`jti` 生成了却从不落库），
"吊销"只能靠 `token_version += 1`（全端下线）。两个后果：

1. **泄露后无法止损**：被偷走的 refresh token 在 7 天有效期内可以反复换出
   新的 access token，服务端既看不见也拦不住；
2. **泄露面单调增长**：每次刷新都签发新令牌而旧令牌继续有效 —— 每刷新一次，
   就多一枚能用的凭证。

这里守的就是"轮换 + 复用检测 + 按会话吊销"这三条契约。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.config import settings
from app.models import RefreshSession
from app.repositories.refresh_session_repository import hash_jti
from app.services.auth_service import REFRESH_REUSE_GRACE_SECONDS, AuthService
from tests.conftest import refresh_cookie_of
from tests.factories import ADMIN_PASSWORD


async def _sessions() -> list[RefreshSession]:
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(select(RefreshSession).order_by(RefreshSession.id))
        return list(result.scalars().all())


async def _set_rotated_at(jti: str, *, seconds_ago: float) -> None:
    """把某条会话的 rotated_at 改到 N 秒前（用来跨过复用宽容窗口）。"""
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        await session.execute(
            update(RefreshSession)
            .where(RefreshSession.jti_hash == hash_jti(jti))
            .values(rotated_at=datetime.now(UTC) - timedelta(seconds=seconds_ago))
        )
        await session.commit()


def _jti_of(token: str) -> str:
    from app.utils.security import decode_token

    return decode_token(token, expected_type="refresh").jti


async def _login(client: AsyncClient) -> tuple[dict[str, str], str]:
    """登录，返回 ``(响应体, refresh token)``。

    refresh token 不再出现在响应体里（见 ``app/api/cookies.py``），只能从
    ``Set-Cookie`` 取。这里保留二元组是为了让"登出/改密后旧令牌必须失效"
    这类用例仍能**显式**把旧令牌交回服务端。
    """
    response = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json(), refresh_cookie_of(response)


async def _refresh(client: AsyncClient, token: str | None = None):
    """刷新。

    ``token=None``（不传 body）= **浏览器的真实路径**：令牌由 Cookie Jar 自动回带，
    服务端自己从 Cookie 里取。传了值则是脚本/CI 的显式传参路径。
    """
    if token is None:
        # 浏览器真实路径：一个字节的请求体都不发（服务端从 Cookie 取）。
        # 这里刻意不发 ``{}`` —— 曾经 refresh 的 body 是必填的，前端只能靠发空
        # 对象绕过；那条约束已经移除，测试必须真的不发 body 才能守住它。
        return await client.post("/api/v1/auth/refresh")
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def _replay(client: AsyncClient, stale_token: str):
    """拿一枚**已经轮换过**的旧令牌重放——复用检测的核心场景。

    刻意先断言 Cookie Jar 里那枚**不是**被重放的这一枚：取凭证时 body 优先于
    Cookie，所以只有两者不同，服务端收到的才真是那枚旧令牌。否则请求会被
    自己的 Cookie 悄悄救回来（200），用例变成假绿，而"窗口外重放要整族吊销"
    这条约束实际上没人守。
    """
    current = client.cookies.get(settings.refresh_token_cookie_name)
    assert current != stale_token, "Cookie Jar 里就是被重放的这一枚：用例没有真正触发复用检测"
    return await _refresh(client, stale_token)


class TestSessionIsRecorded:
    async def test_login_opens_a_session_row(self, client: AsyncClient) -> None:
        _, refresh_token = await _login(client)

        rows = await _sessions()
        assert len(rows) == 1
        # 只存哈希：库里不该出现原始 jti
        jti = _jti_of(refresh_token)
        assert rows[0].jti_hash == hashlib.sha256(jti.encode()).hexdigest()
        assert jti not in rows[0].jti_hash
        assert rows[0].rotated_at is None and rows[0].revoked_at is None

    async def test_session_expiry_matches_token_ttl(self, client: AsyncClient) -> None:
        from app.config import settings

        await _login(client)
        rows = await _sessions()

        # 读出来必须带时区（SQLite 存的是 naive，靠 UTCDateTime 统一补 UTC）
        assert rows[0].expires_at.tzinfo is not None
        expected = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
        assert abs((rows[0].expires_at - expected).total_seconds()) < 60


class TestRotation:
    async def test_rotation_issues_a_new_token_and_records_both(self, client: AsyncClient) -> None:
        _, first = await _login(client)

        rotated = await _refresh(client, first)
        assert rotated.status_code == 200, rotated.text
        second = refresh_cookie_of(rotated)
        assert second != first

        # 两条会话都在：旧的标记为已轮换，新的是可用的
        rows = {row.jti_hash: row for row in await _sessions()}
        assert rows[hash_jti(_jti_of(first))].rotated_at is not None
        assert rows[hash_jti(_jti_of(second))].is_usable

    async def test_old_token_is_rejected_once_the_grace_window_passes(
        self, client: AsyncClient
    ) -> None:
        """轮换的意义在于"旧的不能长期可用"。

        **准确表述**：宽容窗口（默认 30 秒）内旧令牌仍可重放 —— 那是为了
        容忍两个标签页同时刷新（前端的单飞锁只在单页内生效）。
        超出窗口即拒绝，并判定为盗用。
        """
        _, first = await _login(client)
        await _refresh(client, first)

        await _set_rotated_at(_jti_of(first), seconds_ago=REFRESH_REUSE_GRACE_SECONDS + 5)

        assert (await _replay(client, first)).status_code == 401

    async def test_new_token_keeps_working_after_rotation(self, client: AsyncClient) -> None:
        _, first = await _login(client)
        second = refresh_cookie_of(await _refresh(client, first))

        third = await _refresh(client, second)
        assert third.status_code == 200

    async def test_rotation_chains_are_traceable(self, client: AsyncClient) -> None:
        """排障时要能顺着 replaced_by 看出"这枚是从哪来的"。"""
        _, first = await _login(client)
        first_jti = _jti_of(first)
        second = refresh_cookie_of(await _refresh(client, first))

        rows = {row.jti_hash: row for row in await _sessions()}
        old = rows[hash_jti(first_jti)]
        assert old.rotated_at is not None
        assert old.replaced_by == hash_jti(_jti_of(second))


class TestReuseDetection:
    async def test_reuse_outside_grace_revokes_the_whole_family(self, client: AsyncClient) -> None:
        """窗口外拿已轮换的令牌来换 = 盗用 ⇒ 整族吊销。"""
        _, first = await _login(client)
        second = refresh_cookie_of(await _refresh(client, first))

        # 把它推到宽容窗口之外
        await _set_rotated_at(_jti_of(first), seconds_ago=REFRESH_REUSE_GRACE_SECONDS + 10)

        replay = await _replay(client, first)
        assert replay.status_code == 401

        # 关键：**连最新那枚也不能再用** —— 攻击者可能已经拿走了它
        assert (await _refresh(client, second)).status_code == 401

        rows = await _sessions()
        assert all(row.revoked_at is not None for row in rows), "复用后应当整族被吊销"

    async def test_reuse_within_grace_is_tolerated(self, client: AsyncClient) -> None:
        """宽容窗口内 = 多标签页同时刷新，不该把用户踢下线。

        前端的单飞锁只在单个页面内生效；两个标签页的 access token 同时过期时，
        两边会几乎同时发起刷新，其中一边必然拿到"已经用过的"那一枚。
        """
        _, first = await _login(client)
        await _refresh(client, first)  # 另一个标签页先换了

        # 本标签页紧接着也来换（窗口内）
        second = await _replay(client, first)
        assert second.status_code == 200

        rows = await _sessions()
        assert all(row.revoked_at is None for row in rows), "窗口内不该触发整族吊销"

    async def test_unknown_token_is_rejected(self, client: AsyncClient) -> None:
        """库里没有对应行 ⇒ 拒绝。

        这正是**升级后的第一次访问**会遇到的情况：本次改动之前签发的 refresh
        token 都没有落库。宁可让用户重新登录一次，也不能给"不受会话管理约束的
        令牌"开后门。
        """
        from app.utils.security import create_refresh_token

        orphan = create_refresh_token(1, "admin", token_version=0)
        response = await _refresh(client, orphan)

        assert response.status_code == 401
        assert "失效" in response.json()["detail"]


class TestRevocation:
    async def test_logout_revokes_sessions(self, client: AsyncClient, admin_headers) -> None:
        _, refresh_token = await _login(client)

        response = await client.post("/api/v1/auth/logout", headers=admin_headers)
        assert response.status_code in (200, 204), response.text

        # 登出后手上的 refresh token 立刻换不出东西（它自己能续期，这是最危险的点）
        assert (await _refresh(client, refresh_token)).status_code == 401
        rows = await _sessions()
        assert all(row.revoked_at is not None for row in rows)

    async def test_change_password_revokes_sessions(
        self, client: AsyncClient, admin_headers
    ) -> None:
        _, refresh_token = await _login(client)

        response = await client.post(
            "/api/v1/auth/me/password",
            json={"old_password": ADMIN_PASSWORD, "new_password": "NewAdminPass@2026"},
            headers=admin_headers,
        )
        assert response.status_code in (200, 204), response.text

        assert (await _refresh(client, refresh_token)).status_code == 401

        # 改回去，避免影响其它用例（测试库里 admin 是共享的基础数据）
        back = await client.post(
            "/api/v1/auth/me/password",
            json={"old_password": "NewAdminPass@2026", "new_password": ADMIN_PASSWORD},
            headers=admin_headers,
        )
        assert back.status_code in (200, 204, 401), back.text


class TestPruning:
    async def test_prune_removes_only_expired_sessions(self, client: AsyncClient) -> None:
        """清理只删早就过期的行：还在有效期内的会话是关于"谁登过"的唯一记录。"""
        await _login(client)

        from app.db.session import async_session_factory

        async with async_session_factory() as session:
            expired = RefreshSession(
                user_id=1,
                jti_hash=hash_jti("expired-token-jti"),
                expires_at=datetime.now(UTC) - timedelta(days=30),
            )
            session.add(expired)
            await session.commit()

            removed = await AuthService(session).prune_expired_sessions()
            await session.commit()

        assert removed == 1
        remaining = await _sessions()
        assert len(remaining) == 1
        assert remaining[0].jti_hash != hash_jti("expired-token-jti")


@pytest.mark.parametrize("endpoint", ["/api/v1/auth/refresh"])
async def test_refresh_still_rejects_garbage(client: AsyncClient, endpoint: str) -> None:
    response = await client.post(endpoint, json={"refresh_token": "not-a-jwt"})
    assert response.status_code == 401
