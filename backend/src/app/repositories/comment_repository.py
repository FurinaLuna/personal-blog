"""评论仓储。"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import joinedload, selectinload

from app.models import Article, Comment
from app.repositories.base import BaseRepository


class CommentRepository(BaseRepository[Comment]):
    model = Comment

    async def get_with_relations(self, comment_id: int) -> Comment | None:
        """按主键取评论并预载 parent / article（通知任务用）。

        通知跑在自己的后台任务里，绝不能触发惰性加载（异步下抛
        ``MissingGreenlet``），查询时一次 join 清楚。
        """
        stmt = (
            select(Comment)
            .where(Comment.id == comment_id)
            .options(joinedload(Comment.parent), joinedload(Comment.article))
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_roots_for_article(
        self, article_id: int, *, approved_only: bool
    ) -> list[Comment]:
        """取某篇文章的顶级评论，并把二级回复一起 selectin 加载出来。

        明确用 ``selectinload`` 而不是靠惰性加载：异步会话下惰性加载会直接抛
        ``MissingGreenlet``，这类 bug 只在真跑起来才暴露，不如查询时一次写清楚。
        """
        stmt = (
            select(Comment)
            .where(Comment.article_id == article_id, Comment.parent_id.is_(None))
            .options(selectinload(Comment.replies))
            .order_by(Comment.created_at.desc(), Comment.id.desc())
        )
        if approved_only:
            stmt = stmt.where(Comment.is_approved.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def list_paged(
        self,
        *,
        offset: int,
        limit: int,
        approved: bool | None = None,
        article_id: int | None = None,
        article_author_id: int | None = None,
    ) -> list[Comment]:
        stmt = self._scoped(
            select(Comment),
            approved=approved,
            article_id=article_id,
            article_author_id=article_author_id,
        )
        stmt = stmt.order_by(Comment.id.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        approved: bool | None = None,
        article_id: int | None = None,
        article_author_id: int | None = None,
    ) -> int:
        stmt = self._scoped(
            select(func.count()).select_from(Comment),
            approved=approved,
            article_id=article_id,
            article_author_id=article_author_id,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    @staticmethod
    def _scoped(
        stmt: Select,
        *,
        approved: bool | None,
        article_id: int | None,
        article_author_id: int | None,
    ) -> Select:
        """count 与 list_paged 共用的过滤口径——两边必须一致，否则分页总数对不上。

        ``article_author_id`` 用来把后台审核列表按作者收口：作者只能看到自己文章
        下的评论。用子查询而不是 JOIN，避免改变返回行数。
        """
        if approved is not None:
            stmt = stmt.where(Comment.is_approved.is_(approved))
        if article_id is not None:
            stmt = stmt.where(Comment.article_id == article_id)
        if article_author_id is not None:
            own_article_ids = select(Article.id).where(Article.author_id == article_author_id)
            stmt = stmt.where(Comment.article_id.in_(own_article_ids))
        return stmt

    async def count_by_article(self, article_ids: list[int]) -> dict[int, int]:
        """批量统计多篇文章的已审核评论数（列表页用，避免 N+1）。"""
        if not article_ids:
            return {}
        stmt = (
            select(Comment.article_id, func.count(Comment.id))
            .where(Comment.article_id.in_(article_ids), Comment.is_approved.is_(True))
            .group_by(Comment.article_id)
        )
        result = await self.session.execute(stmt)
        return {int(row[0]): int(row[1]) for row in result.all()}
