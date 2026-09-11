"""站点配置（关于页）Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SocialLink(BaseModel):
    label: str = Field(max_length=50, description="展示名，如 GitHub")
    url: str = Field(max_length=500)
    icon: str | None = Field(default=None, max_length=50, description="前端图标标识")


class SiteProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    owner_name: str
    headline: str | None = None
    avatar_url: str | None = None
    bio_md: str | None = None
    about_md: str | None = None
    email: EmailStr | None = None
    location: str | None = None
    icp: str | None = None
    social_links: list[SocialLink] | None = None
    skills: list[str] | None = None
    comment_need_approval: bool
    allow_guest_comment: bool
    updated_at: datetime


class SiteProfileUpdate(BaseModel):
    owner_name: str | None = Field(default=None, max_length=50)
    headline: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = None
    bio_md: str | None = None
    about_md: str | None = None
    email: EmailStr | None = None
    location: str | None = Field(default=None, max_length=100)
    icp: str | None = Field(default=None, max_length=100)
    social_links: list[SocialLink] | None = None
    skills: list[str] | None = None
    comment_need_approval: bool | None = None
    allow_guest_comment: bool | None = None


class SiteStats(BaseModel):
    """首页仪表盘用的聚合数字。"""

    article_total: int
    published_total: int
    draft_total: int
    category_total: int
    tag_total: int
    comment_total: int
    pending_comment_total: int
    total_views: int
    latest_published_at: datetime | None = None


class ArchiveItem(BaseModel):
    id: int
    title: str
    slug: str
    published_at: datetime | None = None


class ArchiveGroup(BaseModel):
    """按年月归档。"""

    year_month: str = Field(description="形如 2026-09")
    count: int
    items: list[ArchiveItem]


__all__ = [
    "ArchiveGroup",
    "ArchiveItem",
    "SiteProfileRead",
    "SiteProfileUpdate",
    "SiteStats",
    "SocialLink",
]
