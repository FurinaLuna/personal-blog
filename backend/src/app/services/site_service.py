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
        # JSON 字段允许显式清空；布尔开关必须允许传 False，所以不能按「非 None」过滤
        always_apply = {"social_links", "skills", "comment_need_approval", "allow_guest_comment"}
        for key, value in data.items():
            if value is not None or key in always_apply:
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
