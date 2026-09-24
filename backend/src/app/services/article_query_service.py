"""文章查询服务（读路径）：列表、检索、详情、相关文章、归档。

按读写方向从 ``article_service.py`` 拆分而来：方法体逐字搬移，只把仓储引用
``self.articles`` 改名为 ``self.queries``（指向只读的 ``ArticleQueryRepository``）。
写路径（新建 / 修改 / 删除 / 点赞）见 ``article_command_service``。

分类与标签的规则（构造、upsert、slug 唯一化、存在性校验）在 ``TaxonomyService``，
本服务只决定「什么时候需要它们」——领域知识的归属要清晰，多写一遍就是多一处会失修的分歧。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleSort, ArticleStatus, User, UserRole
from app.repositories import (
    ArticleFilter,
    ArticleQueryRepository,
    ArticleSorting,
    ArticleWriteRepository,
)
from app.schemas.article import ArticleDetail, ArticleNeighbor, ArticleSummary
from app.schemas.attachment import ImageVariant
from app.schemas.common import Page, PageParams
from app.schemas.site import ArchiveGroup, ArchiveItem
from app.services.article_service import _is_year_month
from app.services.attachment_service import AttachmentService
from app.utils.exceptions import BadRequestError, NotFoundError
from app.utils.text import build_snippet

# 列表页可见：归档文章按定义「不再出现在默认列表」，但直达链接仍可访问
LIST_STATUSES: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED,)
# 详情页可达：已发布 + 已归档（归档只是「不做列表曝光」，不是「下架」）
LINK_STATUSES: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED, ArticleStatus.ARCHIVED)
ALL_STATUSES: tuple[ArticleStatus, ...] = (
    ArticleStatus.PUBLISHED,
    ArticleStatus.ARCHIVED,
    ArticleStatus.DRAFT,
)


# trigram 分词器按三字符滑窗建索引，查询词短于 3 个字符时 FTS 一律零结果。
# 所以短查询必须退回 LIKE，否则「博客」「数据」这类常见中文词会搜不到。
FTS_MIN_QUERY_LENGTH = 3


def is_publicly_visible(article: Article) -> bool:
    """这篇文章此刻是否对**公众**可见（不需要登录就能看）。

    三态语义：

    - ``draft``：不可见；
    - ``published``：还要看 ``published_at`` 到了没有——排在未来的文章
      就是**定时发布**，到点前与草稿同等对待；
    - ``archived``：可见。归档只是「不做列表曝光」，直达链接仍然有效，
      这是项目一开始就定下的语义，别在这里改掉。

    抽成模块级函数是因为评论接口也要用同一个口径——两处各写一遍，
    迟早会出现「文章 404 但评论区能打开」这种不一致（草稿评论泄露
    就是这么来的）。
    """
    if article.status is ArticleStatus.DRAFT:
        return False
    if article.status is ArticleStatus.PUBLISHED:
        return article.published_at is not None and article.published_at <= datetime.now(UTC)
    return True


def visible_link_statuses(viewer: User | None) -> tuple[ArticleStatus, ...]:
    """详情查询用的状态白名单。

    访客只能命中「已发布 / 已归档」；带登录态时才把 draft 纳入查询，
    真正能不能看再由 ``_can_view`` 判断。这样既能让作者预览自己的草稿，
    又不会让访客的查询无谓地扫到草稿分区。
    """
    return ALL_STATUSES if viewer is not None else LINK_STATUSES


def build_article_filter(
    *,
    keyword: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    author_id: int | None = None,
    article_status: ArticleStatus | None = None,
) -> ArticleFilter:
    """把查询参数拼成仓储的筛选对象。

    ``category`` 同时接受 slug 和数字 id，前端就不用关心自己手上拿到的是哪种，
    少一次「到底该传什么」的沟通成本。

    定义在服务层而不是路由层：「分类能用 slug 或 id 查询」是**领域查询语义**，
    路由层不该知道仓储筛选对象长什么样（也据此不 import repositories）。
    """
    category_id: int | None = None
    category_slug: str | None = None
    if category:
        category_slug = category if not category.isdigit() else None
        category_id = int(category) if category.isdigit() else None
    statuses: tuple[ArticleStatus, ...] = (article_status,) if article_status else ()
    return ArticleFilter(
        keyword=keyword,
        statuses=statuses,
        category_id=category_id,
        category_slug=category_slug,
        tag_slug=tag,
        author_id=author_id,
    )


class ArticleQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # 只读路径一律走只读仓储：列表筛选 / 检索 / 邻居 / 归档 / 统计
        self.queries = ArticleQueryRepository(session)
        # 「封面 URL -> 多尺寸变体」的映射是附件域知识，装配在这里组合使用
        self.attachments = AttachmentService(session)
        # 详情页的浏览计数自增是读路径里唯一的一次写（见 get_detail），
        # 语句本身按方向归 ArticleWriteRepository，所以这里也持有它。
        self.writes = ArticleWriteRepository(session)

    # ================================================================ 查询

    @staticmethod
    def _can_view(article: Article, viewer: User | None) -> bool:
        """对公众可见的文章谁都能看；草稿与「未到发布时间的定时文章」只有作者本人和站长能看。"""
        if is_publicly_visible(article):
            return True
        if viewer is None:
            return False
        return viewer.role is UserRole.ADMIN or article.author_id == viewer.id

    async def list_public(
        self,
        *,
        page_params: PageParams,
        keyword: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        author_id: int | None = None,
        sort: ArticleSort = ArticleSort.LATEST,
    ) -> Page[ArticleSummary]:
        """前台列表：只返回已发布文章，且**已到发布时间**。

        ``visible_at`` 这个条件就是定时发布的全部实现：排在未来时刻的文章
        不会出现在列表里，到点之后自然浮现——不需要定时任务去改状态。
        """
        flt = build_article_filter(keyword=keyword, category=category, tag=tag, author_id=author_id)
        scoped = ArticleFilter(
            keyword=flt.keyword,
            statuses=LIST_STATUSES,
            category_id=flt.category_id,
            category_slug=flt.category_slug,
            tag_slug=flt.tag_slug,
            author_id=flt.author_id,
            visible_at=datetime.now(UTC),
        )
        return await self._paginate(scoped, ArticleSorting(sort=sort), page_params)

    async def list_managed(
        self,
        *,
        page_params: PageParams,
        viewer: User,
        keyword: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        author_id: int | None = None,
        article_status: ArticleStatus | None = None,
        sort: ArticleSort = ArticleSort.UPDATED,
    ) -> Page[ArticleSummary]:
        """后台列表：可以是任意状态。

        - 站长：默认看全站所有状态的文章；
        - 作者：默认只看自己的（``scope=mine``），要看全站必须由站长配权限，
          这里采取更保守的策略——作者永远只能看自己的。
        """
        flt = build_article_filter(
            keyword=keyword,
            category=category,
            tag=tag,
            author_id=author_id,
            article_status=article_status,
        )
        statuses = flt.statuses or ALL_STATUSES
        if viewer.role is UserRole.ADMIN:
            scoped = ArticleFilter(
                keyword=flt.keyword,
                statuses=statuses,
                category_id=flt.category_id,
                tag_slug=flt.tag_slug,
                author_id=flt.author_id,
            )
        else:
            scoped = ArticleFilter(
                keyword=flt.keyword,
                statuses=statuses,
                category_id=flt.category_id,
                tag_slug=flt.tag_slug,
                author_id=viewer.id,
            )
        return await self._paginate(scoped, ArticleSorting(sort=sort), page_params, top_first=False)

    async def _paginate(
        self,
        flt: ArticleFilter,
        sorting: ArticleSorting,
        page_params: PageParams,
        *,
        top_first: bool = True,
    ) -> Page[ArticleSummary]:
        """先查总数再查当页。

        注意顺序：先 ``count`` 再取数据会让「页码超出范围」时也返回正确 total，
        前端能据此把用户弹回最后一页，而不是显示成「没有文章」。
        """
        sorting.top_first = top_first
        total = await self.queries.count(flt)
        if total == 0:
            return Page.build([], 0, page_params.page, page_params.page_size)

        rows = await self.queries.list_paged(
            flt=flt,
            sorting=sorting,
            offset=page_params.offset,
            limit=page_params.limit,
        )
        items = [
            ArticleSummary.model_validate(row.article).model_copy(
                update={"comment_count": row.comment_count}
            )
            for row in rows
        ]
        await self._fill_cover_variants(items)
        return Page.build(items, total, page_params.page, page_params.page_size)

    async def search(self, *, keyword: str, page_params: PageParams) -> Page[ArticleSummary]:
        """站内搜索：按相关度返回命中文章（含命中片段）。

        ## 两条检索路径，**同一套排序语义**

        - 查询词 ≥ ``FTS_MIN_QUERY_LENGTH`` 个字符时走 SQLite FTS5（bm25 排序）。
        - 更短的查询退回 LIKE。原因是 trigram 分词器按三字符滑窗建索引，
          **两字查询一律零结果**——实测「博客」「数据」在 FTS 下都搜不到，
          而中文里两字词极其常见。数据库不是 SQLite 时同样走 LIKE。

        关键是两条路径**排序逻辑必须一致**：都是「相关度优先（标题 > 摘要 >
        正文），同等相关度下新文优先」，且**都不看置顶**。这一点曾经做错过——
        短查询走的是列表接口的 `sort=LATEST`，而它置顶优先，于是搜「数据」
        第一篇是只在正文里顺带提了一句的置顶文章，搜「数据层」第一篇却是
        标题命中。同一个搜索框，排序随字数变化。

        ## 为什么不在这里做「按相关度整体排序后再切页」

        排序完全交给 SQL。曾经为了省一次查询，把 ``total`` 取成
        「这一页取回了几条」，结果命中 12 条时 total 显示 10，
        前端据此判断「没有下一页」，第 11 条之后**永远翻不到**。
        总数必须是真实的 COUNT，翻页必须走 LIMIT/OFFSET。
        """
        query = keyword.strip()
        if not query:
            return Page.build([], 0, page_params.page, page_params.page_size)

        now = datetime.now(UTC)
        # 先探测索引是否存在：``articles_fts`` 是虚拟表，create_all 不会建它
        # （只有跑过迁移才有），缺了就安静退回 LIKE 而不是抛 500
        use_fts = (
            len(query) >= FTS_MIN_QUERY_LENGTH and await self.queries.detect_full_text_search()
        )

        if use_fts:
            ids, total = await self.queries.search_fulltext(
                query,
                limit=page_params.limit,
                offset=page_params.offset,
                statuses=LIST_STATUSES,
                visible_at=now,
                now=now,
            )
        else:
            ids, total = await self.queries.search_keyword(
                query,
                limit=page_params.limit,
                offset=page_params.offset,
                statuses=LIST_STATUSES,
                visible_at=now,
                now=now,
            )

        rows = await self.queries.list_by_ids_ordered(ids)
        items = [
            ArticleSummary.model_validate(row.article).model_copy(
                update={
                    "comment_count": row.comment_count,
                    # 命中片段：回答「这条为什么会出现」。正文命中的时候标题和摘要
                    # 里可能一个字都没有，没有片段用户只能靠猜。
                    "snippet": build_snippet(row.article, query),
                }
            )
            for row in rows
        ]
        await self._fill_cover_variants(items)
        return Page.build(items, total, page_params.page, page_params.page_size)

    async def get_detail(
        self, slug_or_id: str, viewer: User | None, *, count_view: bool = True
    ) -> ArticleDetail:
        """按 slug 或 id 取详情。

        Raises:
            NotFoundError: 不存在，或存在但是无权查看的草稿。
                草稿统一返回 404 而不是 403——403 等于告诉别人「这里确实有一篇
                没发布的文章」，属于信息泄露。
        """
        article = await self._resolve(slug_or_id, viewer)
        if article is None or not self._can_view(article, viewer):
            raise NotFoundError("文章不存在或尚未发布")

        if count_view and article.status is ArticleStatus.PUBLISHED:
            # 自增后对象上的 view_count 已被同步为新值，响应里就是最新的
            await self.writes.increment_view(article)

        return await self._build_detail(article)

    async def _resolve(self, slug_or_id: str, viewer: User | None) -> Article | None:
        key = slug_or_id.strip()
        if key.isdigit():
            found = await self.queries.get(int(key))
            if found is not None:
                return found
        return await self.queries.get_by_slug(key, statuses=visible_link_statuses(viewer))

    async def _build_detail(self, article: Article) -> ArticleDetail:
        comment_count = await self.queries.get_comment_count(article.id)
        prev, nxt = await self.queries.get_neighbors(article)
        series_prev, series_next = await self.queries.get_series_neighbors(article)
        data = ArticleSummary.model_validate(article).model_dump()
        data["comment_count"] = comment_count
        data["cover_variants"] = await self._cover_variants(article.cover_image)
        return ArticleDetail(
            **data,
            content_md=article.content_md,
            prev=self._neighbor(prev),
            next=self._neighbor(nxt),
            series_prev=self._neighbor(series_prev),
            series_next=self._neighbor(series_next),
        )

    @staticmethod
    def _neighbor(article: Article | None) -> ArticleNeighbor | None:
        if article is None:
            return None
        return ArticleNeighbor(id=article.id, title=article.title, slug=article.slug)

    async def _fill_cover_variants(self, items: list[ArticleSummary]) -> None:
        """原位补齐列表项的封面变体（srcset 数据源）。"""
        covers = [item.cover_image for item in items if item.cover_image]
        if not covers:
            return
        variant_map = await self.attachments.variant_map_by_urls(covers)
        for item in items:
            if item.cover_image:
                item.cover_variants = variant_map.get(item.cover_image, [])

    async def _cover_variants(self, cover_image: str | None) -> list[ImageVariant]:
        if not cover_image:
            return []
        variant_map = await self.attachments.variant_map_by_urls([cover_image])
        return variant_map.get(cover_image, [])

    # ================================================================ 归档与统计

    async def archive(self) -> list[tuple[str, int]]:
        return await self.queries.list_archive(statuses=LIST_STATUSES)

    async def archive_groups(self, *, limit_per_month: int) -> list[ArchiveGroup]:
        """按月归档的完整编排：月份列表 → 逐月取条目 → 截断拼装。

        「逐月再查一次列表」是 N+1 的**有意选择**：月份总数通常个位数到十几，
        每月条目有 ``list_by_month`` 自己的 limit 保护，一次 JOIN 聚合反而会把
        SQL 写复杂。编排逻辑放服务层，路由只负责 HTTP 参数（limit 校验）。
        """
        groups: list[ArchiveGroup] = []
        for year_month, count in await self.archive():
            # 注意括号：await 的优先级低于下标，写成 await f()[..] 会对协程取下标而报
            # TypeError: 'coroutine' object is not subscriptable
            items = await self.list_by_month(year_month)
            groups.append(
                ArchiveGroup(year_month=year_month, count=count, items=items[:limit_per_month])
            )
        return groups

    async def related(self, article_id: int, *, limit: int = 5) -> list[ArticleSummary]:
        """相关文章：同分类或共享标签，按发布时间倒序。

        没有分类也没有标签的文章直接返回空列表——这时候「相关」无从谈起，
        硬塞最新文章只会稀释详情页的语义。
        """
        article = await self.queries.get(article_id)
        if article is None or article.status is ArticleStatus.DRAFT:
            raise NotFoundError("文章不存在或尚未发布")
        rows = await self.queries.list_related(article, statuses=LIST_STATUSES, limit=limit)
        items = [ArticleSummary.model_validate(item) for item in rows]
        await self._fill_cover_variants(items)
        return items

    async def list_by_month(self, year_month: str) -> list[ArchiveItem]:
        """取某个月份的文章条目。

        归档只需要标题和链接，所以这里返回轻量的 ``ArchiveItem`` 而不是完整的
        ``ArticleSummary`` —— 归档页可能一次展开十几个年份，字段越少越快。
        """
        if not _is_year_month(year_month):
            raise BadRequestError("月份格式应为 YYYY-MM")
        rows = await self.queries.list_by_month(year_month=year_month, statuses=LIST_STATUSES)
        return [
            ArchiveItem(
                id=item.id,
                title=item.title,
                slug=item.slug,
                published_at=item.published_at or item.created_at,
            )
            for item in rows
        ]
