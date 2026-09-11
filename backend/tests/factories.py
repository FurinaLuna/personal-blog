"""测试数据工厂。放在独立模块，让 conftest 与各测试文件都能复用同一套构造逻辑。"""

from __future__ import annotations

import io
import uuid

from PIL import Image

from app.models import Article, ArticleStatus, User, UserRole
from app.utils.security import hash_password

ADMIN_PASSWORD = "admin123456"
AUTHOR_PASSWORD = "author123456"


def unique_suffix() -> str:
    """短随机后缀，避免同一用例内多次创建同名资源造成 slug 冲突。"""
    return uuid.uuid4().hex[:6]


def make_article_payload(**overrides: object) -> dict[str, object]:
    """构造一篇文章的请求体。"""
    payload: dict[str, object] = {
        "title": f"测试文章-{unique_suffix()}",
        "content_md": "# 小标题\n\n这是正文内容，用于验证 Markdown 原文存储、摘要自动生成与阅读时长估算。",
        "status": "published",
        "tags": [f"标签-{unique_suffix()}"],
    }
    payload.update(overrides)
    return payload


def make_user_payload(**overrides: object) -> dict[str, object]:
    suffix = unique_suffix()
    payload: dict[str, object] = {
        "username": f"user{suffix}",
        "email": f"user{suffix}@example.com",
        "password": AUTHOR_PASSWORD,
        "nickname": f"用户{suffix}",
        "role": "author",
    }
    payload.update(overrides)
    return payload


def make_png_bytes(
    size: tuple[int, int] = (64, 48), color: tuple[int, int, int] = (90, 140, 220)
) -> bytes:
    """生成一张真实的 PNG 字节流。

    刻意用 Pillow 现造而不是塞一段假字节：上传接口会真的解码图片来判定类型，
    假字节会被正确拒绝，那样就测不到成功路径。
    """
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_jpeg_bytes(size: tuple[int, int] = (240, 180)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(210, 120, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


__all__ = [
    "ADMIN_PASSWORD",
    "AUTHOR_PASSWORD",
    "Article",
    "ArticleStatus",
    "User",
    "UserRole",
    "hash_password",
    "make_article_payload",
    "make_jpeg_bytes",
    "make_png_bytes",
    "make_user_payload",
    "unique_suffix",
]
