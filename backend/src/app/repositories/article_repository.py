"""文章仓储：列表筛选/排序、上下篇、归档、站点统计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, String, cast, func, or_, select, update
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql import ColumnElement

from app.models import Article, ArticleSort, ArticleStatus, Category, Comment, Tag
from app.repositories.base import BaseRepository

# 「有效发布时间」：已发布用 published_at，草稿回退到 created_at。
# 统一用它排序，就不用到处处理 NULL 在 SQLite / PostgreSQL 中排序位置不一致的坑。
_EFFECTIVE_DATE = func.coalesce(Article.published_at, Article.created_at)


def _like_pattern(keyword: str) -> str:
    """转义 LIKE 通配符。

    不转义的话用户搜 ``100%`` 会变成「以 100 开头」的模糊匹配，结果莫名其妙。
    """
    escaped = (
        keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")
    )
    return f"%{escaped}%"


@dataclass(slots=True)
class ArticleFilter:
    """列表筛选条件。字段默认值语义 = 「不限制」。"""

    keyword: str | None = None
    statuses: tuple[ArticleStatus, ...] = ()
    category_id: int | None = None
    category_slug: str | None = None
    tag_slug: str | None = None
    author_id: int | None = None
    is_top: bool | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.keyword,
                self.statuses,
                self.category_id,
                self.category_slug,
                self.tag_slug,
            )
        )


@dataclass(slots=True)
class ArticleSorting:
    """排序描述对象，把枚举翻译成白名单化的 ORDER BY 子句。"""

    sort: ArticleSort = ArticleSort.LATEST
    # 置顶优先是博客的通行预期；关掉它可以让列表纯粹按时间/热度排
    top_first: bool = True

    def clauses(self) -> list[ColumnElement[Any]]:
        secondary: list[ColumnElement[Any]]
        match self.sort:
            case ArticleSort.LATEST:
                secondary = [_EFFECTIVE_DATE.desc(), Article.id.desc()]
            case ArticleSort.OLDEST:
                secondary = [_EFFECTIVE_DATE.asc(), Article.id.asc()]
            case ArticleSort.HOTTEST:
                secondary = [Article.view_count.desc(), _EFFECTIVE_DATE.desc()]
            case ArticleSort.UPDATED:
                secondary = [Article.updated_at.desc(), Article.id.desc()]
            case ArticleSort.TITLE:
                secondary = [Article.title.asc()]
            case _:  # pragma: no cover - 枚举已穷举，防御性兜底
                secondary = [_EFFECTIVE_DATE.desc()]
        return [Article.is_top.desc(), *secondary] if self.top_first else secondary


@dataclass(slots=True)
class ArticleListRow:
    """列表查询返回的一行：文章本体 + 已审核评论数。"""

    article: Article
    comment_count: int = 0


class ArticleRepository(BaseRepository[Article]):
    model = Article

    # ------------------------------------------------------------------ 查询

    @staticmethod
    def _comment_count_expr() -> ColumnElement[int]:
        return (
            select(func.count(Comment.id))
            .where(Comment.article_id == Article.id, Comment.is_approved.is_(True))
            .correlate(Article)
            .scalar_subquery()
        )

    @staticmethod
    def _apply_filters(stmt: Select[Any], flt: ArticleFilter) -> Select[Any]:
        if flt.statuses:
            stmt = stmt.where(Article.status.in_(list(flt.statuses)))
        if flt.is_top is not None:
            stmt = stmt.where(Article.is_top.is_(flt.is_top))
        if flt.category_id is not None:
            stmt = stmt.where(Article.category_id == flt.category_id)
        if flt.category_slug:
            # 用 EXISTS 子查询而不是 JOIN：天然不会让结果集因关联而重复
            stmt = stmt.where(Article.category.has(Category.slug == flt.category_slug))
        if flt.tag_slug:
            stmt = stmt.where(Article.tags.any(Tag.slug == flt.tag_slug))
        if flt.author_id is not None:
            stmt = stmt.where(Article.author_id == flt.author_id)
        if flt.published_from is not None:
            stmt = stmt.where(Article.published_at >= flt.published_from)
        if flt.published_to is not None:
            stmt = stmt.where(Article.published_at <= flt.published_to)
        if flt.keyword and flt.keyword.strip():
            pattern = _like_pattern(flt.keyword.strip())
            stmt = stmt.where(
                or_(
                    Article.title.ilike(pattern, escape="\\"),
                    Article.summary.ilike(pattern, escape="\\"),
                    Article.content_md.ilike(pattern, escape="\\"),
                )
            )
        return stmt

    async def list_paged(
        self,
        *,
        flt: ArticleFilter,
        sorting: ArticleSorting,
        offset: int,
        limit: int,
    ) -> list[ArticleListRow]:
        """分页取列表。

        刻意只 ``select(Article, comment_count)`` 而不带 content_md 之外的大字段——
        正文由详情接口单独取，列表接口保持轻量。
        """
        stmt = self._apply_filters(select(Article), flt)
        stmt = (
            stmt.add_columns(self._comment_count_expr().label("comment_count"))
            .order_by(*sorting.clauses())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            ArticleListRow(article=row[0], comment_count=int(row[1] or 0)) for row in result.all()
        ]

    async def count(self, flt: ArticleFilter) -> int:
        stmt = self._apply_filters(select(func.count()).select_from(Article), flt)
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_by_slug(
        self, slug: str, *, statuses: tuple[ArticleStatus, ...] = ()
    ) -> Article | None:
        stmt = self._apply_filters(select(Article), ArticleFilter(statuses=statuses)).where(
            Article.slug == slug
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_comment_count(self, article_id: int, *, approved_only: bool = True) -> int:
        stmt = select(func.count()).select_from(Comment).where(Comment.article_id == article_id)
        if approved_only:
            stmt = stmt.where(Comment.is_approved.is_(True))
        return int((await self.session.execute(stmt)).scalar_one())

    async def slug_exists(self, slug: str, *, exclude_id: int | None = None) -> bool:
        stmt = select(func.count()).select_from(Article).where(Article.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(Article.id != exclude_id)
        return int((await self.session.execute(stmt)).scalar_one()) > 0

    async def get_neighbors(self, article: Article) -> tuple[Article | None, Article | None]:
        """取相邻文章。

        Returns:
            ``(prev, next)``：prev 是更早的一篇，next 是更新的一篇；没有则为 ``None``。
            只考虑已发布文章，且用 ``(有效时间, id)`` 做联合比较，
            保证同一秒内发布的两篇文章也有稳定顺序。
        """
        anchor = article.published_at or article.created_at
        base = select(Article).where(Article.status == ArticleStatus.PUBLISHED)

        prev_stmt = (
            base.where(
                or_(
                    anchor > _EFFECTIVE_DATE,
                    (anchor == _EFFECTIVE_DATE) & (Article.id < article.id),
                )
            )
            .order_by(_EFFECTIVE_DATE.desc(), Article.id.desc())
            .limit(1)
        )
        next_stmt = (
            base.where(
                or_(
                    anchor < _EFFECTIVE_DATE,
                    (anchor == _EFFECTIVE_DATE) & (Article.id > article.id),
                )
            )
            .order_by(_EFFECTIVE_DATE.asc(), Article.id.asc())
            .limit(1)
        )
        prev = (await self.session.execute(prev_stmt)).scalars().first()
        nxt = (await self.session.execute(next_stmt)).scalars().first()
        return prev, nxt

    async def list_archive(self, *, statuses: tuple[ArticleStatus, ...]) -> list[tuple[str, int]]:
        """按月归档。

        用 ``substr(cast(时间, 文本), 1, 7)`` 取 ``YYYY-MM``——这个写法在 SQLite
        与 PostgreSQL 上都能跑，避免为了一行 GROUP BY 去写两套方言。
        """
        ym = func.substr(cast(_EFFECTIVE_DATE, String), 1, 7)
        stmt = (
            select(ym.label("ym"), func.count(Article.id).label("cnt"))
            .where(Article.status.in_(list(statuses)))
            .group_by(ym)
            .order_by(ym.desc())
        )
        result = await self.session.execute(stmt)
        return [(str(row.ym), int(row.cnt)) for row in result.all()]

    async def list_by_month(
        self, *, year_month: str, statuses: tuple[ArticleStatus, ...], limit: int = 200
    ) -> list[Article]:
        ym = func.substr(cast(_EFFECTIVE_DATE, String), 1, 7)
        stmt = (
            select(Article)
            .where(Article.status.in_(list(statuses)), ym == year_month)
            .order_by(_EFFECTIVE_DATE.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------ 写入

    async def increment_view(self, article: Article) -> int:
        """原子自增阅读量，并把新值同步回会话里的对象。

        绝不用「读出来 +1 再存回去」——并发下会丢计数。这里交给数据库做
        ``SET view_count = view_count + 1``，一条语句搞定。

        两个必须加的执行选项：

        - ``synchronize_session=False``：默认的自动同步策略在遇到
          ``view_count + 1`` 这种数据库表达式时无法求值，会把这行对象上的
          ``updated_at``（带 onupdate）标记为过期。异步会话里再去访问它就会抛
          ``MissingGreenlet``。
        - 配合 ``set_committed_value``：把新值直接告诉 ORM 并标记为「已提交」，
          这样响应里能拿到新计数，也不会反过来生成一条多余的 UPDATE。

        Returns:
            自增后的阅读量。
        """
        await self.session.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(view_count=Article.view_count + 1)
            .execution_options(synchronize_session=False)
        )
        new_value = (article.view_count or 0) + 1
        set_committed_value(article, "view_count", new_value)
        return new_value

    async def increment_like(self, article: Article) -> int:
        """原子自增点赞数，说明同 ``increment_view``。"""
        await self.session.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(like_count=Article.like_count + 1)
            .execution_options(synchronize_session=False)
        )
        new_value = (article.like_count or 0) + 1
        set_committed_value(article, "like_count", new_value)
        return new_value

    async def set_tags(self, article: Article, tags: list[Tag]) -> None:
        """整体替换文章的标签集合（先清后加，幂等）。

        必须先 ``refresh`` 把现有集合真正加载进来，再赋值。原因是：
        直接写 ``article.tags = tags`` 时，SQLAlchemy 为了算出「增删了哪些」
        会去**惰性加载**原有的 collection —— 而异步会话里的惰性加载会在事件
        循环里发起同步 IO，直接抛 ``MissingGreenlet``。

        这个坑只在真正跑起请求时才会暴露，静态检查完全看不出来。
        """
        await self.session.refresh(article, ["tags"])
        article.tags = list(tags)
        await self.session.flush()

    # ------------------------------------------------------------------ 统计

    async def stats(self) -> dict[str, Any]:
        """站点仪表盘数字，一次查询拿全，避免仪表盘发 8 个请求。"""
        status_rows = await self.session.execute(
            select(Article.status, func.count(Article.id)).group_by(Article.status)
        )
        by_status: dict[str, int] = {
            (row[0].value if hasattr(row[0], "value") else str(row[0])): int(row[1])
            for row in status_rows.all()
        }
        views = await self.session.execute(select(func.coalesce(func.sum(Article.view_count), 0)))
        latest = await self.session.execute(
            select(func.max(Article.published_at)).where(Article.status == ArticleStatus.PUBLISHED)
        )
        return {
            "article_total": sum(by_status.values()),
            "published_total": by_status.get(ArticleStatus.PUBLISHED.value, 0),
            "draft_total": by_status.get(ArticleStatus.DRAFT.value, 0),
            "archived_total": by_status.get(ArticleStatus.ARCHIVED.value, 0),
            "total_views": int(views.scalar_one()),
            "latest_published_at": latest.scalar_one(),
        }
