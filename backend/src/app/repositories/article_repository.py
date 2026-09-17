"""文章仓储：列表筛选/排序、上下篇、归档、站点统计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, String, bindparam, cast, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
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
    series_id: int | None = None
    is_top: bool | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    # 「此刻对公众可见」：published_at 必须非空且不晚于该时刻。
    # 用来实现定时发布——排在未来的文章不该出现在前台列表里。
    # 与 published_to 的区别在于它**排除 NULL**：草稿没有发布时间，
    # 不能因为「NULL <= 某时刻」在 SQL 里求值为 NULL 就被当成通过。
    visible_at: datetime | None = None

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

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        # None = 还没探测过。探测结果缓存在实例上（仓储是请求级的，生命周期正好）
        self._fts_available: bool | None = None

    @property
    def supports_full_text_search(self) -> bool:
        """当前连接上 FTS5 全文检索是否可用（需先 ``detect_full_text_search``）。

        只判断方言是不够的：``articles_fts`` 是 FTS5 **虚拟表**，
        ``Base.metadata.create_all`` 不会创建它——只有跑过 alembic 迁移才有。
        于是「按 README 快速开始直接启动（自动建表、不跑迁移）」这条最常见的
        开发路径上，``MATCH articles_fts`` 会抛 "no such table" 变成 500。

        所以这里要求先实际探测一次，缺索引时返回 False，调用方安静地退回 LIKE。
        """
        return self._fts_available is True

    async def detect_full_text_search(self) -> bool:
        """探测并缓存 FTS 可用性，返回探测结果。"""
        from app.db.fulltext import fulltext_available

        self._fts_available = await fulltext_available(self.session)
        return self._fts_available

    @staticmethod
    def _fts_query(query: str) -> str:
        """把用户输入转成安全的 FTS5 查询串。

        整串用双引号包成**短语**，内部的 ``"`` 双写转义。这样做有两个好处：

        1. **防语法注入**：FTS5 的查询语法里有 ``*`` ``(`` ``)`` ``:`` ``^``
           ``NOT`` ``OR`` ``-`` 等操作符，直接拼进去轻则报错、重则改变查询语义。
           包成短语之后它们都只是普通字符。
        2. 语义正确：用户要搜的就是「这几个字连在一起」，而不是一次布尔查询。
        """
        escaped = query.strip().replace('"', '""')
        return f'"{escaped}"'

    async def search_ids(
        self,
        query: str,
        *,
        limit: int,
        statuses: tuple[ArticleStatus, ...] = (),
        visible_at: datetime | None = None,
    ) -> list[int]:
        """FTS5 全文检索，按相关度（bm25）返回文章 id。

        可见性条件必须写进**同一条 SQL**：先按相关度取前 N 条、再在外面过滤掉
        不可见的，会导致「第一页只剩两条」这种分页数量对不上的问题——
        草稿与未到发布时间的文章都还在索引里。

        ``bm25()`` 返回的是**越小越相关**的负数，所以是 ``ORDER BY score``（升序）。
        这一点很容易写反，写反的结果是搜索结果完全颠倒。

        注意：trigram 分词要求查询词不少于 3 个字符，调用方需自行判断
        （见 ``ArticleService.search``），这里不做兼容——短查询走 LIKE 更准。
        """
        if not query.strip():
            return []

        stmt = text(
            """
            SELECT a.id AS id
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.rowid
            WHERE articles_fts MATCH :query
              AND (:status_filter = 0 OR a.status IN :statuses)
              AND (
                  :visible_at IS NULL
                  OR (a.published_at IS NOT NULL AND a.published_at <= :visible_at)
              )
            ORDER BY bm25(articles_fts, 10.0, 5.0, 1.0), a.id DESC
            LIMIT :limit
            """
        ).bindparams(
            bindparam("query", value=self._fts_query(query)),
            bindparam("status_filter", value=1 if statuses else 0),
            bindparam(
                "statuses",
                value=[s.value if hasattr(s, "value") else s for s in statuses],
                expanding=True,
            ),
            bindparam("visible_at", value=visible_at),
            bindparam("limit", value=limit),
        )
        result = await self.session.execute(stmt)
        return [int(row.id) for row in result.all()]

    async def list_by_ids_ordered(self, ids: list[int]) -> list[ArticleListRow]:
        """按给定 id 顺序取文章（含已审核评论数）。

        搜索结果必须保持**相关度顺序**，而 SQL 的 ``IN`` 不保证顺序，
        所以在 Python 侧按传入的 id 顺序重排。数量受搜索上限约束（≤50），
        重排成本可以忽略。

        渲染所需的关联（author / category / series / tags）由 mapper 级的
        eager 策略自动带出，这里不需要显式 selectinload。
        """
        if not ids:
            return []

        comment_count = self._comment_count_expr()
        stmt = select(Article, comment_count).where(Article.id.in_(ids))
        rows = (await self.session.execute(stmt)).all()

        by_id = {
            article.id: ArticleListRow(article=article, comment_count=int(count))
            for article, count in rows
        }
        # 只保留确实取到的（可能刚好被并发删掉了），并严格按相关度顺序排列
        return [by_id[item_id] for item_id in ids if item_id in by_id]

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
        if flt.series_id is not None:
            stmt = stmt.where(Article.series_id == flt.series_id)
        if flt.published_from is not None:
            stmt = stmt.where(Article.published_at >= flt.published_from)
        if flt.published_to is not None:
            stmt = stmt.where(Article.published_at <= flt.published_to)
        if flt.visible_at is not None:
            stmt = stmt.where(
                Article.published_at.is_not(None),
                Article.published_at <= flt.visible_at,
            )
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

    async def list_related(
        self, article: Article, *, statuses: tuple[ArticleStatus, ...], limit: int = 5
    ) -> list[Article]:
        """相关文章：同分类或共享任一标签，按发布时间倒序，排除自身。

        要求调用方保证 ``article.tags`` 已加载（本项目的详情查询走 selectin，
        天然满足），否则异步会话里访问集合会抛惰性加载异常。
        """
        conditions: list[ColumnElement[Any]] = []
        if article.category_id is not None:
            conditions.append(Article.category_id == article.category_id)
        tag_ids = [tag.id for tag in article.tags]
        if tag_ids:
            conditions.append(Article.tags.any(Tag.id.in_(tag_ids)))
        if not conditions:
            return []
        stmt = (
            select(Article)
            .where(Article.id != article.id)
            .where(Article.status.in_(list(statuses)))
            .where(or_(*conditions))
            .order_by(_EFFECTIVE_DATE.desc(), Article.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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
        return await self.exists_by("slug", slug, exclude_id=exclude_id)

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

    async def get_series_neighbors(self, article: Article) -> tuple[Article | None, Article | None]:
        """取同系列内的相邻文章（按 ``(series_order, id)`` 排序）。

        Returns:
            ``(prev, next)``：prev 是系列内更早的一篇，next 是更后的一篇；
            文章不属于任何系列时返回 ``(None, None)``。
            只考虑已发布文章，与全局上下篇同口径。
        """
        if article.series_id is None:
            return None, None
        base = select(Article).where(
            Article.series_id == article.series_id,
            Article.status == ArticleStatus.PUBLISHED,
        )
        prev_stmt = (
            base.where(
                or_(
                    article.series_order > Article.series_order,
                    (article.series_order == Article.series_order) & (Article.id < article.id),
                )
            )
            .order_by(Article.series_order.desc(), Article.id.desc())
            .limit(1)
        )
        next_stmt = (
            base.where(
                or_(
                    article.series_order < Article.series_order,
                    (article.series_order == Article.series_order) & (Article.id > article.id),
                )
            )
            .order_by(Article.series_order.asc(), Article.id.asc())
            .limit(1)
        )
        prev = (await self.session.execute(prev_stmt)).scalars().first()
        nxt = (await self.session.execute(next_stmt)).scalars().first()
        return prev, nxt

    async def list_by_series(
        self, *, series_id: int, statuses: tuple[ArticleStatus, ...]
    ) -> list[Article]:
        """某系列下的文章，按 ``(series_order, id)`` 排序（系列导航的完整列表）。"""
        stmt = (
            select(Article)
            .where(Article.series_id == series_id, Article.status.in_(list(statuses)))
            .order_by(Article.series_order, Article.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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

        ``updated_at`` 必须显式钉住（``updated_at=Article.updated_at``）：
        列上的 ``onupdate`` 对 Core ``update()`` 同样生效，不钉住的话**读一次文章
        就会改一次 updated_at**。后果是后台默认排序 ``sort=updated`` 变成
        「最近被看过的」，热门旧文会压过刚编辑过的；同时 sitemap 的 ``<lastmod>``
        每次访问都变，等于告诉爬虫全站天天在改。计数变化不是内容修改。

        Returns:
            自增后的阅读量。
        """
        await self.session.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(view_count=Article.view_count + 1, updated_at=Article.updated_at)
            .execution_options(synchronize_session=False)
        )
        new_value = (article.view_count or 0) + 1
        set_committed_value(article, "view_count", new_value)
        return new_value

    async def increment_like(self, article: Article) -> int:
        """原子自增点赞数，说明同 ``increment_view``（含 updated_at 必须钉住的理由）。"""
        await self.session.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(like_count=Article.like_count + 1, updated_at=Article.updated_at)
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
