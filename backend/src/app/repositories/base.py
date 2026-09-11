"""仓储基类。

约定：仓储只负责「怎么取数据」，不含业务规则；业务规则一律在 services 层。
所有写操作只 ``flush`` 不 ``commit``——事务边界由 ``get_session`` 依赖统一收口，
这样一个请求就是一个事务，服务层组合多个仓储也不会出现「改一半提交了」。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用 CRUD。"""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, obj_id: int) -> ModelT | None:
        """按主键取单个对象。"""
        return await self.session.get(self.model, obj_id)

    async def exists(self, obj_id: int) -> bool:
        return await self.session.get(self.model, obj_id) is not None

    async def create(self, **values: Any) -> ModelT:
        """插入并 flush，拿到自增主键。"""
        obj = self.model(**values)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, obj: ModelT, **values: Any) -> ModelT:
        """更新字段。

        **调用方负责过滤**：一律传 ``payload.model_dump(exclude_unset=True)``，
        只把「用户这次真的改了的字段」交给这里。不要在这里做 ``None`` 跳过——
        那样会导致「想把摘要清空」永远做不到，是个典型的隐式行为陷阱。
        """
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def count_all(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(self.model))
        return int(result.scalar_one())
