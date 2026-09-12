"""分类与标签路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AdminUser, AuthorUser, SessionDep
from app.schemas.common import Message
from app.schemas.taxonomy import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    CategoryWithCount,
    TagCreate,
    TagRead,
    TagUpdate,
    TagWithCount,
)
from app.services import TaxonomyService

category_router = APIRouter(prefix="/categories", tags=["分类"])
tag_router = APIRouter(prefix="/tags", tags=["标签"])


# ====================================================================== 分类


@category_router.get("", response_model=list[CategoryWithCount], summary="分类列表")
async def list_categories(
    session: SessionDep,
    with_counts: Annotated[bool, Query(description="是否统计各分类已发布文章数")] = True,
) -> list[CategoryWithCount]:
    return await TaxonomyService(session).list_categories(with_counts=with_counts)


@category_router.get("/{slug_or_id}", response_model=CategoryRead, summary="分类详情")
async def get_category(slug_or_id: str, session: SessionDep) -> CategoryRead:
    category = await TaxonomyService(session).get_category(slug_or_id)
    return CategoryRead.model_validate(category)


@category_router.post(
    "",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="新建分类（作者）",
)
async def create_category(
    payload: CategoryCreate, _: AuthorUser, session: SessionDep
) -> CategoryRead:
    category = await TaxonomyService(session).create_category(payload)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    return CategoryRead.model_validate(category)


@category_router.patch("/{category_id}", response_model=CategoryRead, summary="修改分类（作者）")
async def update_category(
    category_id: int, payload: CategoryUpdate, _: AuthorUser, session: SessionDep
) -> CategoryRead:
    category = await TaxonomyService(session).update_category(category_id, payload)
    await session.commit()
    return CategoryRead.model_validate(category)


@category_router.delete("/{category_id}", response_model=Message, summary="删除分类（站长）")
async def delete_category(category_id: int, _: AdminUser, session: SessionDep) -> Message:
    """删除分类不会删除文章，只会让它们变成「未分类」。"""
    await TaxonomyService(session).delete_category(category_id)
    await session.commit()
    return Message(detail="分类已删除，其下文章已变为「未分类」")


# ====================================================================== 标签


@tag_router.get("", response_model=list[TagWithCount], summary="标签列表 / 标签云")
async def list_tags(
    session: SessionDep,
    limit: Annotated[int | None, Query(ge=1, le=200, description="只取前 N 个（按热度）")] = None,
    min_count: Annotated[int, Query(ge=0, description="过滤掉文章数少于此值的标签")] = 0,
    with_counts: Annotated[bool, Query()] = True,
) -> list[TagWithCount]:
    return await TaxonomyService(session).list_tags(
        limit=limit, min_count=min_count, with_counts=with_counts
    )


@tag_router.post(
    "", response_model=TagRead, status_code=status.HTTP_201_CREATED, summary="新建标签（作者）"
)
async def create_tag(payload: TagCreate, _: AuthorUser, session: SessionDep) -> TagRead:
    tag = await TaxonomyService(session).create_tag(payload)
    await session.commit()
    return TagRead.model_validate(tag)


@tag_router.patch("/{tag_id}", response_model=TagRead, summary="修改标签（作者）")
async def update_tag(
    tag_id: int, payload: TagUpdate, _: AuthorUser, session: SessionDep
) -> TagRead:
    tag = await TaxonomyService(session).update_tag(tag_id, payload)
    await session.commit()
    return TagRead.model_validate(tag)


@tag_router.delete("/{tag_id}", response_model=Message, summary="删除标签（作者）")
async def delete_tag(tag_id: int, _: AuthorUser, session: SessionDep) -> Message:
    await TaxonomyService(session).delete_tag(tag_id)
    await session.commit()
    return Message(detail="标签已删除")


@tag_router.post("/cleanup", response_model=Message, summary="清理空标签（站长）")
async def cleanup_tags(_: AdminUser, session: SessionDep) -> Message:
    """把没有任何文章引用的标签清掉，让标签云保持干净。"""
    removed = await TaxonomyService(session).cleanup_orphan_tags()
    await session.commit()
    return Message(detail=f"已清理 {removed} 个空标签")
