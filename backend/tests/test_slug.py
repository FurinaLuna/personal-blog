"""slug 唯一化测试。

这里是「三处实现收敛成一处」的回归网：任何一处再想自己写一遍，
都会因为行为不一致而在这里暴露（尤其是后缀上限与 exclude_id 支持）。
"""

from __future__ import annotations

import pytest

from app.utils.exceptions import ConflictError
from app.utils.slug import unique_slug

pytestmark = pytest.mark.asyncio


def taken(*names: str) -> object:
    """造一个「这些 slug 已被占用」的 exists 函数。"""
    occupied = set(names)

    async def exists(candidate: str) -> bool:
        return candidate in occupied

    return exists


class TestBasic:
    async def test_returns_base_when_free(self) -> None:
        assert await unique_slug("Hello World", exists=taken(), prefix="article") == "hello-world"

    async def test_appends_suffix_on_conflict(self) -> None:
        exists = taken("hello-world", "hello-world-2")
        assert await unique_slug("Hello World", exists=exists, prefix="article") == "hello-world-3"

    async def test_chinese_title_kept_as_is(self) -> None:
        """中文标题不做拼音转换，URL 里百分号编码即可。"""
        assert await unique_slug("技术笔记", exists=taken(), prefix="article") == "技术笔记"

    async def test_blank_input_falls_back_to_prefix(self) -> None:
        result = await unique_slug("   ", exists=taken(), prefix="article")
        assert result.startswith("article-")
        assert len(result) > len("article-")


class TestBoundaries:
    async def test_empty_string_input(self) -> None:
        """极值：空字符串不能抛异常，也不能生成空 slug。"""
        result = await unique_slug("", exists=taken(), prefix="tag")
        assert result
        assert result.startswith("tag-")

    async def test_very_long_title_is_truncated(self) -> None:
        """极值：超长标题被截断，不会写出几百字符的 slug。"""
        result = await unique_slug("a" * 500, exists=taken(), prefix="article")
        assert len(result) <= 80

    async def test_suffix_limit_raises_instead_of_looping(self) -> None:
        """exists 永远为真时必须抛冲突，而不是无限循环。

        （改造前标签那处没有这个上限，是「拷贝后失修」的直接证据。）
        """
        always_taken = taken(*[f"x-{i}" for i in range(1, 300)], "x")
        with pytest.raises(ConflictError):
            await unique_slug("x", exists=always_taken, prefix="tag")

    async def test_custom_max_suffix_is_respected(self) -> None:
        """上限可通过参数收紧，便于测试与特殊场景。"""
        always_taken = taken("dup", "dup-2", "dup-3")
        with pytest.raises(ConflictError):
            await unique_slug("dup", exists=always_taken, prefix="tag", max_suffix=2)

    async def test_excludes_self_via_partial(self) -> None:
        """更新自己时，自己的 slug 不该算冲突（exclude_id 由 partial 绑定）。"""
        from functools import partial

        async def exists(candidate: str, *, exclude_id: int | None = None) -> bool:
            # 模拟「slug 被别人占用，但那正是自己」
            return candidate == "self-slug" and exclude_id is None

        assert await unique_slug(
            "self slug", exists=partial(exists, exclude_id=7), prefix="cat"
        ) == ("self-slug")
        # 不排除自己时则要退让
        assert await unique_slug(
            "self slug", exists=partial(exists, exclude_id=None), prefix="cat"
        ) == ("self-slug-2")

    async def test_concurrent_same_name_diverges(self) -> None:
        """并发创建同名实体：第二次调用会退让成 -2，不会拿到同一个 slug。

        用「先查后写」的两次独立调用模拟竞态下的必然结果——
        真正要保证的是「退让逻辑对同一个候选集是确定的」。
        """
        occupied: set[str] = set()

        async def exists(candidate: str) -> bool:
            return candidate in occupied

        first = await unique_slug("同名", exists=exists, prefix="tag")
        occupied.add(first)
        second = await unique_slug("同名", exists=exists, prefix="tag")

        assert first == "同名"
        assert second == "同名-2"
