"""站点配置（关于页）Schema。

## 为什么社交链接也要校验协议

``social_links[].url`` 与 ``avatar_url`` 是「前端直接绑到 ``:href`` / ``:src``
的用户可控值」。它们此前是**唯一**漏掉协议校验的两个字段：评论的网站、留言板的
网站、友链地址都已经在用 ``app.utils.url``，于是「往 ``social_links`` 里存一个
``javascript:`` 地址」成了唯一一条能绕过该规则的路径 —— 而它渲染在**每一页的页脚**上。

参考部署的 nginx 有 CSP 兜底（``deploy/security-headers.inc`` 的 ``script-src 'self'``
且不带 ``unsafe-inline``），会挡住 ``javascript:`` 跳转；但裸机部署
（``uvicorn app.main:app`` 直接对外）没有这层保护。**安全判定不该依赖
「部署时恰好配了 nginx」**，所以这里补齐。

## 为什么不能直接复用 ``normalize_http_url``（这里踩过坑）

第一版就是直接复用的，结果**读接口 500**：``SocialLink`` 同时被 ``SiteProfileRead``
（响应模型）使用，而响应模型会 ``model_validate`` **库里的旧数据**——旧数据里
``{"label": "RSS", "url": "/api/v1/articles"}`` 是**相对路径**，会被判定为
「不是绝对地址」而抛错，于是 ``GET /api/v1/site/profile`` 整站 500。

所以这里有两条互相独立的规则，缺一不可：

1. **校验只放在写入模型上**（下面的 ``SocialLinkInput``），读模型保持宽松 ——
   与 ``FriendLinkRead`` 无校验器是同一个道理。响应模型的职责是「把库里已有的
   东西渲染出去」，它没有资格决定哪些历史数据能读出来；
2. **相对路径必须被接受**：种子数据里的 RSS 就是 ``/api/v1/articles``，它是站内
   同源路径，既不危险也有实际用途（``docs/DESIGN.md`` 的默认档案一直这么写）。
   真正要拦的是 ``javascript:`` / ``data:`` 这类**可执行伪协议**，以及
   ``//evil.test/x`` 这种**协议相对地址**（它等价于跨域绝对地址，只是写得更短）。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.utils.url import normalize_http_url


def _normalize_site_url(value: str | None, *, field: str) -> str | None:
    """社交链接 / 头像地址：站内路径原样放行，其余交给 ``normalize_http_url``。

    站内路径（``/api/v1/articles``、``/feed.xml``）对 ``href`` 与 ``src`` 都安全：
    浏览器只会解析到本站源上。刻意**不接受** ``//host/x``（协议相对地址）——
    它的实际解析结果是跨域绝对地址，与 ``https://host`` 等价，只是写得更短，
    是这类字段最常见的伪装。
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field}不能为空")
    if urlsplit(cleaned).scheme == "" and cleaned.startswith("/"):
        if cleaned.startswith("//"):
            raise ValueError(f"{field}只支持 http、https 或站内路径（不能是 // 开头的跨域地址）")
        return cleaned
    return normalize_http_url(cleaned, field=field)


class SocialLink(BaseModel):
    """**读模型**：不加校验器，理由见模块 docstring 第 1 条。"""

    label: str = Field(max_length=50, description="展示名，如 GitHub")
    url: str = Field(max_length=500, description="http/https 绝对地址，或 / 开头的站内路径")
    icon: str | None = Field(default=None, max_length=50, description="前端图标标识")


class SocialLinkInput(SocialLink):
    """**写模型**：只在这里判定协议。"""

    @field_validator("url")
    @classmethod
    def _clean_url(cls, value: str) -> str:
        cleaned = _normalize_site_url(value, field="社交链接地址")
        if cleaned is None:
            raise ValueError("社交链接地址不能为空")
        return cleaned


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
    show_login_entry: bool
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
    social_links: list[SocialLinkInput] | None = None
    skills: list[str] | None = None
    comment_need_approval: bool | None = None
    allow_guest_comment: bool | None = None
    show_login_entry: bool | None = None

    @field_validator("avatar_url")
    @classmethod
    def _clean_avatar(cls, value: str | None) -> str | None:
        """可空的头像地址：空白视为「没填」，统一收敛成 ``None``。

        统一成 ``None`` 而不是空串，与 ``FriendLinkUpdate`` 的理由相同：
        让「前端用首字占位」只需要判断一次 ``avatar_url === null``。
        这里复用社交链接的规则（允许站内路径、拦伪协议）——两者是同一类字段，
        分成两套判定只会让其中一个慢慢漂移。
        """
        return _normalize_site_url(value, field="头像地址")


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
    "SocialLinkInput",
]
