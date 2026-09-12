"""通用 Schema：分页信封、分页参数值对象、统一消息体、错误体。

``PageParams``（请求侧）与 ``Page``（响应侧）成对放在这里：
服务层直接 import 本模块的值对象，不再各自手算 ``(page - 1) * page_size``。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class PageParams:
    """分页参数值对象（不可变）。

    上限硬约束在服务端（``MAX_PAGE_SIZE``），防止 ``?page_size=100000`` 打爆内存。
    """

    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class Page(BaseModel, Generic[T]):
    """分页响应信封。所有列表接口返回统一结构，前端只需要写一份分页组件。"""

    items: list[T]
    total: int = Field(description="符合筛选条件的总记录数")
    page: int = Field(description="当前页码，从 1 开始")
    page_size: int = Field(description="每页条数")
    pages: int = Field(description="总页数")

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        """由查询结果构造分页信封。

        Args:
            items: 当前页数据。
            total: 总记录数。
            page: 当前页码（1-based）。
            page_size: 每页条数。

        Returns:
            填充好 ``pages`` 的分页对象。
        """
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=ceil(total / page_size) if page_size > 0 else 0,
        )


class Message(BaseModel):
    """简单操作结果。"""

    detail: str


class ErrorBody(BaseModel):
    """统一错误体，与 FastAPI 默认 ``HTTPException`` 结构保持一致。"""

    detail: str | int | list[dict] | dict
