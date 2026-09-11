"""应用配置：全部通过环境变量 / .env 注入，代码里不出现任何硬编码密钥。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（config.py -> app/ -> src/ -> backend/）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置对象。

    所有字段都可以用同名（大写）环境变量覆盖，例如 ``JWT_SECRET_KEY=xxx``。
    列表字段支持 JSON 数组或逗号分隔字符串两种写法。
    """

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 基础 ----------
    app_name: str = "Personal Blog API"
    app_env: str = "development"  # development | testing | production
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ---------- 数据库 ----------
    # 开发默认 SQLite（零依赖启动）；生产建议
    # postgresql+asyncpg://user:pass@host:5432/blog
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'blog.db').as_posix()}"
    db_echo: bool = False
    # 启动时自动建表。开发/演示方便，**生产必须设为 false**，
    # 改由 `alembic upgrade head` 管理结构变更——否则一次误改模型就会在生产悄悄改表。
    db_auto_create: bool = True

    # ---------- JWT ----------
    # 开发默认值刻意凑够 32 字节以上：PyJWT 对 HS256 的密钥长度有下限警告，
    # 密钥长度不足会显著降低签名强度。生产必须用 `openssl rand -hex 32` 重新生成。
    jwt_secret_key: str = "dev-only-secret-key-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 7

    # ---------- CORS ----------
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ]

    # ---------- 文件上传 ----------
    storage_dir: Path = BASE_DIR / "storage"
    media_url_prefix: str = "/media"
    max_upload_size: int = 10 * 1024 * 1024  # 10 MB
    thumbnail_max_width: int = 480
    thumbnail_max_height: int = 480
    allowed_image_types: list[str] = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/avif",
    ]
    # 说明：SVG 刻意不在默认白名单里。SVG 是 XML，可以内嵌 <script>，
    # 若从本站同源直出，攻击者上传一个 SVG 就等于拿到了存储型 XSS，
    # 可以盗取所有访客的登录态。真要用 SVG，请单独放到独立域名下托管。
    allowed_file_types: list[str] = [
        "application/pdf",
        "application/zip",
        "text/plain",
        "text/markdown",
        "application/octet-stream",
    ]

    # ---------- 初始化管理员 / 演示数据 ----------
    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: str = "admin123456"
    seed_demo_data: bool = True

    @field_validator("cors_origins", "allowed_image_types", "allowed_file_types", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        """允许 ``A,B,C`` 形式的逗号分隔环境变量。"""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def upload_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def avatar_dir(self) -> Path:
        return self.storage_dir / "avatars"


@lru_cache
def get_settings() -> Settings:
    """带缓存的配置单例，避免每次请求重复解析 .env。"""
    return Settings()


settings = get_settings()
