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

此外还下发一个**非 httpOnly** 的「会话提示」Cookie（``set_session_hint_cookie``）：
它不含任何秘密，只是给前端一个"这台浏览器可能有会话"的提示位，用来省掉匿名访客
每次进站那一次多余的刷新请求。它与上面两条规则同处一地，正是为了避免"登出时
忘了删它"这类漂移。
"""

from __future__ import annotations

from fastapi import Request, Response

from app.config import settings
from app.schemas.user import Token
from app.utils.exceptions import PermissionDeniedError, UnauthorizedError

# Cookie 只发到 API 前缀下：/media 与静态资源没必要带上登录凭证。
COOKIE_PATH = f"{settings.api_v1_prefix}/auth"

# 会话提示 Cookie 的 path 与 refresh Cookie **刻意不同**。
#
# ``document.cookie`` 只暴露「路径是当前页面路径前缀」的 cookie，而 SPA 页面在
# ``/``。若把提示 Cookie 也收窄到 ``COOKIE_PATH``（``/api/v1/auth``），页面 JS
# 就**读不到它**——那这个 Cookie 等于白发（前端会永远认为"没有会话"，
# 公开页面再也不会恢复登录态，反而变成功能回归）。
# 它的值恒为 ``"1"``、不含任何秘密，放宽 path 的代价只是它会跟着 /media 等请求
# 一起发出去，无信息价值。
SESSION_HINT_COOKIE_PATH = "/"


def set_session_hint_cookie(response: Response) -> None:
    """下发「会话提示」Cookie：非 httpOnly、值恒为 ``"1"``、不含任何秘密。

    为什么需要它：access token 只存前端内存后，前端在页面加载时**无法从 JS 侧
    判断这台浏览器有没有会话**，于是连公开页面也会先打一次
    ``POST /api/v1/auth/refresh``，匿名访客每次进站白吃一个 401。这个提示位
    让前端先判断"值不值得去续期"。

    为什么它是安全的：值就是字符串 ``"1"`` —— **不是 token、不含用户标识、
    不含过期时间**。它只表达"这台浏览器可能有会话"。XSS 攻击者读不读它都没有
    额外收益：他想验证会话，直接发一个请求就知道了；想伪造会话也伪造不出什么
    （服务端不认这个 Cookie，只认 httpOnly 里那份 refresh token）。

    风险（必须知道）：hint 与实际会话状态**可能不一致**——例如用户手动删掉了
    refresh Cookie 而 hint 还在，或反之。所以它**只能用来省一次请求，
    绝不能用来做任何授权 / 安全决策**。前端受保护路由仍然无条件尝试恢复登录态
    （见 ``frontend/src/router/index.ts``），就是为了不让 hint 变成"偶发被登出"的判据。

    部署层面的边界：前后端**不同域**且未设 ``COOKIE_DOMAIN`` 时，本 Cookie 落在
    API 域上，SPA 的 ``document.cookie`` 读不到它 —— 这类部署会退化成"公开页不恢复
    登录态"（受保护路由不受影响，功能仍正确）。跨子域部署请设
    ``COOKIE_DOMAIN=example.com``；完全不同的域之间无法共享 Cookie，
    这个优化在该形态下天然不可用。

    属性与 refresh Cookie 对齐（secure / sameSite / max-age / domain），**只有
    path 刻意不同**：提示 Cookie 必须是 ``/``，否则页面 JS 读不到它
    （理由见 ``SESSION_HINT_COOKIE_PATH`` 的注释）。
    """
    response.set_cookie(
        key=settings.session_hint_cookie_name,
        value="1",
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        # 唯一与 refresh Cookie 相反的一项，也正是它存在的理由：JS 要能读到它。
        httponly=False,
        secure=settings.cookie_secure_flag,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        path=SESSION_HINT_COOKIE_PATH,
        domain=settings.cookie_domain,
    )


def set_refresh_cookie(response: Response, tokens: Token) -> Token:
    """把 refresh token 写进 httpOnly Cookie，并决定响应体里是否还留一份。

    顺带下发会话提示 Cookie（签发与刷新两条路径共用本函数，所以两条都覆盖到）。

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
    set_session_hint_cookie(response)
    if settings.refresh_token_in_body:
        return tokens
    return Token(access_token=tokens.access_token, expires_in=tokens.expires_in)


def clear_refresh_cookie(response: Response) -> None:
    """登出时删掉 Cookie（refresh 与提示 Cookie 都要删）。

    ``delete_cookie`` 本质上是发一个"已过期"的 Set-Cookie，**path 与 domain
    必须和写入时完全一致**，否则浏览器会把它当成另一个 Cookie，
    结果是"登出后 Cookie 还在"——用户以为下线了，凭证其实还能用。

    提示 Cookie 同理：漏删它的表现是"登出后公开页仍在尝试续期"（每次进站白跑
    一个注定 401 的请求），而且它会一直挂到 max-age 到期。
    """
    response.delete_cookie(
        key=settings.refresh_token_cookie_name,
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
    )
    response.delete_cookie(
        key=settings.session_hint_cookie_name,
        path=SESSION_HINT_COOKIE_PATH,
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
