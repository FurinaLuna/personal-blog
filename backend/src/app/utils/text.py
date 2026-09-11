"""字符串工具。"""

from __future__ import annotations

import re
import unicodedata
import uuid

_SLUG_STRIP = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_SLUG_COLLAPSE = re.compile(r"-{2,}")


def slugify(text: str, *, fallback_prefix: str = "item", max_length: int = 80) -> str:
    """把任意标题转成 URL 友好的 slug。

    中文标题不做拼音转换（避免引入额外词典依赖），直接保留汉字——
    汉字在 URL 里会被百分号编码，浏览器地址栏依然可读，这是中文博客的常见做法。

    Args:
        text: 原始标题。
        fallback_prefix: 结果为空时的前缀（例如 ``article``）。
        max_length: 最大长度，超出部分截断。

    Returns:
        形如 ``hello-world`` / ``技术笔记`` 的 slug；输入无有效字符时返回
        ``{fallback_prefix}-{8位随机串}``，保证唯一性由调用方的去重逻辑兜底。
    """
    normalized = unicodedata.normalize("NFKC", text or "").strip().lower()
    normalized = _SLUG_STRIP.sub("-", normalized)
    normalized = _SLUG_COLLAPSE.sub("-", normalized).strip("-")
    if not normalized:
        return f"{fallback_prefix}-{uuid.uuid4().hex[:8]}"
    return normalized[:max_length].strip("-")


def short_uuid() -> str:
    """短随机串，用于文件名去重。"""
    return uuid.uuid4().hex[:12]


def truncate(text: str, limit: int) -> str:
    """按字符数截断，超出补省略号。"""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def estimate_reading_time(content_md: str, *, chars_per_minute: int = 400) -> int:
    """估算阅读时长（分钟）。

    中文按「字符数 / 400」计算，同时把 Markdown 语法符号排除在外，
    避免代码块把时长算爆。
    """
    if not content_md:
        return 1
    # 去掉代码块与图片语法，只统计真正要读的正文
    plain = re.sub(r"```.*?```", "", content_md, flags=re.DOTALL)
    plain = re.sub(r"!\[[^\]]*]\([^)]*\)", "", plain)
    plain = re.sub(r"[#>*`\-\[\]()!]", "", plain)
    length = len(re.sub(r"\s", "", plain))
    return max(1, round(length / chars_per_minute) or 1)


def strip_markdown(content_md: str, limit: int = 200) -> str:
    """从 Markdown 正文里抽取纯文本摘要（生成 summary 用）。"""
    if not content_md:
        return ""
    text = re.sub(r"```.*?```", "", content_md, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~`>]", "", text)
    return truncate(re.sub(r"\s+", " ", text).strip(), limit)
