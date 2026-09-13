"""附件 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ImageVariant(BaseModel):
    """多尺寸变体的一档。"""

    width: int = Field(description="变体宽度（像素），可直接拼进 srcset 的 w 描述符")
    url: str = Field(description="该尺寸文件的访问地址")


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    mime_type: str
    size: int = Field(description="字节数")
    kind: str = Field(description="image | file")
    width: int | None = None
    height: int | None = None
    url: str = Field(description="可直接放进 <img src> 或 Markdown 的相对地址")
    thumbnail_url: str | None = None
    variants: list[ImageVariant] = Field(
        default_factory=list,
        description="按宽度升序的多尺寸变体（小图/GIF/上传早于该功能的附件可能为空）",
    )
    created_at: datetime


class UploadResult(AttachmentRead):
    """上传接口返回体，额外给出可直接粘贴进 Markdown 的片段。"""

    markdown: str = Field(description="形如 ![name](url) 的 Markdown 片段")


class BackfillResult(BaseModel):
    """存量图片变体回填结果。"""

    processed: int = Field(description="本轮检查的图片数")
    updated: int = Field(description="成功生成并落库变体的图片数")
    skipped: int = Field(description="跳过数（已有变体 / GIF / 小图 / 原文件缺失或损坏）")
