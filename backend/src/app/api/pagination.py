"""分页参数依赖。

单独成文件是因为它既被路由用，又被服务层当值对象接收；
这里做成一个有 ``offset``/``limit`` 的不可变对象，避免各路由各算一遍 offset 算错。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class PageParams:
    """分页参数值对象。"""

    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


async def get_page_params(
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"每页条数，最大 {MAX_PAGE_SIZE}")
    ] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    """把查询串转成分页值对象。上限硬约束在服务端，防止 ?page_size=100000 打爆内存。"""
    return PageParams(page=page, page_size=page_size)


PageParamsDep = Annotated[PageParams, Depends(get_page_params)]
