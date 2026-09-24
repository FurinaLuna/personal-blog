"""评论 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.utils.url import normalize_http_url


class CommentCreate(BaseModel):
    author_name: str | None = Field(
        default=None, max_length=50, description="登录用户可留空，自动取昵称"
    )
    author_email: EmailStr | None = Field(default=None, description="不公开，仅站长可见")
    author_site: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1, max_length=2000)
    parent_id: int | None = Field(default=None, description="回复某条评论时传其 id，仅支持两级")

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("评论内容不能为空")
        return value

    @field_validator("author_name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("author_site")
    @classmethod
    def _clean_site(cls, value: str | None) -> str | None:
        """只接受 http/https 的绝对地址（判定规则见 ``app.utils.url``）。

        规则本身抽到了 ``utils/url.py``：友链、以及将来的留言板都要用同一套判定，
        三处各写一份迟早会在某个入口漏掉一种伪协议 —— 而这类漏洞是存储型 XSS
        （前端把值直接绑到 `:href`，Vue 不清洗动态 href）。
        """
        return normalize_http_url(value, field="网站地址")


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    parent_id: int | None = None
    author_name: str
    author_site: str | None = None
    content: str
    is_admin_reply: bool
    is_approved: bool
    created_at: datetime
    replies: list[CommentRead] = Field(default_factory=list)


CommentRead.model_rebuild()


class CommentModerate(BaseModel):
    """站长审核 / 删除以外的人工调整。"""

    is_approved: bool | None = None
