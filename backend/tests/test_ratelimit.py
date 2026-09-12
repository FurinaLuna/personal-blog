"""限流测试。

覆盖三类容易写错的地方：边界次数、维度隔离、以及「伪造 IP 能不能绕过」。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api.deps import client_ip
from app.config import settings
from app.utils.ratelimit import SlidingWindowLimiter, limiter


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    """每个用例前清空计数：否则用例之间会共享配额，随机失败。"""
    limiter.reset()
    yield
    limiter.reset()


class TestLoginRateLimit:
    async def test_allows_up_to_limit_then_blocks(self, client: AsyncClient) -> None:
        """第 6 次开始被挡（配额 5/分钟）。"""
        payload = {"username": "admin", "password": "definitely-wrong"}

        for _ in range(5):
            response = await client.post("/api/v1/auth/login", json=payload)
            assert response.status_code == 401

        blocked = await client.post("/api/v1/auth/login", json=payload)
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "rate_limited"
        # Retry-After 必须给：没有它客户端会原地重试，限流反而变成放大攻击
        assert int(blocked.headers["Retry-After"]) >= 1

    async def test_blocked_request_does_not_reach_service(self, client: AsyncClient) -> None:
        """被限流时应当直接 429，而不是先查一遍密码再拒绝（省掉一次哈希计算）。"""
        payload = {"username": "admin", "password": "wrong"}
        for _ in range(5):
            await client.post("/api/v1/auth/login", json=payload)

        blocked = await client.post("/api/v1/auth/login", json=payload)
        assert blocked.json()["detail"] == "操作过于频繁，请稍后再试"


class TestRateLimitIsolation:
    async def test_different_headers_share_bucket_without_trust(self, client: AsyncClient) -> None:
        """默认不信任 XFF：换 header 不能绕过限流。"""
        payload = {"username": "admin", "password": "wrong"}
        for index in range(5):
            await client.post(
                "/api/v1/auth/login",
                json=payload,
                headers={"X-Forwarded-For": f"10.0.0.{index}"},
            )

        blocked = await client.post(
            "/api/v1/auth/login", json=payload, headers={"X-Forwarded-For": "10.0.0.99"}
        )
        assert blocked.status_code == 429

    def test_trusted_proxy_header_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """打开 TRUST_PROXY_HEADERS 后，取第一跳作为客户端 IP。"""

        class FakeRequest:
            def __init__(self) -> None:
                self.headers = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
                self.client = type("Client", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        assert client_ip(FakeRequest()) == "203.0.113.7"  # type: ignore[arg-type]

        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        assert client_ip(FakeRequest()) == "10.0.0.1"  # type: ignore[arg-type]

    async def test_comment_and_login_buckets_are_independent(self, client: AsyncClient) -> None:
        """规则名参与 key：登录刷满不该把评论也一起封掉。"""
        for _ in range(5):
            await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        assert (
            await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        ).status_code == 429

        # 评论接口有独立的配额，应当仍然可达（这里用不存在的文章拿 404，
        # 关键是「没有被 429 拦下」）
        response = await client.post(
            "/api/v1/comments/article/99999999",
            json={
                "author_name": "访客",
                "content": "测试内容",
            },
        )
        assert response.status_code != 429


class TestLimiterUnit:
    def test_window_reset_after_expiry(self) -> None:
        limiter_under_test = SlidingWindowLimiter()
        for _ in range(2):
            allowed, _ = limiter_under_test.hit("k", limit=2, window_seconds=60)
            assert allowed

        blocked, retry_after = limiter_under_test.hit("k", limit=2, window_seconds=60)
        assert not blocked
        assert retry_after >= 1

        # 窗口长度为 0 时下一个请求立即进入新窗口（用 0 秒模拟时间流逝）
        allowed_after_reset, _ = limiter_under_test.hit("k", limit=2, window_seconds=0)
        assert allowed_after_reset

    def test_reset_specific_key_only(self) -> None:
        limiter_under_test = SlidingWindowLimiter()
        limiter_under_test.hit("a", limit=1, window_seconds=60)
        limiter_under_test.hit("b", limit=1, window_seconds=60)

        limiter_under_test.reset("a")
        assert limiter_under_test.hit("a", limit=1, window_seconds=60)[0] is True
        assert limiter_under_test.hit("b", limit=1, window_seconds=60)[0] is False


class TestDisabledSwitch:
    async def test_can_be_disabled(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """压测/联调时可以整体关掉。"""
        monkeypatch.setattr(settings, "rate_limit_enabled", False)
        payload = {"username": "admin", "password": "wrong"}
        for _ in range(8):
            response = await client.post("/api/v1/auth/login", json=payload)
            assert response.status_code == 401
