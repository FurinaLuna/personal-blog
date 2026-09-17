"""文章版本历史 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RevisionAuthorBrief(BaseModel):
    """版本作者的精简信息。

    刻意不复用 ``UserBrief``：那个模型带有 ``avatar_url``，
    而版本列表一屏可能几十条，头像 URL 对「谁改的」这个问题毫无帮助。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str | None = None


class RevisionRead(BaseModel):
    """一个版本。

    ``content_md`` 在**列表**接口里是 ``None``：一篇文章几十个版本、
    每版几 KB 正文，全带上会让列表响应轻松上兆——这正是分页接口最常见的
    性能事故来源。要正文就单独取那一个版本。
    """

    id: int
    article_id: int
    title: str
    summary: str | None = None
    content_md: str | None = Field(default=None, description="仅单版本接口返回；列表中为 null")
    content_length: int
    reason: str = Field(description="save（保存前快照）/ restore（恢复前快照）")
    restored_from_id: int | None = Field(
        default=None, description="这一版是因为要恢复到哪个版本而产生的"
    )
    author: RevisionAuthorBrief | None = None
    created_at: datetime
