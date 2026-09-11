"""评论仓储。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import Comment
from app.repositories.base import BaseRepository


class CommentRepository(BaseRepository[Comment]):
    model = Comment

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
    ) -> list[Comment]:
        stmt = select(Comment)
        if approved is not None:
            stmt = stmt.where(Comment.is_approved.is_(approved))
        if article_id is not None:
            stmt = stmt.where(Comment.article_id == article_id)
        stmt = stmt.order_by(Comment.id.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, approved: bool | None = None, article_id: int | None = None) -> int:
        stmt = select(func.count()).select_from(Comment)
        if approved is not None:
            stmt = stmt.where(Comment.is_approved.is_(approved))
        if article_id is not None:
            stmt = stmt.where(Comment.article_id == article_id)
        return int((await self.session.execute(stmt)).scalar_one())

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
