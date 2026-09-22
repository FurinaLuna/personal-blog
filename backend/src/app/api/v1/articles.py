"""文章路由。

路由声明顺序很重要：``/archive``、``/manage`` 这类静态路径必须写在
``/{slug_or_id}`` 之前，否则 FastAPI 会把 "archive" 当成一个 slug 去查文章。

本层职责边界：只做 HTTP 参数声明（Query 校验、别名）与响应模型标注，
**不 import 仓储层**——查询语义（如「分类可用 slug 或 id」）归服务层，
这里传下去的都是普通数据。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.api.deps import (
    LIKE_RATE_LIMIT,
    SEARCH_RATE_LIMIT,
    AuthorUser,
    OptionalUser,
    SessionDep,
    client_ip,
)
from app.api.pagination import PageParamsDep
from app.models import ArticleSort, ArticleStatus
from app.schemas.article import ArticleCreate, ArticleDetail, ArticleSummary, ArticleUpdate
from app.schemas.common import Page
from app.schemas.site import ArchiveGroup
from app.services import ArticleService, VisitStatsService

router = APIRouter(prefix="/articles", tags=["文章"])


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
    return await ArticleService(session).list_public(
        page_params=page_params,
        keyword=keyword,
        category=category,
        tag=tag,
        author_id=author_id,
        sort=sort,
    )


@router.get("/search", response_model=Page[ArticleSummary], summary="全文检索（前台）")
async def search_articles(
    session: SessionDep,
    page_params: PageParamsDep,
    q: Annotated[str, Query(min_length=1, max_length=100, description="搜索词")],
    _: None = SEARCH_RATE_LIMIT,
) -> Page[ArticleSummary]:
    """站内搜索：按相关度返回命中文章。

    与列表接口的 ``keyword`` 参数的区别在**排序**：列表接口的关键词是筛选，
    结果仍按时间/热度排；这个接口按 bm25 相关度排（标题权重最高）。
    独立成一个端点而不是给列表接口加 ``sort=relevance``，是因为两者的
    语义与实现路径都不同——列表走 LIKE，这里走 FTS5。

    声明在 ``/archive`` 旁边而不是 ``/{slug_or_id}`` 之后：静态路径必须
    先注册，否则 ``/search`` 会被当成 slug 去查文章。
    """
    return await ArticleService(session).search(keyword=q, page_params=page_params)


@router.get("/archive", response_model=list[ArchiveGroup], summary="按月归档")
async def list_archive(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100, description="每个月份最多返回多少篇")] = 50,
) -> list[ArchiveGroup]:
    return await ArticleService(session).archive_groups(limit_per_month=limit)


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
    return await ArticleService(session).list_managed(
        page_params=page_params,
        viewer=user,
        keyword=keyword,
        category=category,
        tag=tag,
        author_id=author_id,
        article_status=article_status,
        sort=sort,
    )


@router.post(
    "", response_model=ArticleDetail, status_code=status.HTTP_201_CREATED, summary="新建文章"
)
async def create_article(
    payload: ArticleCreate, user: AuthorUser, session: SessionDep
) -> ArticleDetail:
    """新建文章。``status`` 直接传 ``published`` 即为发布，传 ``draft`` 存草稿。"""
    detail = await ArticleService(session).create(payload, user)
    # 写路由显式提交：yield 依赖的收尾提交发生在响应发送之后，会把「提交落地」
    # 暴露给连接池里的下一个请求，引入写后立读旧快照的竞态（见 db/session.py）
    await session.commit()
    return detail


@router.get("/{slug_or_id}", response_model=ArticleDetail, summary="文章详情")
async def get_article(
    slug_or_id: str, request: Request, session: SessionDep, viewer: OptionalUser
) -> ArticleDetail:
    """按 slug 或 id 取详情。

    带登录态时可以预览自己的草稿；访客访问草稿会得到 404（而非 403），
    避免暴露「这里存在一篇未发布文章」。
    """
    detail = await ArticleService(session).get_detail(slug_or_id, viewer)
    # 详情 GET 也会写库（已发布文章的浏览计数 + 访问日志）。显式提交与其他
    # 写路由同口径，不依赖 get_session 的收尾提交，规避写后读旧快照的竞态
    # （见 db/session.py）。访问记录与 view_count 同门槛：仅已发布文章
    if detail.status is ArticleStatus.PUBLISHED:
        await VisitStatsService(session).record(detail.id, ip=client_ip(request))
    await session.commit()
    return detail


@router.patch("/{article_id}", response_model=ArticleDetail, summary="修改文章")
async def update_article(
    article_id: int, payload: ArticleUpdate, user: AuthorUser, session: SessionDep
) -> ArticleDetail:
    """部分更新：只提交需要改的字段，未出现的字段保持原值。"""
    detail = await ArticleService(session).update(article_id, payload, user)
    await session.commit()
    return detail


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除文章")
async def delete_article(article_id: int, user: AuthorUser, session: SessionDep) -> None:
    await ArticleService(session).delete(article_id, user)
    await session.commit()


@router.post("/{article_id}/like", response_model=dict, summary="点赞")
async def like_article(
    article_id: int, session: SessionDep, _: None = LIKE_RATE_LIMIT
) -> dict[str, int]:
    """无需登录的点赞，返回最新点赞数。

    因为无需登录，这个接口天然可被脚本刷——限流是它唯一的门槛。
    """
    count = await ArticleService(session).like(article_id)
    await session.commit()
    return {"like_count": count}


@router.get("/{article_id}/related", response_model=list[ArticleSummary], summary="相关文章")
async def related_articles(
    article_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> list[ArticleSummary]:
    """同分类或共享标签的文章，按发布时间倒序。"""
    return await ArticleService(session).related(article_id, limit=limit)
