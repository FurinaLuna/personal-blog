"""留言板 Schema。

三个响应模型的分工，是这个域最重要的接口契约：

- ``GuestbookMessageRead``：**公开**。刻意没有 ``author_email`` / ``ip_address`` /
  ``user_agent``——邮箱是留言者的隐私（只用于站长回复通知），IP 与 UA 是反垃圾留痕。
  另一个同样重要的理由：公开响应会进 ``PublicCacheMiddleware`` 的共享缓存
  （``/api/v1/guestbook`` 在 ``CACHEABLE_PREFIXES`` 里），里面出现任何一个
  「随请求变化」的字段都会污染缓存，所以宁可分成两个模型也不共用一个大而全的。
- ``GuestbookMessageAdminRead``：站长看的完整字段，多出 ``author_email`` 与
  ``ip_address``；它只出现在 ``/manage`` 与被 ``/manage`` 系接口返回的响应里，
  这两条路径都不在缓存白名单内。
- ``GuestbookMessageCreate``：请求体。校验口径（strip、空名字转 None、空正文报错、
  URL 归一）与 ``schemas/comment.py`` 逐字一致——同一类输入在两个入口必须给出
  同一种反应，否则「为什么评论拦了、留言没拦」就是下一个安全问题。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.utils.url import normalize_http_url


class GuestbookMessageCreate(BaseModel):
    author_name: str | None = Field(
        default=None, max_length=50, description="登录用户可留空，自动取昵称"
    )
    author_email: EmailStr | None = Field(
        default=None, description="不公开，仅站长可见；留了才收得到回复通知"
    )
    author_site: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("留言内容不能为空")
        return value

    @field_validator("author_name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        """纯空白等价于没填：服务层只需要判断一种「没有昵称」。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("author_site")
    @classmethod
    def _clean_site(cls, value: str | None) -> str | None:
        """只接受 http/https 的绝对地址（判定规则见 ``app.utils.url``）。

        与评论、友链共用同一套判定：前端把这个值直接绑到 `:href`，而 Vue
        不清洗动态 href，任何一个入口漏掉一种伪协议就是一条存储型 XSS 路径。
        """
        return normalize_http_url(value, field="网站地址")


class GuestbookMessageRead(BaseModel):
    """公开响应（字段名是冻结契约，前端 types 逐字对应）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    author_name: str
    author_site: str | None = None
    content: str
    is_approved: bool
    # 站长回复与回复时间一起给：只给文字不给时间，前端无法回答「这是刚回的还是很久以前回的」
    reply_content: str | None = None
    replied_at: datetime | None = None
    created_at: datetime


class GuestbookMessageAdminRead(GuestbookMessageRead):
    """站长视角：多出邮箱与 IP。"""

    author_email: str | None = None
    ip_address: str | None = None


class GuestbookReply(BaseModel):
    """站长回复的请求体。

    ``reply_content`` **允许空字符串**（也允许纯空白）：那表示「清除回复」。
    清除会把 ``replied_at`` / ``replied_by_id`` 一并置空——留下一个「回复时间为 T
    但回复内容为空」的状态，前端就得为它写一套没意义的展示分支。
    长度上限 2000 与新建留言的正文一致：回复不该比留言还长。
    """

    reply_content: str = Field(
        max_length=2000,
        description="回复正文；传空字符串（或纯空白）表示清除这条回复",
    )


class GuestbookModerate(BaseModel):
    """站长审核：放行 / 撤回（口径与 ``CommentModerate`` 一致）。"""

    is_approved: bool | None = None
