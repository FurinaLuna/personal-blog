"""限流。

实现取向：**进程内固定窗口**，不引入 Redis。

理由：个人博客是单实例部署，加 Redis 会让运维复杂度（和故障面）翻倍，
而它要防的是「脚本刷登录 / 刷评论」这类低强度滥用，不是分布式 DDoS——
后者应该交给 nginx 或云厂商的 WAF。

**已知边界（必须知道）**：计数在进程内存里，多 worker 时每个 worker 各算一份，
实际放行量会乘以 worker 数。真要精确限流再换 Redis 实现，接口保持不变即可。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

# 内存保护：字典按 key 累积，攻击者不断换 IP 就能把内存撑爆。
# 超过上限时清掉最久未更新的 key（不是精确 LRU，够用且实现极短）。
_MAX_KEYS = 10_000


@dataclass(slots=True)
class _Bucket:
    window_start: float
    count: int


class SlidingWindowLimiter:
    """固定窗口计数器。

    实现极简（一个 dict），但要清楚它的边界：窗口边界处可能出现
    「前一窗口末 + 后一窗口初」的瞬时双倍放行。对防刷场景完全够用。
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(0.0, 0))

    def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """记一次访问。

        Args:
            key: 限流维度，通常 ``"<规则名>:<客户端标识>"``。
            limit: 窗口内允许的次数。
            window_seconds: 窗口长度（秒）。

        Returns:
            ``(是否放行, 建议等待秒数)``；放行时等待秒数为 0。
        """
        now = time.monotonic()

        if len(self._buckets) > _MAX_KEYS:
            self._evict_oldest()

        bucket = self._buckets[key]
        if bucket.window_start == 0.0 or now - bucket.window_start >= window_seconds:
            bucket.window_start = now
            bucket.count = 0

        bucket.count += 1
        if bucket.count > limit:
            remaining = window_seconds - (now - bucket.window_start)
            return False, max(1, int(remaining + 0.999))
        return True, 0

    def reset(self, key: str | None = None) -> None:
        """清空计数。测试与运维手工解封用。"""
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)

    def _evict_oldest(self) -> None:
        """丢掉窗口最旧的一批 key，把内存拉回上限以内。"""
        ordered = sorted(self._buckets.items(), key=lambda item: item[1].window_start)
        for key, _ in ordered[: _MAX_KEYS // 4]:
            self._buckets.pop(key, None)


limiter = SlidingWindowLimiter()
