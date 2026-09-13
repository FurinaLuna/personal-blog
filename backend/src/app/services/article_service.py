"""文章服务：可见性判定、状态流转、上下篇与归档。

分类与标签的规则（构造、upsert、slug 唯一化、存在性校验）在 ``TaxonomyService``，
本服务只决定「什么时候需要它们」——领域知识的归属要清晰，多写一遍就是多一处会失修的分歧。
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleSort, ArticleStatus, Tag, User, UserRole
from app.repositories import (
    ArticleFilter,
    ArticleRepository,
    ArticleSorting,
    CategoryRepository,
    TagRepository,
)
from app.schemas.article import (
    ArticleCreate,
    ArticleDetail,
    ArticleNeighbor,
    ArticleSummary,
    ArticleUpdate,
)
from app.schemas.attachment import ImageVariant
from app.schemas.common import Page, PageParams
from app.schemas.site import ArchiveGroup, ArchiveItem
from app.services.attachment_service import AttachmentService
from app.services.series_service import SeriesService
from app.services.taxonomy_service import TaxonomyService
from app.utils.exceptions import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from app.utils.slug import unique_slug
from app.utils.text import estimate_reading_time, strip_markdown

# 列表页可见：归档文章按定义「不再出现在默认列表」，但直达链接仍可访问
LIST_STATUSES: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED,)
# 详情页可达：已发布 + 已归档（归档只是「不做列表曝光」，不是「下架」）
LINK_STATUSES: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED, ArticleStatus.ARCHIVED)
ALL_STATUSES: tuple[ArticleStatus, ...] = (
    ArticleStatus.PUBLISHED,
    ArticleStatus.ARCHIVED,
    ArticleStatus.DRAFT,
)


def visible_link_statuses(viewer: User | None) -> tuple[ArticleStatus, ...]:
    """详情查询用的状态白名单。

    访客只能命中「已发布 / 已归档」；带登录态时才把 draft 纳入查询，
    真正能不能看再由 ``_can_view`` 判断。这样既能让作者预览自己的草稿，
    又不会让访客的查询无谓地扫到草稿分区。
    """
    return ALL_STATUSES if viewer is not None else LINK_STATUSES


MAX_TAGS_PER_ARTICLE = 10
# 允许被显式清空为 NULL 的字段（其余字段收到 None 视为「不修改」）。
# 注意这里用的是关系名 category 而不是 category_id：赋值关系对象才能既改外键
# 又把关系置为已加载，避免后续序列化触发惰性加载。
NULLABLE_FIELDS = {"summary", "cover_image", "category", "series"}


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


class ArticleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.articles = ArticleRepository(session)
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)
        # 分类与标签的规则（upsert / slug / 存在性校验）归 TaxonomyService，
        # 文章服务只负责「什么时候需要它们」——同层服务组合是允许的，
        # 因为 taxonomy_service 不反向依赖 article_service，没有循环。
        self.taxonomy = TaxonomyService(session)
        # 系列的领域规则（唯一性 / slug）归 SeriesService，同上单向组合
        self.series = SeriesService(session)
        # 「封面 URL -> 多尺寸变体」的映射是附件域知识，装配在这里组合使用
        self.attachments = AttachmentService(session)

    # ================================================================ 查询

    @staticmethod
    def _can_view(article: Article, viewer: User | None) -> bool:
        """草稿只有作者本人和站长能看。"""
        if article.status is not ArticleStatus.DRAFT:
            return True
        if viewer is None:
            return False
        return viewer.role is UserRole.ADMIN or article.author_id == viewer.id

    @staticmethod
    def _can_edit(article: Article, user: User) -> bool:
        """作者只能改自己的文章；站长可以改任何人的。"""
        return user.role is UserRole.ADMIN or article.author_id == user.id

    def _assert_can_edit(self, article: Article, user: User) -> None:
        if not self._can_edit(article, user):
            raise PermissionDeniedError("只能操作自己发布的文章")

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
        """前台列表：只返回已发布文章。"""
        flt = build_article_filter(keyword=keyword, category=category, tag=tag, author_id=author_id)
        scoped = ArticleFilter(
            keyword=flt.keyword,
            statuses=LIST_STATUSES,
            category_id=flt.category_id,
            category_slug=flt.category_slug,
            tag_slug=flt.tag_slug,
            author_id=flt.author_id,
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
        total = await self.articles.count(flt)
        if total == 0:
            return Page.build([], 0, page_params.page, page_params.page_size)

        rows = await self.articles.list_paged(
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
            await self.articles.increment_view(article)

        return await self._build_detail(article)

    async def _resolve(self, slug_or_id: str, viewer: User | None) -> Article | None:
        key = slug_or_id.strip()
        if key.isdigit():
            found = await self.articles.get(int(key))
            if found is not None:
                return found
        return await self.articles.get_by_slug(key, statuses=visible_link_statuses(viewer))

    async def _build_detail(self, article: Article) -> ArticleDetail:
        comment_count = await self.articles.get_comment_count(article.id)
        prev, nxt = await self.articles.get_neighbors(article)
        series_prev, series_next = await self.articles.get_series_neighbors(article)
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

    # ================================================================ 写入

    async def create(self, payload: ArticleCreate, author: User) -> ArticleDetail:
        """新建文章。状态为 published 时才写入发布时间。

        这里刻意把 ``author`` / ``category`` / ``tags`` 作为**关系对象**直接传给
        构造函数，而不是只传 ``author_id`` / ``category_id``：新对象在内存里就把
        关系置为「已加载」，后面序列化时才不会为了取作者名去触发惰性加载。
        """
        category = await self.taxonomy.ensure_category(payload.category_id)
        series = await self.series.ensure_series(payload.series_id)
        slug = await self._resolve_slug(payload.slug or payload.title, fallback="article")
        tags = await self._resolve_tags(payload.tags)

        article = await self.articles.create(
            title=payload.title,
            slug=slug,
            summary=payload.summary or strip_markdown(payload.content_md, 200) or None,
            content_md=payload.content_md,
            cover_image=payload.cover_image,
            status=payload.status,
            is_top=payload.is_top,
            allow_comment=payload.allow_comment,
            series_order=payload.series_order,
            reading_time=estimate_reading_time(payload.content_md),
            published_at=datetime.now(UTC) if payload.status is ArticleStatus.PUBLISHED else None,
            author=author,
            category=category,
            series=series,
            tags=tags,
        )
        return await self._build_detail(article)

    async def update(self, article_id: int, payload: ArticleUpdate, user: User) -> ArticleDetail:
        """部分更新。只有请求体里出现过的字段才会被写库。"""
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")
        self._assert_can_edit(article, user)

        data = payload.model_dump(exclude_unset=True)

        if new_slug := data.pop("slug", None):
            # 只有显式传 slug 才改 URL。仅在标题变化时自动改 slug 是个坏主意——
            # 已发布文章的链接一旦变动，外链、搜索引擎收录和 RSS 全部作废。
            data["slug"] = await self._resolve_slug(
                new_slug, fallback="article", exclude_id=article.id
            )

        if "tags" in data:
            tag_names = data.pop("tags") or []
            await self.articles.set_tags(article, await self._resolve_tags(tag_names))

        if data.get("content_md") is not None:
            data["reading_time"] = estimate_reading_time(data["content_md"])
            if not data.get("summary"):
                data["summary"] = strip_markdown(data["content_md"], 200) or None

        if "category_id" in data:
            data["category"] = await self.taxonomy.ensure_category(data.pop("category_id"))

        if "series_id" in data:
            # 显式传 null 表示移出系列（FK 置空）；系列规则由 SeriesService 校验
            data["series"] = await self.series.ensure_series(data.pop("series_id"))

        if data.get("status") is ArticleStatus.PUBLISHED and article.published_at is None:
            # 首次发布时补上发布时间；之后反复切状态不会覆盖原发布时间
            data["published_at"] = datetime.now(UTC)

        # 按约定过滤 None：只允许 NULLABLE_FIELDS 里的字段被清空
        cleaned = {
            key: value for key, value in data.items() if value is not None or key in NULLABLE_FIELDS
        }
        await self.articles.update(article, **cleaned)
        return await self._build_detail(article)

    async def delete(self, article_id: int, user: User) -> None:
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")
        self._assert_can_edit(article, user)
        await self.articles.delete(article)

    async def like(self, article_id: int) -> int:
        """点赞。

        当前是「无身份计数」：不做去重，因为去重需要记录每个访客，代价远大于收益。
        如果将来要做防刷，应该引入 Redis + IP/Cookie 限频，而不是往数据库加表。

        草稿不可见，照旧 404；归档文章详情页仍可达，但不再接受点赞——与
        「浏览计数只累计已发布文章」的口径一致，避免归档页被刷出虚高热度。
        """
        article = await self.articles.get(article_id)
        if article is None or article.status is ArticleStatus.DRAFT:
            raise NotFoundError("文章不存在或尚未发布")
        if article.status is not ArticleStatus.PUBLISHED:
            raise BadRequestError("文章已归档，无法点赞")
        return await self.articles.increment_like(article)

    # ================================================================ 归档与统计

    async def archive(self) -> list[tuple[str, int]]:
        return await self.articles.list_archive(statuses=LIST_STATUSES)

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
        article = await self.articles.get(article_id)
        if article is None or article.status is ArticleStatus.DRAFT:
            raise NotFoundError("文章不存在或尚未发布")
        rows = await self.articles.list_related(article, statuses=LIST_STATUSES, limit=limit)
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
        rows = await self.articles.list_by_month(year_month=year_month, statuses=LIST_STATUSES)
        return [
            ArchiveItem(
                id=item.id,
                title=item.title,
                slug=item.slug,
                published_at=item.published_at or item.created_at,
            )
            for item in rows
        ]

    # ================================================================ 内部工具

    async def _resolve_slug(self, raw: str, *, fallback: str, exclude_id: int | None = None) -> str:
        """生成文章唯一 slug（唯一化规则见 ``app.utils.slug``）。"""
        return await unique_slug(
            raw,
            prefix=fallback,
            exists=partial(self.articles.slug_exists, exclude_id=exclude_id),
        )

    async def _resolve_tags(self, names: list[str]) -> list[Tag]:
        """校验数量上限后，交给标签领域做 upsert。

        「一篇文章最多几个标签」是文章侧规则，所以留在这里；
        「标签怎么建、slug 怎么取」是标签侧规则，委托给 ``TaxonomyService``。
        """
        cleaned = [name.strip() for name in names if name.strip()]
        if len(set(cleaned)) > MAX_TAGS_PER_ARTICLE:
            raise BadRequestError(f"一篇文章最多 {MAX_TAGS_PER_ARTICLE} 个标签")
        return await self.taxonomy.ensure_tags(names)


def _is_year_month(value: str) -> bool:
    """校验 ``YYYY-MM``，顺便确认月份在 1-12 之间（``2026-13`` 要拦住）。"""
    if len(value) != 7 or value[4] != "-":
        return False
    year, month = value[:4], value[5:]
    return year.isdigit() and month.isdigit() and 1 <= int(month) <= 12
