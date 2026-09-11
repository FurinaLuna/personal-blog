"""文章服务：可见性判定、slug/标签解析、状态流转、上下篇与归档。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleStatus, Category, Tag, User, UserRole
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
from app.schemas.common import Page
from app.schemas.site import ArchiveItem
from app.utils.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.utils.text import estimate_reading_time, slugify, strip_markdown

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
NULLABLE_FIELDS = {"summary", "cover_image", "category"}


class ArticleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.articles = ArticleRepository(session)
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)

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
        flt: ArticleFilter,
        sorting: ArticleSorting,
        page: int,
        page_size: int,
    ) -> Page[ArticleSummary]:
        """前台列表：只返回已发布文章。"""
        scoped = ArticleFilter(
            keyword=flt.keyword,
            statuses=LIST_STATUSES,
            category_id=flt.category_id,
            category_slug=flt.category_slug,
            tag_slug=flt.tag_slug,
            author_id=flt.author_id,
        )
        return await self._paginate(scoped, sorting, page, page_size)

    async def list_managed(
        self,
        *,
        flt: ArticleFilter,
        sorting: ArticleSorting,
        page: int,
        page_size: int,
        viewer: User,
    ) -> Page[ArticleSummary]:
        """后台列表：可以是任意状态。

        - 站长：默认看全站所有状态的文章；
        - 作者：默认只看自己的（``scope=mine``），要看全站必须由站长配权限，
          这里采取更保守的策略——作者永远只能看自己的。
        """
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
        return await self._paginate(scoped, sorting, page, page_size, top_first=False)

    async def _paginate(
        self,
        flt: ArticleFilter,
        sorting: ArticleSorting,
        page: int,
        page_size: int,
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
            return Page.build([], 0, page, page_size)

        rows = await self.articles.list_paged(
            flt=flt,
            sorting=sorting,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items = [
            ArticleSummary.model_validate(row.article).model_copy(
                update={"comment_count": row.comment_count}
            )
            for row in rows
        ]
        return Page.build(items, total, page, page_size)

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
        data = ArticleSummary.model_validate(article).model_dump()
        data["comment_count"] = comment_count
        return ArticleDetail(
            **data,
            content_md=article.content_md,
            prev=self._neighbor(prev),
            next=self._neighbor(nxt),
        )

    @staticmethod
    def _neighbor(article: Article | None) -> ArticleNeighbor | None:
        if article is None:
            return None
        return ArticleNeighbor(id=article.id, title=article.title, slug=article.slug)

    # ================================================================ 写入

    async def create(self, payload: ArticleCreate, author: User) -> ArticleDetail:
        """新建文章。状态为 published 时才写入发布时间。

        这里刻意把 ``author`` / ``category`` / ``tags`` 作为**关系对象**直接传给
        构造函数，而不是只传 ``author_id`` / ``category_id``：新对象在内存里就把
        关系置为「已加载」，后面序列化时才不会为了取作者名去触发惰性加载。
        """
        category = await self._resolve_category(payload.category_id)
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
            reading_time=estimate_reading_time(payload.content_md),
            published_at=datetime.now(UTC) if payload.status is ArticleStatus.PUBLISHED else None,
            author=author,
            category=category,
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
            data["category"] = await self._resolve_category(data.pop("category_id"))

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
        """
        article = await self.articles.get(article_id)
        if article is None or article.status is ArticleStatus.DRAFT:
            raise NotFoundError("文章不存在或尚未发布")
        return await self.articles.increment_like(article)

    # ================================================================ 归档与统计

    async def archive(self) -> list[tuple[str, int]]:
        return await self.articles.list_archive(statuses=LIST_STATUSES)

    async def related(self, article_id: int, *, limit: int = 5) -> list[ArticleSummary]:
        """相关文章：同分类或共享标签，按发布时间倒序。

        没有分类也没有标签的文章直接返回空列表——这时候「相关」无从谈起，
        硬塞最新文章只会稀释详情页的语义。
        """
        article = await self.articles.get(article_id)
        if article is None or article.status is ArticleStatus.DRAFT:
            raise NotFoundError("文章不存在或尚未发布")
        rows = await self.articles.list_related(article, statuses=LIST_STATUSES, limit=limit)
        return [ArticleSummary.model_validate(item) for item in rows]

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
        """生成唯一 slug：重名时依次尝试 ``-2`` / ``-3`` …"""
        base = slugify(raw, fallback_prefix=fallback)
        candidate = base
        suffix = 2
        while await self.articles.slug_exists(candidate, exclude_id=exclude_id):
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 200:  # pragma: no cover - 防御性上限
                raise ConflictError("无法生成唯一 slug，请手动指定")
        return candidate

    async def _resolve_tags(self, names: list[str]) -> list[Tag]:
        """按名称取标签，不存在的即时创建（这也是「自由打标签」体验的实现方式）。"""
        cleaned: list[str] = []
        for name in names:
            trimmed = name.strip()
            if trimmed and trimmed not in cleaned:
                cleaned.append(trimmed)
        if not cleaned:
            return []
        if len(cleaned) > MAX_TAGS_PER_ARTICLE:
            raise BadRequestError(f"一篇文章最多 {MAX_TAGS_PER_ARTICLE} 个标签")

        existing = {tag.name: tag for tag in await self.tags.get_by_names(cleaned)}
        result: list[Tag] = []
        for name in cleaned:
            tag = existing.get(name)
            if tag is None:
                slug = await self._resolve_tag_slug(name)
                tag = await self.tags.create(name=name, slug=slug)
                existing[name] = tag
            result.append(tag)
        return result

    async def _resolve_tag_slug(self, name: str) -> str:
        base = slugify(name, fallback_prefix="tag")
        candidate = base
        suffix = 2
        while await self.tags.slug_exists(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    async def _resolve_category(self, category_id: int | None) -> Category | None:
        """取分类对象（而不是只校验存在性）。

        Returns:
            ``None`` 表示不分类；找不到对应分类时抛 400。

        Raises:
            BadRequestError: ``category_id`` 指向不存在的分类。
        """
        if category_id is None:
            return None
        category = await self.categories.get(category_id)
        if category is None:
            raise BadRequestError(f"分类 {category_id} 不存在")
        return category


def _is_year_month(value: str) -> bool:
    """校验 ``YYYY-MM``，顺便确认月份在 1-12 之间（``2026-13`` 要拦住）。"""
    if len(value) != 7 or value[4] != "-":
        return False
    year, month = value[:4], value[5:]
    return year.isdigit() and month.isdigit() and 1 <= int(month) <= 12
