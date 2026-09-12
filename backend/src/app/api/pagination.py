"""分页参数依赖（HTTP 装配层）。

``PageParams`` 值对象定义在 ``schemas/common.py``（与 ``Page`` 响应信封成对），
服务层从那里 import；本文件只负责把 HTTP 查询串装配成 ``PageParams``，
并 re-export 常量，方便既有代码迁移引用。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query

from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PageParams

__all__ = ["DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE", "PageParams", "PageParamsDep"]


async def get_page_params(
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"每页条数，最大 {MAX_PAGE_SIZE}")
    ] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    """把查询串转成分页值对象。"""
    return PageParams(page=page, page_size=page_size)


PageParamsDep = Annotated[PageParams, Depends(get_page_params)]
