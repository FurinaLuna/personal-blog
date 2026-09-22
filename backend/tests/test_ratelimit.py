"""限流测试。

覆盖三类容易写错的地方：边界次数、维度隔离、以及「伪造 IP 能不能绕过」。
"""

from __future__ import annotations

import threading
import time

import pytest
from httpx import AsyncClient

from app.api.deps import client_ip
from app.config import settings
from app.utils import ratelimit
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
        """打开 TRUST_PROXY_HEADERS 后，取最右一跳作为客户端 IP。

        最右一跳才是最近的可信代理（nginx 用 $proxy_add_x_forwarded_for **追加**）
        写进去的真实来源；左侧全部由客户端提供，可任意伪造。
        早期实现取的是最左，导致「改个请求头就换一个限流桶」。
        """

        class FakeRequest:
            def __init__(self, headers: dict[str, str]) -> None:
                self.headers = headers
                self.client = type("Client", (), {"host": "10.0.0.1"})()

        # 客户端伪造了最左一跳；可信的是 nginx 追加在最右的真实来源
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        assert client_ip(FakeRequest({"X-Forwarded-For": "9.9.9.9, 203.0.113.7"})) == "203.0.113.7"

        # X-Real-IP 优先于 XFF：nginx 里它恒等于 $remote_addr，客户端改不动
        assert (
            client_ip(FakeRequest({"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "9.9.9.9"}))
            == "203.0.113.9"
        )

        # 仅单跳时最右即最左，行为不变
        assert client_ip(FakeRequest({"X-Forwarded-For": "203.0.113.7"})) == "203.0.113.7"

        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        assert client_ip(FakeRequest({"X-Forwarded-For": "9.9.9.9, 203.0.113.7"})) == "10.0.0.1"

    def test_spoofed_xff_cannot_create_unlimited_buckets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """伪造 XFF 不能绕过限流：不同伪造值必须映射到同一个桶。"""

        class FakeRequest:
            def __init__(self, spoofed: str) -> None:
                self.headers = {"X-Forwarded-For": f"{spoofed}, 203.0.113.7"}
                self.client = type("Client", (), {"host": "10.0.0.1"})()

        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        # 攻击者不断换最左值，但我们只认最右，所以永远得到同一个 IP
        assert client_ip(FakeRequest("1.1.1.1")) == "203.0.113.7"  # type: ignore[arg-type]
        assert client_ip(FakeRequest("2.2.2.2")) == "203.0.113.7"  # type: ignore[arg-type]
        assert client_ip(FakeRequest("3.3.3.3")) == "203.0.113.7"  # type: ignore[arg-type]

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


class TestSearchRateLimit:
    """搜索是读接口里**唯一**的重查询（三列 ILIKE + 同条件 COUNT）。

    限流的必要性来自成本不对称：真人一分钟搜不了 30 次，而脚本可以拿它
    当放大器——在没有 pg_trgm 索引的库上，每次请求都是一次全表扫描。
    """

    async def test_allows_up_to_limit_then_blocks(self, client: AsyncClient) -> None:
        """第 31 次开始被挡（配额 30/分钟）。"""
        for _ in range(30):
            response = await client.get("/api/v1/articles/search", params={"q": "示例"})
            assert response.status_code == 200, response.text

        blocked = await client.get("/api/v1/articles/search", params={"q": "示例"})
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "rate_limited"
        assert "Retry-After" in blocked.headers

    async def test_search_bucket_is_independent_from_login(self, client: AsyncClient) -> None:
        """规则名参与 key：搜索刷满不该影响登录，反之亦然。"""
        for _ in range(30):
            await client.get("/api/v1/articles/search", params={"q": "示例"})
        assert (
            await client.get("/api/v1/articles/search", params={"q": "示例"})
        ).status_code == 429

        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123456"}
        )
        assert login.status_code == 200

    async def test_list_endpoint_is_not_throttled(self, client: AsyncClient) -> None:
        """列表接口不该被顺手限流：它是正常浏览路径，真人翻页就会超过 30 次。"""
        for _ in range(35):
            response = await client.get("/api/v1/articles", params={"page": 1})
            assert response.status_code == 200, response.text


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


class _SlowCountBucket:
    """``count`` 做成「读一次就让出一次 GIL」的属性，用来放大竞态窗口。

    为什么需要它：CPython 的 GIL 让 ``bucket.count += 1`` 的读-改-写窗口只有
    几条字节码——实测连续 1.6 万次并发自增都不会丢一次计数，也就是说**直接
    并发打不加锁的实现，这个用例也会通过**，那就等于没写。把它换成带让出的
    属性后，单线程语义完全不变（读写仍是同一个值），但窗口被拉到真实线程
    切换的量级，去掉锁就会稳定失败。
    """

    def __init__(self, window_start: float, count: int) -> None:
        self.window_start = window_start
        self._count = count

    @property
    def count(self) -> int:  # type: ignore[override]
        value = self._count
        time.sleep(0)  # 主动放弃 GIL，让另一个线程有机会插进来
        return value

    @count.setter
    def count(self, value: int) -> None:  # type: ignore[override]
        self._count = value


class TestLimiterThreadSafety:
    """并发安全：``rate_limit`` 是**同步**依赖，FastAPI 会把它放进线程池执行。

    所以 ``hit()`` 一定是被多线程并发调用的。计数若还是无保护的
    ``bucket.count += 1``（读-改-写三步，GIL 只保证单条字节码原子），
    两个线程会读到同一个旧值再各自写回，计数被吞掉，配额就被放大。
    """

    def test_concurrent_hits_never_exceed_quota(self) -> None:
        """并发 50 次打 10 次配额：恰好只放行 10 次。"""
        limiter_under_test = SlidingWindowLimiter()
        total, quota = 50, 10
        # Barrier 让所有线程尽量同时起步，把竞争窗口压到最短、冲突概率拉到最高
        barrier = threading.Barrier(total)
        results: list[bool] = []
        results_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            allowed, _ = limiter_under_test.hit("concurrent", limit=quota, window_seconds=60)
            with results_lock:
                results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(total)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(results) == total
        assert sum(results) == quota, f"并发下放行了 {sum(results)} 次，配额是 {quota}"

    def test_concurrent_hits_are_counted_exactly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """放大竞态窗口后，并发 50 次打 10 次配额仍然只放行 10 次。

        这条才是真正能抓住「丢了锁」的用例（见 ``_SlowCountBucket`` 的说明）：
        把锁去掉它就会失败，而上面那条简单并发用例在同样情况下仍会通过。
        """
        monkeypatch.setattr(ratelimit, "_Bucket", _SlowCountBucket)
        limiter_under_test = SlidingWindowLimiter()
        total, quota = 50, 10
        barrier = threading.Barrier(total)
        results: list[bool] = []
        results_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            allowed, _ = limiter_under_test.hit("race", limit=quota, window_seconds=60)
            with results_lock:
                results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(total)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(results) == quota, f"并发下放行了 {sum(results)} 次，配额是 {quota}"

    def test_concurrent_hits_on_distinct_keys_stay_isolated(self) -> None:
        """并发不串桶：两个 key 各自按自己的配额放行，互不吃掉对方的计数。"""
        limiter_under_test = SlidingWindowLimiter()
        total, quota = 30, 5
        barrier = threading.Barrier(total)
        results: dict[str, int] = {"a": 0, "b": 0}
        results_lock = threading.Lock()

        def worker(key: str) -> None:
            barrier.wait()
            allowed, _ = limiter_under_test.hit(key, limit=quota, window_seconds=60)
            with results_lock:
                results[key] += int(allowed)

        threads = [
            threading.Thread(target=worker, args=("a" if index % 2 == 0 else "b",))
            for index in range(total)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results == {"a": quota, "b": quota}

    def test_reset_is_safe_while_hitting(self) -> None:
        """``reset`` 与 ``hit`` 并发时不炸，之后也还能拿到一个干净的空桶。

        锁必须同时覆盖这两条路径：``reset`` 若在 ``hit`` 的读-改-写中间把字典
        清掉，那次自增会写进一个刚被丢弃的桶里（计数凭空消失）。
        """
        limiter_under_test = SlidingWindowLimiter()
        stop = threading.Event()

        def hammer() -> None:
            while not stop.is_set():
                limiter_under_test.hit("churn", limit=1_000_000, window_seconds=60)

        thread = threading.Thread(target=hammer)
        thread.start()
        try:
            for _ in range(200):
                limiter_under_test.reset()
        finally:
            stop.set()
            thread.join()

        # 收尾后再 reset 一次：并发搅动停下来以后，桶必须是干净可复用的
        limiter_under_test.reset()
        assert limiter_under_test.hit("churn", limit=1, window_seconds=60)[0] is True


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
