"""附件 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    created_at: datetime


class UploadResult(AttachmentRead):
    """上传接口返回体，额外给出可直接粘贴进 Markdown 的片段。"""

    markdown: str = Field(description="形如 ![name](url) 的 Markdown 片段")
