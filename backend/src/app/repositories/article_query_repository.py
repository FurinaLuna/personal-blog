"""文章只读仓储：列表筛选/排序、检索、上下篇、归档、站点统计。

读写拆分自 ``article_repository.py``：本文件承载全部只读方法与只读的表达式
构造器，方法体逐字搬移（只改了类名）。写入方法（``increment_view`` /
``increment_like`` / ``set_tags``）见 ``article_write_repository``；
``ArticleFilter`` / ``ArticleSorting`` / ``ArticleListRow`` 三个值对象随只读
查询一起放在这里，并照旧从 ``app.repositories`` 导出。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, String, case, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.models import Article, ArticleSort, ArticleStatus, Category, Comment, Tag
from app.repositories.base import BaseRepository

# 「有效发布时间」：已发布用 published_at，草稿回退到 created_at。
# 统一用它排序，就不用到处处理 NULL 在 SQLite / PostgreSQL 中排序位置不一致的坑。
_EFFECTIVE_DATE = func.coalesce(Article.published_at, Article.created_at)


# LIKE 的转义符。抽成常量是刻意的：内联写进 f-string 时，
# ESCAPE '{_LIKE_ESCAPE}' 里的 \' 会被 Python 当成转义引号、反斜杠被吞掉，
# SQL 里剩下的引号会把后续内容当成字符串字面量，报
# "ESCAPE expression must be a single character" —— 排查起来很费时间。
_LIKE_ESCAPE = "\\"


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


class ArticleQueryRepository(BaseRepository[Article]):
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

    # ------------------------------------------------------------------ 搜索
    #
    # 两条检索路径共用同一套**排序语义**：相关度越高越靠前，其次越新越靠前。
    # 分数统一采用「越小越好」的约定（与 bm25 一致），这样两条路径的
    # ORDER BY 方向相同，也不会再出现「换个字数排序就变了」。

    @staticmethod
    def _like_score_expr(pattern: str) -> ColumnElement[float]:
        """LIKE 路径的相关度分：标题 > 摘要 > 正文。

        用**负数**（越小越好）与 bm25 的方向对齐，避免两条路径一个升序
        一个降序、read 的时候要看半天才敢确认没写反。

        档位取**十的幂**（-100 / -10 / -1）而不是 -10/-5/-1，是因为后面还要
        除以时间衰减因子：档距必须**大于最大衰减倍数**，否则衰减会把标题命中
        降级成摘要档。这个坑真实踩到过——-10 / 2.0 恰好等于 -5，
        于是六年前的标题命中与最近的摘要命中并列，再由时间分胜负。
        """
        return (
            case((Article.title.ilike(pattern, escape="\\"), -100.0), else_=0.0)
            + case((Article.summary.ilike(pattern, escape="\\"), -10.0), else_=0.0)
            + case((Article.content_md.ilike(pattern, escape="\\"), -1.0), else_=0.0)
        )

    @staticmethod
    def _recency_divisor_expr(*, now: datetime) -> ColumnElement[float]:
        """按年龄放大分数的**除数**（时间衰减）。

        必须用**除法**而不是减法。两个原因，都是实测出来的：

        1. **bm25 的量纲随语料规模与词频变化几个数量级**：实测同一套代码在
           3 篇语料下稀有词约 -1.0，202 篇下约 -8.8，而高频词只有 -0.000001。
           任何固定的加减值都会在某个区间里彻底压倒相关度——语料小的时候
           加 0.5 就等于「完全按时间排」。
        2. 分数是「越小越好」，乘以大于 1 的因子反而会让旧文**更靠前**。
           除以因子才是降权。

        分档而不是连续函数：跨方言（SQLite / PostgreSQL）能写出完全一致的
        表达，且参数肉眼可核对。
        """
        one_year_ago = now - timedelta(days=365)
        three_years_ago = now - timedelta(days=365 * 3)
        return case(
            (Article.published_at >= one_year_ago, 1.0),
            (Article.published_at >= three_years_ago, 1.5),
            else_=2.0,
        )

    async def search_fulltext(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
        statuses: tuple[ArticleStatus, ...] = (),
        visible_at: datetime | None = None,
        now: datetime,
    ) -> tuple[list[int], int]:
        """FTS5 检索，返回 ``(本页文章 id, 命中总数)``。

        ``bm25()`` 返回**越小越相关**的负数，所以是 ``ORDER BY score``（升序）。

        ## 排序：先分层，再精排，最后看时间

        1. **命中位置分层**（标题 > 摘要 > 正文）——语料无关的硬规则；
        2. bm25 层内精排；
        3. 新文优先。

        分层必须放在 bm25 前面。实测 bm25 的量纲随语料规模剧烈变化：3 篇语料下
        稀有词约 -1.0，高频词只有 -1e-6。小语料或常见词时 bm25 几乎没有区分力，
        单靠它排序会退化成纯时间序——标题命中反而输给正文命中
        （这个用例在 tests/test_search.py::TestRecencyDecay 里）。

        时间衰减用 ``/ 年龄因子``：同等相关度下新文优先，而旧文的强相关匹配
        依然能压过新文的弱匹配——除法的效果与分数大小成比例，这正是我们想要的。

        可见性条件必须写进**同一条 SQL**：先按相关度取前 N 条、再在外面过滤，
        会出现「第一页只剩两条」这种分页数量对不上的问题。

        短查询（<3 字符）不走这里——trigram 分词要求查询词不少于 3 个字符，
        调用方需自行判断，见 ``ArticleQueryService.search``。
        """
        if not query.strip():
            return [], 0

        params = {
            "query": self._fts_query(query),
            # 状态与可见性条件在 count 与分页两条 SQL 里必须完全一致，
            # 否则会出现「总数说有 12 条，翻到第 3 页却是空的」
            "now": now,
            "one_year_ago": now - timedelta(days=365),
            "three_years_ago": now - timedelta(days=365 * 3),
            "like_pattern": _like_pattern(query),
            "limit": limit,
            "offset": offset,
        }
        where = """
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.rowid
            WHERE articles_fts MATCH :query
              AND a.status = 'published'
              AND a.published_at IS NOT NULL
              AND a.published_at <= :now
        """
        # 状态白名单用固定字面量而不是参数化的 IN：只有「前台已发布」一种用法，
        # 而多一个 expanding 参数会让下面两条 SQL 的绑定复杂一倍。
        if statuses and tuple(statuses) != (ArticleStatus.PUBLISHED,):
            listed = ", ".join(f"'{s.value if hasattr(s, 'value') else s}'" for s in statuses)
            where = where.replace("a.status = 'published'", f"a.status IN ({listed})")

        divisor = """
            CASE
                WHEN a.published_at >= :one_year_ago THEN 1.0
                WHEN a.published_at >= :three_years_ago THEN 1.5
                ELSE 2.0
            END
        """

        count_result = await self.session.execute(text(f"SELECT count(*) AS n {where}"), params)
        total = int(count_result.scalar_one())

        page_result = await self.session.execute(
            text(
                f"""
                SELECT a.id AS id
                {where}
                ORDER BY
                    -- 主排序键是「命中位置」的分层，语料无关。档位取十的幂，
                    -- 保证任何倍数的年龄衰减都跨不过档位（见 _like_score_expr）：
                    -- 标题命中 > 摘要命中 > 正文命中。实测 bm25 在小语料下
                    -- IDF 趋近 0（高频词只有 -1e-6），此时它没有区分力，
                    -- 单靠它排序会退化成纯时间序——标题命中反而输给正文命中。
                    (
                        CASE WHEN a.title LIKE :like_pattern ESCAPE '{_LIKE_ESCAPE}'
                             THEN -100.0 ELSE 0.0 END
                      + CASE WHEN a.summary LIKE :like_pattern ESCAPE '{_LIKE_ESCAPE}'
                             THEN -10.0 ELSE 0.0 END
                      + CASE WHEN a.content_md LIKE :like_pattern ESCAPE '{_LIKE_ESCAPE}'
                             THEN -1.0 ELSE 0.0 END
                    ) / ({divisor}),
                    -- 层内用 bm25 精排（词频、文档长度归一化）。
                    -- 语料够大时它才有意义，所以放在第二键。
                    bm25(articles_fts, 10.0, 5.0, 1.0) / ({divisor}),
                    a.published_at DESC, a.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [int(row.id) for row in page_result.all()], total

    async def search_keyword(
        self,
        keyword: str,
        *,
        limit: int,
        offset: int = 0,
        statuses: tuple[ArticleStatus, ...] = (),
        visible_at: datetime | None = None,
        now: datetime,
    ) -> tuple[list[int], int]:
        """LIKE 检索，返回 ``(本页文章 id, 命中总数)``。

        给 trigram 覆盖不到的两字 / 单字查询兜底，也是非 SQLite 数据库上
        唯一的检索路径。

        **不带 is_top 权重**：搜索场景里「置顶」没有任何意义。之前走
        ``list_public(sort=LATEST)`` 时置顶优先会生效，于是搜「数据」
        第一篇是那篇只在正文里顺带提了一句的置顶文章——这是真实踩到的
        不一致（见 tests/test_search.py::TestRankingConsistency）。
        """
        pattern = _like_pattern(keyword)
        score = self._like_score_expr(pattern)
        divisor = self._recency_divisor_expr(now=now)

        conditions = [
            Article.title.ilike(pattern, escape="\\")
            | Article.summary.ilike(pattern, escape="\\")
            | Article.content_md.ilike(pattern, escape="\\")
        ]
        if statuses:
            conditions.append(Article.status.in_(list(statuses)))
        if visible_at is not None:
            conditions.append(Article.published_at.is_not(None))
            conditions.append(Article.published_at <= visible_at)

        count_stmt = select(func.count()).select_from(Article).where(*conditions)
        total = int((await self.session.execute(count_stmt)).scalar_one())
        if total == 0:
            return [], 0

        page_stmt = (
            select(Article.id)
            .where(*conditions)
            .order_by(
                (score / divisor).asc(),
                _EFFECTIVE_DATE.desc(),
                Article.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = await self.session.execute(page_stmt)
        return [int(row.id) for row in rows.all()], total

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
