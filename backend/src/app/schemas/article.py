"""文章 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ArticleStatus
from app.schemas.common import Page
from app.schemas.series import SeriesBrief, SeriesRead
from app.schemas.taxonomy import TagBrief
from app.schemas.user import UserBrief


class CategoryBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class ArticleNeighbor(BaseModel):
    """上一篇 / 下一篇。"""

    id: int
    title: str
    slug: str


class ArticleSummary(BaseModel):
    """列表页卡片所需的字段——刻意不含 ``content_md``。

    列表接口一次返回 10~20 条，如果带上正文，单次响应可能上兆，这是最常见的
    分页接口性能事故来源。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    summary: str | None = None
    cover_image: str | None = None
    status: ArticleStatus
    is_top: bool
    allow_comment: bool
    view_count: int
    like_count: int
    reading_time: int
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    author: UserBrief | None = None
    category: CategoryBrief | None = None
    series: SeriesBrief | None = None
    series_order: int = 0
    tags: list[TagBrief] = Field(default_factory=list)
    comment_count: int = 0


class ArticleDetail(ArticleSummary):
    """详情页：在列表字段基础上补正文与上下篇。

    ``series_prev`` / ``series_next``：同系列内按 ``series_order`` 相邻的文章；
    不属于任何系列时为 ``None``。它服务于详情页顶部的系列导航条
    （全局 prev/next 仍按发布时间排，两者语义不同）。
    """

    content_md: str
    prev: ArticleNeighbor | None = None
    next: ArticleNeighbor | None = None
    series_prev: ArticleNeighbor | None = None
    series_next: ArticleNeighbor | None = None


class SeriesWithArticles(BaseModel):
    """系列详情响应：系列信息 + 该系列的文章（分页）。

    放在本模块而非 ``schemas.series``：它同时引用 ``SeriesRead`` 与
    ``ArticleSummary``，而 article schema 已被 series schema 依赖（``SeriesBrief``），
    反向放置会造成循环导入。
    """

    series: SeriesRead
    articles: Page[ArticleSummary]


class ArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=220, description="留空按标题自动生成")
    summary: str | None = Field(default=None, max_length=500, description="留空从正文自动摘要")
    content_md: str = Field(default="", description="Markdown 原文")
    cover_image: str | None = Field(default=None, max_length=500)
    status: ArticleStatus = ArticleStatus.DRAFT
    is_top: bool = False
    allow_comment: bool = True
    category_id: int | None = None
    series_id: int | None = Field(default=None, description="所属系列；不传表示不挂系列")
    series_order: int = Field(default=0, ge=0, description="系列内顺序，小者在前")
    tags: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="标签名列表；不存在的标签会自动创建",
    )

    @field_validator("tags", mode="after")
    @classmethod
    def _dedupe_tags(cls, value: list[str]) -> list[str]:
        """去重并去掉空白项，保持用户输入顺序。"""
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            name = item.strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result


class ArticleUpdate(BaseModel):
    """部分更新：只有显式传入的字段才会被写库。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=220)
    summary: str | None = Field(default=None, max_length=500)
    content_md: str | None = None
    cover_image: str | None = None
    status: ArticleStatus | None = None
    is_top: bool | None = None
    allow_comment: bool | None = None
    category_id: int | None = None
    # 显式传 null 表示「移出系列」；只传 series_order 表示仅调整顺序
    series_id: int | None = None
    series_order: int | None = Field(default=None, ge=0)
    tags: list[str] | None = Field(default=None, max_length=10)
