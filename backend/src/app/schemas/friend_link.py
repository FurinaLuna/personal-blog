"""友情链接 Schema。

## URL 校验为什么必须走 ``app.utils.url``

``url`` 与 ``avatar_url`` 都是「前端会直接绑到 ``:href`` / ``:src`` 的用户可控值」，
判定规则只写在 ``app.utils.url.normalize_http_url`` 一处（评论的网站字段已经在用）。
本模块**不做**任何协议判断，只负责两件事：

1. 区分「必填」与「可空」两种语义（见 ``_friend_link_url`` / ``_avatar_url``）；
2. 在**归一化之后**补一次长度判定（原因见 ``_normalize_url`` 的 docstring）。

自己再写一份正则或 ``urlsplit`` 的后果是确定的：三处实现迟早只在一处补上
``javascript:`` / ``data:`` 这类伪协议，而漏掉的那个入口就是存储型 XSS。

## 三个类的分工

- ``FriendLinkBase``：创建时的完整字段与约束；
- ``FriendLinkCreate``：语义上等于 Base（单独命名，是为了让路由与服务的入参类型
  不叫「Base」——将来 Base 增减字段时，创建接口的签名不必跟着改名）；
- ``FriendLinkUpdate``：**全部字段可选**，语义是「只改传了的字段」。
  刻意不继承 Base：继承会让「Base 新增一个必填字段」静默变成
  「Update 也必填」，而这个类的全部意义就是可以不填。与 ``CategoryUpdate`` /
  ``SiteProfileUpdate`` 的写法一致。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.url import normalize_http_url

# 与 models/friend_link.py 的列长度、以及本模块 Field(max_length=...) 三处一致
URL_MAX_LENGTH = 500


def _normalize_url(value: str | None, *, field: str) -> str | None:
    """归一 + 长度判定；输入为空白时返回 ``None``。

    判定顺序是**先归一、后量长度**，不能反过来：``normalize_http_url`` 会给
    「没写协议」的输入补上 ``https://``，于是 500 个字符的原始输入会变成 508 个字符。
    只按原始输入卡长度的话，同一个请求在 SQLite 上会静默存下超长值
    （SQLite 不校验 ``VARCHAR(n)``），在 PostgreSQL 上则是 ``DataError`` → 500。
    两份数据库给出两种结果，是最难排查的一类不一致。
    """
    normalized = normalize_http_url(value, field=field)
    if normalized is None:
        return None
    if len(normalized) > URL_MAX_LENGTH:
        raise ValueError(f"{field}不能超过 {URL_MAX_LENGTH} 个字符")
    return normalized


def _friend_link_url(value: str | None) -> str:
    """必填的友链地址。

    空值（None / 空串 / 纯空白）在这里**报错**而不是放过：一条没有地址的友链
    在库里就是一行孤儿数据，而报错能让提交者立刻知道「地址没填」。
    """
    normalized = _normalize_url(value, field="友链地址")
    if normalized is None:
        raise ValueError("友链地址不能为空")
    return normalized


def _avatar_url(value: str | None) -> str | None:
    """可空的头像地址：空白视为「没填」，统一收敛成 ``None``。

    统一成 ``None`` 而不是空串，是为了让「前端用首字占位」只需要判断一次
    ``avatar_url === null``，不必再处理 ``""`` 这个同义值。
    """
    return _normalize_url(value, field="头像地址")


class FriendLinkBase(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="站点名")
    url: str = Field(max_length=URL_MAX_LENGTH, description="站点地址，必须 http/https")
    description: str | None = Field(default=None, max_length=200, description="一句话介绍")
    avatar_url: str | None = Field(
        default=None, max_length=URL_MAX_LENGTH, description="站点头像 / 图标地址"
    )
    sort_order: int = Field(default=0, ge=0, description="展示顺序，升序")
    is_active: bool = Field(default=True, description="是否在前台展示")

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        """先去空白，长度约束再作用于去空白之后的值。

        用 ``mode="before"`` 而不是默认的 ``after``：``after`` 会让 ``"   "``
        以「长度 3」通过 ``min_length=1``，然后被存进库——一个只看得见空白的
        友链卡片。放在 before 之后，空白名在 strip 后就是空串，
        ``min_length=1`` 自然拦下，不需要再写一个自定义报错。
        """
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def _clean_url(cls, value: str | None) -> str:
        return _friend_link_url(value)

    @field_validator("avatar_url")
    @classmethod
    def _clean_avatar(cls, value: str | None) -> str | None:
        return _avatar_url(value)


class FriendLinkCreate(FriendLinkBase):
    pass


class FriendLinkUpdate(BaseModel):
    """部分更新：**只改传了的字段**（服务层按 ``exclude_unset`` 落库）。

    ``name`` / ``url`` 上的 ``None`` 由校验器拒绝（422）而不是被静默忽略：
    「把友链的名字清空」在语义上不存在，服务层也就不需要为它准备一条分支。
    """

    name: str | None = Field(default=None, min_length=1, max_length=50)
    url: str | None = Field(default=None, max_length=URL_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=URL_MAX_LENGTH)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def _clean_url(cls, value: str | None) -> str:
        return _friend_link_url(value)

    @field_validator("avatar_url")
    @classmethod
    def _clean_avatar(cls, value: str | None) -> str | None:
        return _avatar_url(value)


class FriendLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    description: str | None = None
    avatar_url: str | None = None
    sort_order: int
    is_active: bool
    created_at: datetime
