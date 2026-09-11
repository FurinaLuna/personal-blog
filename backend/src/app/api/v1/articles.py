"""文章路由。

路由声明顺序很重要：``/archive``、``/manage`` 这类静态路径必须写在
``/{slug_or_id}`` 之前，否则 FastAPI 会把 "archive" 当成一个 slug 去查文章。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AuthorUser, OptionalUser, SessionDep
from app.api.pagination import PageParamsDep
from app.models import ArticleSort, ArticleStatus
from app.repositories import ArticleFilter, ArticleSorting
from app.schemas.article import ArticleCreate, ArticleDetail, ArticleSummary, ArticleUpdate
from app.schemas.common import Page
from app.schemas.site import ArchiveGroup
from app.services import ArticleService

router = APIRouter(prefix="/articles", tags=["文章"])


def _build_filter(
    *,
    keyword: str | None,
    category: str | None,
    tag: str | None,
    author_id: int | None,
    article_status: ArticleStatus | None = None,
) -> ArticleFilter:
    """把查询串拼成仓储的筛选对象。

    ``category`` 同时接受 slug 和数字 id，前端就不用关心自己手上拿到的是哪种，
    少一次「到底该传什么」的沟通成本。
    """
    category_id: int | None = None
    category_slug: str | None = None
    if category:
        category_slug = category if not category.isdigit() else None
        category_id = int(category) if category.isdigit() else None
    statuses: tuple[ArticleStatus, ...] = (article_status,) if article_status else ()
    return ArticleFilter(
        keyword=keyword,
        statuses=statuses,
        category_id=category_id,
        category_slug=category_slug,
        tag_slug=tag,
        author_id=author_id,
    )


@router.get("", response_model=Page[ArticleSummary], summary="文章列表（前台）")
async def list_articles(
    session: SessionDep,
    page_params: PageParamsDep,
    keyword: Annotated[
        str | None, Query(max_length=100, description="标题 / 摘要 / 正文关键词")
    ] = None,
    category: Annotated[str | None, Query(description="分类 slug 或 id")] = None,
    tag: Annotated[str | None, Query(description="标签 slug")] = None,
    author_id: Annotated[int | None, Query(description="按作者过滤")] = None,
    sort: Annotated[ArticleSort, Query(description="排序方式")] = ArticleSort.LATEST,
) -> Page[ArticleSummary]:
    """前台列表：只返回已发布文章，支持分页 + 关键词/分类/标签筛选 + 多种排序。"""
    service = ArticleService(session)
    flt = _build_filter(keyword=keyword, category=category, tag=tag, author_id=author_id)
    return await service.list_public(
        flt=flt,
        sorting=ArticleSorting(sort=sort),
        page=page_params.page,
        page_size=page_params.page_size,
    )


@router.get("/archive", response_model=list[ArchiveGroup], summary="按月归档")
async def list_archive(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100, description="每个月份最多返回多少篇")] = 50,
) -> list[ArchiveGroup]:
    service = ArticleService(session)
    groups: list[ArchiveGroup] = []
    for year_month, count in await service.archive():
        # 注意括号：await 的优先级低于下标，写成 await f()[..] 会对协程取下标而报
        # TypeError: 'coroutine' object is not subscriptable
        items = await service.list_by_month(year_month)
        groups.append(ArchiveGroup(year_month=year_month, count=count, items=items[:limit]))
    return groups


@router.get("/manage/list", response_model=Page[ArticleSummary], summary="文章列表（后台）")
async def list_managed_articles(
    session: SessionDep,
    page_params: PageParamsDep,
    user: AuthorUser,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(description="分类 slug 或 id")] = None,
    tag: Annotated[str | None, Query()] = None,
    author_id: Annotated[int | None, Query(description="仅站长可用")] = None,
    article_status: Annotated[ArticleStatus | None, Query(alias="status")] = None,
    sort: Annotated[ArticleSort, Query()] = ArticleSort.UPDATED,
) -> Page[ArticleSummary]:
    """后台列表：作者只看自己的（含草稿），站长可看全站并可按作者过滤。"""
    service = ArticleService(session)
    flt = _build_filter(
        keyword=keyword,
        category=category,
        tag=tag,
        author_id=author_id,
        article_status=article_status,
    )
    return await service.list_managed(
        flt=flt,
        sorting=ArticleSorting(sort=sort),
        page=page_params.page,
        page_size=page_params.page_size,
        viewer=user,
    )


@router.post(
    "", response_model=ArticleDetail, status_code=status.HTTP_201_CREATED, summary="新建文章"
)
async def create_article(
    payload: ArticleCreate, user: AuthorUser, session: SessionDep
) -> ArticleDetail:
    """新建文章。``status`` 直接传 ``published`` 即为发布，传 ``draft`` 存草稿。"""
    return await ArticleService(session).create(payload, user)


@router.get("/{slug_or_id}", response_model=ArticleDetail, summary="文章详情")
async def get_article(slug_or_id: str, session: SessionDep, viewer: OptionalUser) -> ArticleDetail:
    """按 slug 或 id 取详情。

    带登录态时可以预览自己的草稿；访客访问草稿会得到 404（而非 403），
    避免暴露「这里存在一篇未发布文章」。
    """
    return await ArticleService(session).get_detail(slug_or_id, viewer)


@router.patch("/{article_id}", response_model=ArticleDetail, summary="修改文章")
async def update_article(
    article_id: int, payload: ArticleUpdate, user: AuthorUser, session: SessionDep
) -> ArticleDetail:
    """部分更新：只提交需要改的字段，未出现的字段保持原值。"""
    return await ArticleService(session).update(article_id, payload, user)


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除文章")
async def delete_article(article_id: int, user: AuthorUser, session: SessionDep) -> None:
    await ArticleService(session).delete(article_id, user)


@router.post("/{article_id}/like", response_model=dict, summary="点赞")
async def like_article(article_id: int, session: SessionDep) -> dict[str, int]:
    """无需登录的点赞，返回最新点赞数。"""
    count = await ArticleService(session).like(article_id)
    return {"like_count": count}


@router.get("/{article_id}/related", response_model=list[ArticleSummary], summary="相关文章")
async def related_articles(
    article_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> list[ArticleSummary]:
    """同分类或共享标签的文章，按发布时间倒序。"""
    return await ArticleService(session).related(article_id, limit=limit)
