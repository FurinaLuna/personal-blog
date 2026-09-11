"""评论路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.api.deps import AuthorUser, OptionalUser, SessionDep
from app.api.pagination import PageParamsDep
from app.schemas.comment import CommentCreate, CommentModerate, CommentRead
from app.schemas.common import Message, Page
from app.services import CommentService

router = APIRouter(prefix="/comments", tags=["评论"])


@router.get("/article/{article_id}", response_model=list[CommentRead], summary="某篇文章的评论树")
async def list_article_comments(
    article_id: int, session: SessionDep, viewer: OptionalUser
) -> list[CommentRead]:
    """返回两级评论树。作者/站长可以看到自己文章下的待审核评论。"""
    return await CommentService(session).list_for_article(article_id, viewer=viewer)


@router.post(
    "/article/{article_id}",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    summary="发表评论（支持匿名）",
)
async def create_comment(
    article_id: int,
    payload: CommentCreate,
    request: Request,
    session: SessionDep,
    viewer: OptionalUser,
) -> CommentRead:
    """发表评论或回复。

    是否免审核由站点配置决定；无论是否需要审核，接口都会返回创建结果，
    前端据此提示「已提交，等待审核」或「发布成功」。
    """
    # X-Forwarded-For 取第一段：反向代理后面拿到的 client.host 是代理地址
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return await CommentService(session).create(
        article_id,
        payload,
        viewer=viewer,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )


# ------------------------------------------------------------------ 后台管理


@router.get("", response_model=Page[CommentRead], summary="评论列表（作者）")
async def list_comments(
    user: AuthorUser,
    session: SessionDep,
    page_params: PageParamsDep,
    approved: Annotated[bool | None, Query(description="按审核状态过滤，不传为全部")] = None,
    article_id: Annotated[int | None, Query()] = None,
) -> Page[CommentRead]:
    return await CommentService(session).list_moderation(
        page=page_params.page,
        page_size=page_params.page_size,
        approved=approved,
        article_id=article_id,
    )


@router.patch("/{comment_id}", response_model=CommentRead, summary="审核评论（作者）")
async def moderate_comment(
    comment_id: int, payload: CommentModerate, _: AuthorUser, session: SessionDep
) -> CommentRead:
    """通过 / 撤回评论。``is_approved=True`` 即放行。"""
    approved = True if payload.is_approved is None else payload.is_approved
    return await CommentService(session).set_approved(comment_id, approved)


@router.delete("/{comment_id}", response_model=Message, summary="删除评论（作者）")
async def delete_comment(comment_id: int, user: AuthorUser, session: SessionDep) -> Message:
    """删除顶级评论会连带删除其下所有回复。"""
    await CommentService(session).delete(comment_id, operator=user)
    return Message(detail="评论已删除")
