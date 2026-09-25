"""Refresh token 的 Cookie 读写与同源校验。

## 为什么会有这个文件

refresh token 以前放在 localStorage 里，任何一次 XSS 都能把它读走并顺着
轮换链续期出新的 access token。改到 **httpOnly Cookie** 之后 JS 读不到它，
这个攻击面就消失了——但代价是它变成了一个"浏览器会自动带上"的凭证，
于是必须同时处理两件 localStorage 时代不存在的事：

1. **Cookie 属性**（httpOnly / Secure / SameSite / Path）要按环境算对，
   算错的症状都是"登录 200、刷新必掉线"这种最难查的形态；
2. **CSRF**：Cookie 会被浏览器自动附带，所以刷新接口要多一道来源校验。
   没有它，任意站点都能让访客的浏览器悄悄拿一枚新的 access token。

规则集中在这里，是为了让「签发」「刷新」「登出」三处不会各写一份
set_cookie —— 三份迟早漂移，而漂移的表现是"登出后还能刷新"这种安全洞。
"""

from __future__ import annotations

from fastapi import Request, Response

from app.config import settings
from app.schemas.user import Token
from app.utils.exceptions import PermissionDeniedError, UnauthorizedError

# Cookie 只发到 API 前缀下：/media 与静态资源没必要带上登录凭证。
COOKIE_PATH = f"{settings.api_v1_prefix}/auth"


def set_refresh_cookie(response: Response, tokens: Token) -> Token:
    """把 refresh token 写进 httpOnly Cookie，并决定响应体里是否还留一份。

    Returns:
        实际应该返回给客户端的 Token。默认把 ``refresh_token`` 抹掉——
        响应体里的那一份页面 JS 读得到，留着等于白改。
        只有 ``REFRESH_TOKEN_IN_BODY=true``（非浏览器客户端）时才原样返回。
    """
    refresh_token = tokens.refresh_token or ""
    response.set_cookie(
        key=settings.refresh_token_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure_flag,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
    )
    if settings.refresh_token_in_body:
        return tokens
    return Token(access_token=tokens.access_token, expires_in=tokens.expires_in)


def clear_refresh_cookie(response: Response) -> None:
    """登出时删掉 Cookie。

    ``delete_cookie`` 本质上是发一个"已过期"的 Set-Cookie，**path 与 domain
    必须和写入时完全一致**，否则浏览器会把它当成另一个 Cookie，
    结果是"登出后 Cookie 还在"——用户以为下线了，凭证其实还能用。
    """
    response.delete_cookie(
        key=settings.refresh_token_cookie_name,
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
    )


def read_refresh_token(request: Request, payload_token: str | None) -> str:
    """取出本次刷新要用的 refresh token。

    优先级：**显式传入的 body > Cookie**。

    这样安排是因为两类客户端的诉求相反：
    - 浏览器不传 body，靠 Cookie（它读不到 Cookie 内容，只能靠浏览器自动附带）；
    - 脚本 / CI / curl 拿不到 Cookie Jar，显式传 body 才能跑；
    - 反向用例（"拿 access token 去刷新该被拒"）必须让 body 说话——
      否则浏览器 Cookie 会把这个本该失败的请求悄悄救回来，测试就失去意义了。
    """
    if payload_token:
        return payload_token
    cookie_token = request.cookies.get(settings.refresh_token_cookie_name)
    if not cookie_token:
        # 401 而不是 403：这是"没有凭证"，不是"有凭证但没权限"。
        # 前端拦截器靠 401 判断"该重新登录了"，用错状态码会让页面卡在
        # 「每个请求都失败但就是不跳登录页」的状态。
        raise UnauthorizedError("登录状态已失效，请重新登录")
    return cookie_token


def assert_same_origin(request: Request) -> None:
    """刷新接口的 CSRF 防线：带 Origin 的请求必须来自可信来源。

    为什么还需要它：`SameSite=lax` 已经挡掉了大部分跨站场景，但那依赖
    浏览器实现与"确实是 lax"这个前提；一旦有人为了跨站部署把
    ``COOKIE_SAMESITE`` 调成 ``none``，防线就只剩这一道了。

    不带 Origin 的请求（curl / 服务端脚本 / 老浏览器）直接放行：
    它们本来就无法被 CSRF 利用（没有"浏览器自动带凭证"这回事），
    拦住只会让回归脚本和命令行调试莫名其妙 403。
    """
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    allowed.add(settings.site_base_url.rstrip("/"))
    if origin.rstrip("/") in allowed:
        return
    raise PermissionDeniedError("跨站请求被拒绝")
