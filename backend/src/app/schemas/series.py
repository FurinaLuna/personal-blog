"""系列 Schema。

注意：``SeriesWithArticles``（系列详情响应）定义在 ``schemas.article`` 里——
它同时引用 ``SeriesRead`` 与 ``ArticleSummary``，而 article schema 已经依赖
本模块（内嵌 ``SeriesBrief``），若定义在这里会造成 series → article 循环导入。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SeriesBrief(BaseModel):
    """内嵌到文章里的轻量系列信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class SeriesRead(SeriesBrief):
    """系列列表 / 详情。"""

    description: str | None = None
    article_count: int = 0
    created_at: datetime
    updated_at: datetime


class SeriesCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120, description="留空按名称自动生成")
    description: str | None = Field(default=None, description="系列简介（会展示在详情页导航下方）")


class SeriesUpdate(BaseModel):
    """部分更新：只有显式传入的字段才会被写库。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, description="传 null 清空简介")
