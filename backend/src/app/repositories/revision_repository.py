"""文章版本历史仓储。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import ArticleRevision
from app.repositories.base import BaseRepository


class ArticleRevisionRepository(BaseRepository[ArticleRevision]):
    model = ArticleRevision

    async def list_for_article(
        self, article_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[ArticleRevision]:
        """某篇文章的版本列表，最新的在前。

        刻意**不按 created_at 单列排序**：同一秒内连续保存两次（自动保存 +
        手动保存）时间戳可能相同，只按时间排会让顺序不确定。
        追加 id 作为次级排序键，保证结果稳定可复现。
        """
        stmt = (
            select(ArticleRevision)
            .where(ArticleRevision.article_id == article_id)
            .order_by(ArticleRevision.created_at.desc(), ArticleRevision.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_article(self, article_id: int) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(ArticleRevision)
            .where(ArticleRevision.article_id == article_id)
        )
        return int(result.scalar_one())

    async def latest_for_article(self, article_id: int) -> ArticleRevision | None:
        """最近一版。用于「保存前先看看和上一版有没有差别」。"""
        rows = await self.list_for_article(article_id, limit=1)
        return rows[0] if rows else None
