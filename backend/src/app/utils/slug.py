"""slug 唯一化。

**为什么单独一个模块**：文章、分类、标签三处都需要「生成 slug，重名就加后缀」，
早先各自实现了一份，结果其中一份在泛化后失修（丢了 ``exclude_id`` 与后缀上限保护）。
现在只有这一份实现，行为由单元测试锁定。

放在 ``utils`` 而不是某个 service 里，是因为它服务三个领域对象——
任一 service 持有都会让另外两个产生「跨域依赖」。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.utils.exceptions import ConflictError
from app.utils.text import slugify

#: 判定候选 slug 是否已被占用。调用方用 ``functools.partial`` 绑定 ``exclude_id``
#: （更新自己的记录时，自己的 slug 不算冲突）。
ExistsFn = Callable[[str], Awaitable[bool]]

#: 后缀尝试上限。不是业务规则，而是防御性的——避免 exists 实现有 bug 时无限循环。
#: 触发它会抛冲突异常（提示用户手动指定），而不是静默接受重复。
MAX_SLUG_SUFFIX = 200


async def unique_slug(
    raw: str,
    *,
    exists: ExistsFn,
    prefix: str,
    max_suffix: int = MAX_SLUG_SUFFIX,
) -> str:
    """把任意标题转成未被占用的 slug。

    重名时依次尝试 ``-2`` / ``-3`` …… 最多 ``max_suffix`` 次。

    Args:
        raw: 原始标题或用户指定的 slug。
        exists: 异步判定函数，返回 True 表示候选已被占用。
        prefix: slug 化结果为空时的前缀（如 ``article`` / ``tag``）。
        max_suffix: 后缀尝试上限。

    Returns:
        未被占用的 slug。

    Raises:
        ConflictError: 尝试次数超过 ``max_suffix``（例如同名记录异常多，
            或 ``exists`` 实现有误）。宁可报错让用户手动指定，也不要静默生成
            一个可能重复的值。
    """
    base = slugify(raw, fallback_prefix=prefix)
    candidate = base
    suffix = 2
    while await exists(candidate):
        if suffix > max_suffix:
            raise ConflictError("无法生成唯一 slug，请手动指定")
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
