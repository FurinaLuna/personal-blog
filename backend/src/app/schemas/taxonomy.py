"""分类与标签 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CategoryBase(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    slug: str | None = Field(default=None, max_length=80, description="留空则按名称自动生成")
    description: str | None = None
    sort_order: int = 0


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = None
    sort_order: int | None = None


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    sort_order: int
    created_at: datetime


class CategoryWithCount(CategoryRead):
    article_count: int = Field(default=0, description="该分类下已发布文章数")


class TagBase(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    slug: str | None = Field(default=None, max_length=80)


class TagCreate(TagBase):
    pass


class TagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    slug: str | None = Field(default=None, max_length=80)


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class TagWithCount(TagRead):
    article_count: int = Field(default=0, description="该标签下已发布文章数")


class TagBrief(BaseModel):
    """嵌在文章里的标签，只需 id/name/slug。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
