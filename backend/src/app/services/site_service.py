"""站点配置（关于页）与全站统计服务。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SiteProfile
from app.repositories import (
    ArticleRepository,
    CategoryRepository,
    CommentRepository,
    SiteRepository,
    TagRepository,
)
from app.schemas.site import SiteProfileRead, SiteProfileUpdate, SiteStats

# 允许被显式清空为 NULL 的可空字段（文本 + JSON 结构化字段）。
# owner_name 与两个布尔列都是非空列，不在此列——与 article_service 的
# NULLABLE_FIELDS 约定同名同义，保持两处写法一致。
NULLABLE_FIELDS = frozenset(
    {
        "headline",
        "avatar_url",
        "bio_md",
        "about_md",
        "email",
        "location",
        "icp",
        "social_links",
        "skills",
    }
)


class SiteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.site = SiteRepository(session)
        self.articles = ArticleRepository(session)
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)
        self.comments = CommentRepository(session)

    async def get_profile(self) -> SiteProfile:
        """取站点档案；不存在则自动建一行空档案，保证「关于页」永远有数据可渲染。"""
        return await self.site.get_or_create_profile()

    async def update_profile(self, payload: SiteProfileUpdate) -> SiteProfile:
        profile = await self.site.get_or_create_profile()
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            # 布尔开关传 False 天然通过（False is not None）；显式 null 只对
            # NULLABLE_FIELDS 生效——布尔列是非空的，把 None 写进去只会换来
            # 一条 500，所以布尔收到 null 一律视为「不修改」
            if value is not None or key in NULLABLE_FIELDS:
                setattr(profile, key, value)
        await self.session.flush()
        return profile

    @staticmethod
    def to_read(profile: SiteProfile) -> SiteProfileRead:
        return SiteProfileRead.model_validate(profile)

    async def stats(self) -> SiteStats:
        """仪表盘数字。一次聚合查询 + 几个 count，避免仪表盘打 8 个接口。"""
        article_stats = await self.articles.stats()
        return SiteStats(
            article_total=article_stats["article_total"],
            published_total=article_stats["published_total"],
            draft_total=article_stats["draft_total"],
            category_total=await self.categories.count_all(),
            tag_total=await self.tags.count_all(),
            comment_total=await self.comments.count(),
            pending_comment_total=await self.comments.count(approved=False),
            total_views=article_stats["total_views"],
            latest_published_at=article_stats["latest_published_at"],
        )
