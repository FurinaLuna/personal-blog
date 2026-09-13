"""分类与标签仓储。"""

from __future__ import annotations

from sqlalchemy import and_, func, select

from app.models import Article, ArticleStatus, Category, Tag, article_tags
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository[Category]):
    model = Category

    async def get_by_slug(self, slug: str) -> Category | None:
        return await self.get_by("slug", slug)

    async def get_by_name(self, name: str) -> Category | None:
        return await self.get_by("name", name)

    async def slug_exists(self, slug: str, *, exclude_id: int | None = None) -> bool:
        return await self.exists_by("slug", slug, exclude_id=exclude_id)

    async def list_all(self) -> list[Category]:
        result = await self.session.execute(
            select(Category).order_by(Category.sort_order, Category.id)
        )
        return list(result.scalars().all())

    async def list_with_counts(
        self, *, statuses: tuple[ArticleStatus, ...]
    ) -> list[tuple[Category, int]]:
        """分类 + 该分类下的文章数。

        统计口径与列表页保持一致（只算给定状态），否则会出现「分类显示 12 篇，
        点进去只有 8 篇」的经典不一致。
        """
        stmt = (
            select(Category, func.count(Article.id))
            .outerjoin(
                Article,
                and_(Article.category_id == Category.id, Article.status.in_(list(statuses))),
            )
            .group_by(Category.id)
            .order_by(Category.sort_order, Category.id)
        )
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]


class TagRepository(BaseRepository[Tag]):
    model = Tag

    async def get_by_slug(self, slug: str) -> Tag | None:
        return await self.get_by("slug", slug)

    async def get_by_name(self, name: str) -> Tag | None:
        return await self.get_by("name", name)

    async def get_by_names(self, names: list[str]) -> list[Tag]:
        if not names:
            return []
        result = await self.session.execute(select(Tag).where(Tag.name.in_(names)))
        return list(result.scalars().all())

    async def slug_exists(self, slug: str, *, exclude_id: int | None = None) -> bool:
        return await self.exists_by("slug", slug, exclude_id=exclude_id)

    async def list_all(self) -> list[Tag]:
        result = await self.session.execute(select(Tag).order_by(Tag.name))
        return list(result.scalars().all())

    async def list_with_counts(
        self,
        *,
        statuses: tuple[ArticleStatus, ...],
        limit: int | None = None,
        min_count: int = 0,
    ) -> list[tuple[Tag, int]]:
        count_expr = func.count(Article.id)
        stmt = (
            select(Tag, count_expr)
            .outerjoin(article_tags, article_tags.c.tag_id == Tag.id)
            .outerjoin(
                Article,
                and_(
                    Article.id == article_tags.c.article_id,
                    Article.status.in_(list(statuses)),
                ),
            )
            .group_by(Tag.id)
            .order_by(count_expr.desc(), Tag.id)
        )
        if min_count > 0:
            # 过滤前移到 SQL（HAVING 先于 LIMIT 生效）：语义即「计数达标的前 N 个」，
            # 避免「先截断再在 Python 侧过滤」让返回数量少于调用方预期
            stmt = stmt.having(count_expr >= min_count)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]

    async def count_usage(self, tag_id: int) -> int:
        stmt = select(func.count()).select_from(article_tags).where(article_tags.c.tag_id == tag_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def delete_orphans(self) -> int:
        """清理没有任何文章引用的标签，返回删除条数。

        作者频繁改标签很容易留下大量空标签，让标签云变得没法看。
        """
        orphan_ids = (
            select(Tag.id)
            .outerjoin(article_tags, article_tags.c.tag_id == Tag.id)
            .where(article_tags.c.article_id.is_(None))
        )
        rows = (await self.session.execute(orphan_ids)).scalars().all()
        if not rows:
            return 0
        result = await self.session.execute(select(Tag).where(Tag.id.in_(list(rows))))
        tags = result.scalars().all()
        for tag in tags:
            await self.session.delete(tag)
        await self.session.flush()
        return len(tags)
