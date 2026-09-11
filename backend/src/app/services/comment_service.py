"""评论服务。"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ArticleStatus, Comment, User, UserRole
from app.repositories import ArticleRepository, CommentRepository, SiteRepository
from app.schemas.comment import CommentCreate, CommentRead
from app.schemas.common import Page
from app.utils.exceptions import BadRequestError, NotFoundError, PermissionDeniedError


def _is_loaded(instance: object, attribute: str) -> bool:
    """判断某个关系是否已经加载。

    异步会话下访问未加载的关系会触发惰性加载并抛 ``MissingGreenlet``，
    所以序列化前必须先问一句「这个关系到底加载了没有」，而不是想当然地访问。
    这个守卫比在每个调用点手动传参更不容易漏。
    """
    return attribute not in sa_inspect(instance).unloaded


class CommentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.comments = CommentRepository(session)
        self.articles = ArticleRepository(session)
        self.site = SiteRepository(session)

    # ---------------------------------------------------------------- 读取

    async def list_for_article(self, article_id: int, *, viewer: User | None) -> list[CommentRead]:
        """取一篇文章的评论树（两级）。

        登录的作者/站长能看到待审核评论（否则自己回复完看不到会很困惑），
        访客只看到已审核的。
        """
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")

        is_staff = viewer is not None and (
            viewer.role is UserRole.ADMIN or article.author_id == viewer.id
        )
        roots = await self.comments.list_roots_for_article(article_id, approved_only=not is_staff)
        return [self._to_read(root, include_pending=is_staff) for root in roots]

    async def list_moderation(
        self,
        *,
        page: int,
        page_size: int,
        approved: bool | None = None,
        article_id: int | None = None,
    ) -> Page[CommentRead]:
        """后台评论审核列表。

        刻意返回**扁平列表**（不带 replies）：审核场景下运营需要一眼看到所有待审
        评论，包括二级回复；把它们折叠进父评论反而会造成漏审。
        """
        total = await self.comments.count(approved=approved, article_id=article_id)
        rows = await self.comments.list_paged(
            offset=(page - 1) * page_size,
            limit=page_size,
            approved=approved,
            article_id=article_id,
        )
        return Page.build([self._to_read(row) for row in rows], total, page, page_size)

    @staticmethod
    def _to_read(comment: Comment, *, include_pending: bool = False) -> CommentRead:
        """把 ORM 对象转成响应体。

        回复列表只在**已经被加载**时才输出（见 ``_is_loaded``），未加载就当作空——
        绝不为了让响应「更完整」而触发一次惰性加载。需要回复的场景，
        查询时必须用 ``selectinload`` 显式声明。
        """
        replies: list[CommentRead] = []
        if _is_loaded(comment, "replies"):
            replies = [
                CommentService._to_read(reply)
                for reply in sorted(comment.replies, key=lambda item: item.id)
                if include_pending or reply.is_approved
            ]
        return CommentRead(
            id=comment.id,
            article_id=comment.article_id,
            parent_id=comment.parent_id,
            author_name=comment.author_name,
            author_site=comment.author_site,
            content=comment.content,
            is_admin_reply=comment.is_admin_reply,
            is_approved=comment.is_approved,
            created_at=comment.created_at,
            replies=replies,
        )

    # ---------------------------------------------------------------- 写入

    async def create(
        self,
        article_id: int,
        payload: CommentCreate,
        *,
        viewer: User | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> CommentRead:
        """发表评论。

        Raises:
            NotFoundError: 文章不存在或尚未发布。
            BadRequestError: 文章关闭了评论 / 站点禁止游客评论 / 回复目标不属于本文。
        """
        article = await self.articles.get(article_id)
        if article is None or article.status is ArticleStatus.DRAFT:
            raise NotFoundError("文章不存在或尚未发布")
        if not article.allow_comment:
            raise BadRequestError("该文章已关闭评论")

        profile = await self.site.get_profile()
        if viewer is None and profile is not None and not profile.allow_guest_comment:
            raise PermissionDeniedError("本站已关闭游客评论，请登录后再发表")

        if payload.parent_id is not None:
            parent = await self.comments.get(payload.parent_id)
            if parent is None or parent.article_id != article_id:
                # 关键校验：不检查的话，可以把评论挂到任意文章下面，污染别人的评论区
                raise BadRequestError("被回复的评论不属于这篇文章")

        is_staff = viewer is not None and (
            viewer.role is UserRole.ADMIN or article.author_id == viewer.id
        )
        need_approval = profile.comment_need_approval if profile else True

        # 登录用户不必重复填名字；游客必须留名，否则评论区会出现一堆无名氏
        if viewer is not None:
            display_name = viewer.nickname or viewer.username
        else:
            display_name = payload.author_name
            if not display_name:
                raise BadRequestError("请填写昵称")

        comment = await self.comments.create(
            article_id=article_id,
            parent_id=payload.parent_id,
            user_id=viewer.id if viewer else None,
            author_name=display_name,
            author_email=str(payload.author_email) if payload.author_email else None,
            author_site=payload.author_site,
            content=payload.content,
            # 站长的回复直接过审，否则「站长回复了但用户看不到」很荒谬
            is_approved=(not need_approval) or is_staff,
            is_admin_reply=is_staff,
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else None,
        )
        return self._to_read(comment, include_pending=True)

    async def set_approved(self, comment_id: int, approved: bool) -> CommentRead:
        comment = await self.comments.get(comment_id)
        if comment is None:
            raise NotFoundError("评论不存在")
        comment.is_approved = approved
        await self.session.flush()
        return self._to_read(comment, include_pending=True)

    async def delete(self, comment_id: int, *, operator: User) -> None:
        """删除评论。删顶级评论会连带删掉它的回复（数据库 CASCADE）。"""
        comment = await self.comments.get(comment_id)
        if comment is None:
            raise NotFoundError("评论不存在")
        # 作者可以管理自己文章下的评论
        article = await self.articles.get(comment.article_id)
        if operator.role is not UserRole.ADMIN and (
            article is None or article.author_id != operator.id
        ):
            raise PermissionDeniedError("只能管理自己文章下的评论")
        await self.comments.delete(comment)
