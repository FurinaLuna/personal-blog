"""评论邮件通知 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UnsubscribeRequest(BaseModel):
    """退订请求体。

    token 是邮件里退订链接携带的签名 JWT（``sub`` 为小写邮箱），
    前端从 query 取出后原样放进 body POST 过来。
    """

    token: str = Field(min_length=1)
