"""文章版本历史路由。

挂在 ``/articles/{article_id}/revisions`` 下而不是独立的 ``/revisions``：
版本永远从属于一篇文章，路径里带上归属关系，权限校验的语义也更自然
（「你有没有资格看这篇文章的历史」）。

注意路由声明顺序：这些静态后缀要写在 ``articles.py`` 的
``/{slug_or_id}`` **之前**，否则 ``/articles/5/revisions`` 会被
``/{slug_or_id}`` 吃掉去当 slug 查。所以它们放在这个独立模块里，
在 ``api/v1/__init__.py`` 中先于 articles 注册。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AuthorUser, SessionDep
from app.schemas.article import ArticleDetail
from app.schemas.revision import RevisionRead
from app.services import ArticleQueryService
from app.services.revision_service import RevisionService

router = APIRouter(prefix="/articles", tags=["文章版本"])


@router.get(
    "/{article_id}/revisions",
    response_model=list[RevisionRead],
    summary="文章版本列表（作者/站长）",
)
async def list_revisions(
    article_id: int,
    session: SessionDep,
    user: AuthorUser,
) -> list[RevisionRead]:
    """按时间倒序列出历史版本。**不含正文**——一屏几十条正文会让响应上兆。"""
    return await RevisionService(session).list_for_article(article_id, viewer=user)


@router.get(
    "/{article_id}/revisions/{revision_id}",
    response_model=RevisionRead,
    summary="单个版本详情（含正文）",
)
async def get_revision(
    article_id: int,
    revision_id: int,
    session: SessionDep,
    user: AuthorUser,
) -> RevisionRead:
    return await RevisionService(session).get_revision(article_id, revision_id, viewer=user)


@router.post(
    "/{article_id}/revisions/{revision_id}/restore",
    response_model=ArticleDetail,
    summary="恢复到指定版本（作者/站长）",
)
async def restore_revision(
    article_id: int,
    revision_id: int,
    session: SessionDep,
    user: AuthorUser,
) -> ArticleDetail:
    """把某一版的内容写回文章。

    恢复前会自动为**当前内容**留一版（reason=restore），所以恢复错了还能退回来。
    """
    service = RevisionService(session)
    await service.restore(article_id, revision_id, viewer=user)
    await session.commit()
    # 复用详情构建，保证恢复后的响应与其他写接口结构一致
    return await ArticleQueryService(session).get_detail(str(article_id), user, count_view=False)
