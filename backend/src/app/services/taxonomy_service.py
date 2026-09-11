"""分类与标签服务。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ArticleStatus, Category, Tag
from app.repositories import CategoryRepository, TagRepository
from app.schemas.taxonomy import (
    CategoryCreate,
    CategoryUpdate,
    CategoryWithCount,
    TagCreate,
    TagUpdate,
    TagWithCount,
)
from app.utils.exceptions import ConflictError, NotFoundError
from app.utils.text import slugify

# 前台统计口径：分类/标签的计数只算已发布文章，与列表页保持一致
PUBLISHED_ONLY: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED,)


class TaxonomyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)

    # ---------------------------------------------------------------- 分类

    async def list_categories(self, *, with_counts: bool = True) -> list[CategoryWithCount]:
        """分类列表。

        Args:
            with_counts: 为 False 时跳过 count 子查询（例如只用于下拉选择框），
                省掉一次全表聚合。
        """
        if not with_counts:
            return [self._category_out(item, 0) for item in await self.categories.list_all()]

        rows = await self.categories.list_with_counts(statuses=PUBLISHED_ONLY)
        return [self._category_out(category, count) for category, count in rows]

    @staticmethod
    def _category_out(category: Category, count: int) -> CategoryWithCount:
        return CategoryWithCount(
            id=category.id,
            name=category.name,
            slug=category.slug,
            description=category.description,
            sort_order=category.sort_order,
            created_at=category.created_at,
            article_count=count,
        )

    async def get_category(self, slug_or_id: str) -> Category:
        category = await self._resolve_category(slug_or_id)
        if category is None:
            raise NotFoundError("分类不存在")
        return category

    async def create_category(self, payload: CategoryCreate) -> Category:
        if await self.categories.get_by_name(payload.name):
            raise ConflictError(f"分类「{payload.name}」已存在")
        slug = await self._unique_slug(
            payload.slug or payload.name, self.categories.slug_exists, "category"
        )
        return await self.categories.create(
            name=payload.name,
            slug=slug,
            description=payload.description,
            sort_order=payload.sort_order,
        )

    async def update_category(self, category_id: int, payload: CategoryUpdate) -> Category:
        category = await self.categories.get(category_id)
        if category is None:
            raise NotFoundError("分类不存在")

        data = payload.model_dump(exclude_unset=True)
        new_name = data.get("name")
        if new_name and new_name != category.name and await self.categories.get_by_name(new_name):
            raise ConflictError(f"分类「{new_name}」已存在")
        if new_slug := data.pop("slug", None):
            data["slug"] = await self._unique_slug(
                new_slug, self.categories.slug_exists, "category", exclude_id=category_id
            )

        # description 允许被清空为 null；其余字段收到 None 视为「不修改」
        for key, value in data.items():
            if value is not None or key == "description":
                setattr(category, key, value)
        await self.session.flush()
        return category

    async def delete_category(self, category_id: int) -> None:
        """删除分类。

        分类下的文章 **不删**：由数据库的 ``ON DELETE SET NULL`` 把它们变成
        「未分类」。内容永远比分类结构重要——这是内容型系统的第一原则。
        """
        category = await self.categories.get(category_id)
        if category is None:
            raise NotFoundError("分类不存在")
        await self.categories.delete(category)

    # ---------------------------------------------------------------- 标签

    async def list_tags(
        self, *, limit: int | None = None, min_count: int = 0, with_counts: bool = True
    ) -> list[TagWithCount]:
        if not with_counts:
            return [self._tag_out(item, 0) for item in await self.tags.list_all()]

        rows = await self.tags.list_with_counts(
            statuses=PUBLISHED_ONLY, limit=limit, min_count=min_count
        )
        return [self._tag_out(tag, count) for tag, count in rows]

    @staticmethod
    def _tag_out(tag: Tag, count: int) -> TagWithCount:
        return TagWithCount(id=tag.id, name=tag.name, slug=tag.slug, article_count=count)

    async def create_tag(self, payload: TagCreate) -> Tag:
        if await self.tags.get_by_name(payload.name):
            raise ConflictError(f"标签「{payload.name}」已存在")
        slug = await self._unique_slug(payload.slug or payload.name, self.tags.slug_exists, "tag")
        return await self.tags.create(name=payload.name, slug=slug)

    async def update_tag(self, tag_id: int, payload: TagUpdate) -> Tag:
        tag = await self.tags.get(tag_id)
        if tag is None:
            raise NotFoundError("标签不存在")

        data = payload.model_dump(exclude_unset=True)
        new_name = data.get("name")
        if new_name and new_name != tag.name and await self.tags.get_by_name(new_name):
            raise ConflictError(f"标签「{new_name}」已存在")
        if new_slug := data.pop("slug", None):
            data["slug"] = await self._unique_slug(
                new_slug, self.tags.slug_exists, "tag", exclude_id=tag_id
            )

        for key, value in data.items():
            if value is not None:
                setattr(tag, key, value)
        await self.session.flush()
        return tag

    async def delete_tag(self, tag_id: int) -> None:
        tag = await self.tags.get(tag_id)
        if tag is None:
            raise NotFoundError("标签不存在")
        await self.tags.delete(tag)

    async def cleanup_orphan_tags(self) -> int:
        """清理没有任何文章引用的空标签，返回删除条数。"""
        return await self.tags.delete_orphans()

    # ---------------------------------------------------------------- 工具

    async def _resolve_category(self, slug_or_id: str) -> Category | None:
        key = slug_or_id.strip()
        if key.isdigit():
            found = await self.categories.get(int(key))
            if found is not None:
                return found
        return await self.categories.get_by_slug(key)

    @staticmethod
    async def _unique_slug(
        raw: str,
        exists: Callable[..., Awaitable[bool]],
        prefix: str,
        *,
        exclude_id: int | None = None,
    ) -> str:
        """生成唯一 slug：重名时依次尝试 ``-2`` / ``-3`` …"""
        base = slugify(raw, fallback_prefix=prefix)
        candidate = base
        suffix = 2
        while await exists(candidate, exclude_id=exclude_id):
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 200:  # pragma: no cover - 防御性上限
                raise ConflictError("无法生成唯一 slug，请手动指定")
        return candidate
