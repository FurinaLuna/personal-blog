"""密码哈希的线程池包装：bcrypt 不许按住事件循环。

存在意义：bcrypt 是**同步 CPU 密集**调用（cost=12，实测约 180ms/次），而部署是
单 worker（`deploy/Dockerfile.backend` 的 `--workers 1`）。在 async 路径上直接调用
它，一次登录就能让整个事件循环停摆 180ms —— 所有并发请求（包括静态资源和
健康检查）一起排队。

这个文件的用例是"行为断言"而不是"实现断言"：不检查是否 import 了 asyncio，
而是**真的去量事件循环有没有被卡住**。
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.security import (
    BCRYPT_MAX_BYTES,
    PasswordTooLongError,
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_async,
)


async def test_hash_password_async_matches_sync_result() -> None:
    """async 包装与同步版本的语义必须完全一致（含 72 字节校验）。"""
    hashed = await hash_password_async("correct horse battery staple")
    assert hashed.startswith("$2b$")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


async def test_hash_password_async_rejects_over_long_password() -> None:
    """超长密码在 async 路径上同样抛 PasswordTooLongError，不能被吞成 False。"""
    with pytest.raises(PasswordTooLongError):
        await hash_password_async("a" * (BCRYPT_MAX_BYTES + 1))


async def test_verify_password_async_handles_broken_hash() -> None:
    """损坏的哈希返回 False 而不是抛异常（与同步版一致，避免 500）。"""
    assert await verify_password_async("whatever", "not-a-bcrypt-hash") is False
    assert await verify_password_async("", "") is False


async def test_hashing_does_not_block_the_event_loop() -> None:
    """核心用例：哈希期间事件循环必须还能跑别的任务。

    实现方式是一个 5ms 一跳的计数器：bcrypt cost=12 约 100–200ms，
    如果它跑在事件循环里，计数器最多跳 0–1 次；丢到线程池后应该跳几十次。

    阈值取 5 而不是"约 30"：CI 上 CPU 慢、bcrypt 更快跑完都可能发生，
    这里只要求"明显不是被卡住的形状"。反过来说，**同步实现必然失败**
    （实测 ticks=0），这正是这条用例的价值。
    """
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    try:
        await hash_password_async("blocking-check-password")
    finally:
        stop.set()
        await task

    assert ticks > 5, f"哈希期间事件循环只跳了 {ticks} 次，说明 bcrypt 仍跑在循环里"


async def test_verify_does_not_block_the_event_loop() -> None:
    """校验路径同理：它在线上的调用频率比哈希更高（每次登录都要走一次）。"""
    hashed = hash_password("verify-blocking-check")
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    try:
        assert await verify_password_async("verify-blocking-check", hashed)
    finally:
        stop.set()
        await task

    assert ticks > 5, f"校验期间事件循环只跳了 {ticks} 次，说明 bcrypt 仍跑在循环里"


async def test_login_endpoint_yields_to_other_requests(client) -> None:
    """端到端形态：登录接口必须并发可用，而不是串行排队。

    两个并发登录请求的总耗时应当明显小于「2 × 单次耗时」。这里用宽松阈值
    （< 1.6 倍单次）避免 CI 抖动误报，但足以区分"并发"与"串行"：

    - 线程池实现：两次 bcrypt 在线程池里并行（默认线程池 ≥ 4），总耗时 ≈ 1 倍；
    - 事件循环里跑同步 bcrypt：总耗时 ≈ 2 倍。

    注意限流是 5 次/分，本用例只发 3 次登录，且 `_reset_rate_limiter` 每个用例
    会清空计数，不会触发 429。
    """
    import time

    from tests.factories import ADMIN_PASSWORD

    payload = {"username": "admin", "password": ADMIN_PASSWORD}

    start = time.perf_counter()
    response = await client.post("/api/v1/auth/login", json=payload)
    single = time.perf_counter() - start
    assert response.status_code == 200, response.text

    start = time.perf_counter()
    results = await asyncio.gather(
        client.post("/api/v1/auth/login", json=payload),
        client.post("/api/v1/auth/login", json=payload),
    )
    concurrent = time.perf_counter() - start

    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert concurrent < single * 1.6, (
        f"并发两次登录耗时 {concurrent:.3f}s，单次 {single:.3f}s —— 没有并发起来"
    )
