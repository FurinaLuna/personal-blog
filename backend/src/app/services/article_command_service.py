"""文章写入服务（写路径）：新建、修改、删除、点赞。

按读写方向从 ``article_service.py`` 拆分而来：方法体逐字搬移，写侧仓储引用
``self.articles`` 仍指向写仓储（``ArticleWriteRepository``）；只读查询
（``slug_exists``）与详情装配转交只读侧，见 ``article_query_service``。

分类与标签的规则（构造、upsert、slug 唯一化、存在性校验）在 ``TaxonomyService``，
本服务只决定「什么时候需要它们」——领域知识的归属要清晰，多写一遍就是多一处会失修的分歧。
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleStatus, Tag, User, UserRole
from app.repositories import ArticleQueryRepository, ArticleWriteRepository
from app.schemas.article import ArticleCreate, ArticleDetail, ArticleUpdate
from app.services.article_query_service import ArticleQueryService
from app.services.revision_service import RevisionService
from app.services.series_service import SeriesService
from app.services.taxonomy_service import TaxonomyService
from app.utils.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.utils.slug import unique_slug
from app.utils.text import estimate_reading_time, strip_markdown

MAX_TAGS_PER_ARTICLE = 10
# 允许被显式清空为 NULL 的字段（其余字段收到 None 视为「不修改」）。
# 注意这里用的是关系名 category 而不是 category_id：赋值关系对象才能既改外键
# 又把关系置为已加载，避免后续序列化触发惰性加载。
NULLABLE_FIELDS = {"summary", "cover_image", "category", "series"}


def _normalize_published_at(value: datetime) -> datetime:
    """把客户端传来的时间归一成「UTC 带时区」。

    接口可能收到带偏移的时间（``+08:00``）或不带时区的裸时间。前者要换算，
    后者按 UTC 解释——混着存会让「谁先谁后」的比较结果取决于服务器时区，
    是最难查的一类 bug。
    """
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _resolve_published_at(status: ArticleStatus, requested: datetime | None) -> datetime | None:
    """决定**新建**文章落库的 ``published_at``。

    规则：
    - 显式传了时间就以它为准（**未来时间即定时发布**）；
    - 没传但状态是「已发布」→ 立刻发布，取当前时间；
    - 其余情况（草稿 / 归档）→ 不设发布时间。
    """
    if requested is not None:
        return _normalize_published_at(requested)
    if status is ArticleStatus.PUBLISHED:
        return datetime.now(UTC)
    return None


class ArticleCommandService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # 写路径一律走写仓储：新增 / 修改 / 删除（通用 CRUD）+ 计数自增 + 标签替换
        self.articles = ArticleWriteRepository(session)
        # slug 唯一性校验（slug_exists）是只读查询，按方向归只读仓储
        self.queries = ArticleQueryRepository(session)
        # 分类与标签的规则（upsert / slug / 存在性校验）归 TaxonomyService，
        # 文章服务只负责「什么时候需要它们」——同层服务组合是允许的，
        # 因为 taxonomy_service 不反向依赖 article_service，没有循环。
        self.taxonomy = TaxonomyService(session)
        # 系列的领域规则（唯一性 / slug）归 SeriesService，同上单向组合
        self.series = SeriesService(session)
        # 版本历史：只在内容真的变化时留痕，判定逻辑在 RevisionService 里
        self.revisions = RevisionService(session)
        # 详情装配（评论数 / 上下篇 / 封面变体）是只读逻辑，按方向归 ArticleQueryService；
        # 写路径的响应体必须是同一个 ArticleDetail（否则「刚写完看到的」与「重新打开」
        # 会漂移），所以组合只读服务、复用它唯一的详情私有助手 _build_detail，
        # 而不是把装配抄成第二份。这是两个方向唯一共享的私有工具。
        self.details = ArticleQueryService(session)

    # 「_can_edit 放哪一侧」的决定：**留在写侧原地**。它只有写路径的
    # ``_assert_can_edit``（update / delete）调用，读路径用的是 ``_can_view``，
    # 「两侧都要用」的前提不成立——所以既不需要从 query 侧 import，也不必复制
    # 第二份静态方法。
    @staticmethod
    def _can_edit(article: Article, user: User) -> bool:
        """作者只能改自己的文章；站长可以改任何人的。"""
        return user.role is UserRole.ADMIN or article.author_id == user.id

    def _assert_can_edit(self, article: Article, user: User) -> None:
        if not self._can_edit(article, user):
            raise PermissionDeniedError("只能操作自己发布的文章")

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
            published_at=_resolve_published_at(payload.status, payload.published_at),
            author=author,
            category=category,
            series=series,
            tags=tags,
        )
        return await self.details._build_detail(article)

    async def update(self, article_id: int, payload: ArticleUpdate, user: User) -> ArticleDetail:
        """部分更新。只有请求体里出现过的字段才会被写库。"""
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")
        self._assert_can_edit(article, user)

        data = payload.model_dump(exclude_unset=True)

        # 版本快照必须在**改之前**做，而且要在把 data 应用到对象上之前。
        # 判定用的是「最终会写进去的值」：没传的字段沿用当前值，
        # 所以这里先把三要素算出来，而不是只看 data 里有没有出现。
        await self.revisions.snapshot_if_content_changed(
            article,
            new_title=data.get("title", article.title),
            new_summary=data.get("summary", article.summary),
            new_content_md=data.get("content_md", article.content_md),
            author=user,
        )

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

        # ``published_at`` 的处理有一条不变量：**published 状态不能没有发布时间**。
        #
        # 违反它的后果很隐蔽：``is_publicly_visible()`` 对 published 且
        # ``published_at is None`` 返回 False，于是文章对**公众彻底消失**
        # （详情 404、不进列表、不接受评论），而作者自己因为
        # ``_can_view`` 放行仍然看得到 —— 编辑器里一切正常，谁都发现不了。
        #
        # 这里踩过一次：写成 ``if "published_at" in data: ... elif ...``，
        # 而前端每次保存都会带上 ``published_at: form.published_at || null``，
        # 于是 if 分支永远命中、把 NULL 写回去，elif 成了死代码。
        # 「新建草稿 → 点发布」走的正是这条路径，等于**所有经编辑器发布的文章
        # 都对访客不可见**。
        status_after = data.get("status", article.status)

        if data.get("published_at") is not None:
            # 显式给了时间（含未来的定时发布）：以它为准
            data["published_at"] = _normalize_published_at(data["published_at"])
        else:
            # 没传，或显式传了 null。两种情况都**不能直接写 None**：
            # 已有的发布时间要保留（重发一次不该让旧文掉出列表），
            # 缺了则按最终状态补齐。
            data.pop("published_at", None)
            if status_after is ArticleStatus.PUBLISHED and article.published_at is None:
                data["published_at"] = datetime.now(UTC)

        # 按约定过滤 None：只允许 NULLABLE_FIELDS 里的字段被清空
        cleaned = {
            key: value for key, value in data.items() if value is not None or key in NULLABLE_FIELDS
        }
        await self.articles.update(article, **cleaned)
        return await self.details._build_detail(article)

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

    # ================================================================ 内部工具

    async def _resolve_slug(self, raw: str, *, fallback: str, exclude_id: int | None = None) -> str:
        """生成文章唯一 slug（唯一化规则见 ``app.utils.slug``）。"""
        return await unique_slug(
            raw,
            prefix=fallback,
            exists=partial(self.queries.slug_exists, exclude_id=exclude_id),
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
