"""应用配置：全部通过环境变量 / .env 注入，代码里不出现任何硬编码密钥。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/ 目录（config.py -> app/ -> src/ -> backend/）
BASE_DIR = Path(__file__).resolve().parents[2]

# 开发默认值单独抽成常量：check_production_safety() 要拿它做「是否仍在使用
# 仓库里公开的默认值」的比对，写成两处字面量迟早会改漏一处而让门禁失效。
_DEFAULT_JWT_SECRET = "dev-only-secret-key-change-me-in-production-0123456789"
_DEFAULT_ADMIN_PASSWORD = "admin123456"


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
    jwt_secret_key: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 7

    # ---------- CORS ----------
    # NoDecode：关闭 pydantic-settings 对 list 字段的自动 JSON 解析。
    # 否则 CSV 写法（A,B,C）会在进入 _split_csv 验证器之前直接抛 SettingsError，
    # 按旧版 .env.example 配置的项目根本起不来。
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ]

    # ---------- 站点对外地址 ----------
    # RSS / sitemap 里的链接必须是绝对 URL，否则阅读器和搜索引擎拿到相对路径会拼错。
    # 开发默认指向 Vite 端口；生产改成正式域名（含协议、不带尾斜杠）。
    site_base_url: str = "http://localhost:5173"
    # 文章详情页的路径模板，用来拼 RSS / sitemap 里的文章链接。
    # 之所以做成配置：后端原本硬编码 "/article/{slug}"，等于后端知道前端的路由形状，
    # 前端改路由时后端会静默生成 404 链接。放在这里至少是一处显式约定，
    # 并由 tests/test_feed.py 的用例与前端路由表做机器比对（不一致就测试失败）。
    site_article_path: str = "/article/{slug}"

    # ---------- 可观测性与限流 ----------
    # 访问日志用 JSON 输出（日志收集器可直接按字段过滤）。
    # 本地想看人读格式时设 LOG_JSON=false。
    log_json: bool = True
    log_level: str = "INFO"
    # 是否信任 X-Forwarded-For。只有确实部署在可信反代（nginx）之后才打开：
    # 该头可被客户端伪造，盲信会导致限流可绕过、甚至能借伪造 IP 封掉别人。
    trust_proxy_headers: bool = False
    # 限流开关。压测 / 本地联调时可临时关闭（生产不建议）。
    rate_limit_enabled: bool = True

    # ---------- 访问日志留存 ----------
    # visit_logs 每次详情访问写一行，而仪表盘最多只读近 90 天。
    # 不清理的话这张表会无限增长（个人博客量级下一年约几十万行），
    # 最终拖慢备份与迁移。保留期给到 90 天的两倍，留出回头看的余地。
    visit_log_retention_days: int = 180

    # ---------- 文件上传 ----------
    storage_dir: Path = BASE_DIR / "storage"
    media_url_prefix: str = "/media"
    max_upload_size: int = 10 * 1024 * 1024  # 10 MB
    thumbnail_max_width: int = 480
    thumbnail_max_height: int = 480
    # 上传图片时顺带生成的多尺寸变体宽度档位；宽度不足的档位自动跳过（不放大）。
    # 编码格式优先 AVIF（Pillow 无编码器时运行时降级 WEBP）。
    image_variant_widths: Annotated[list[int], NoDecode] = [480, 800, 1600]
    allowed_image_types: Annotated[list[str], NoDecode] = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/avif",
    ]
    # 说明：SVG 刻意不在默认白名单里。SVG 是 XML，可以内嵌 <script>，
    # 若从本站同源直出，攻击者上传一个 SVG 就等于拿到了存储型 XSS，
    # 可以盗取所有访客的登录态。真要用 SVG，请单独放到独立域名下托管。
    allowed_file_types: Annotated[list[str], NoDecode] = [
        "application/pdf",
        "application/zip",
        "text/plain",
        "text/markdown",
        "application/octet-stream",
    ]

    # ---------- 邮件通知（SMTP）----------
    # 默认全关：不开 SMTP 时评论接口照常工作，通知只是不发（fire-and-forget）。
    # 个人博客常用 QQ / 163 邮箱的 SMTP：开启 use_tls + 端口 465，
    # 密码用「授权码」而不是登录密码。
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    # 发件人地址；留空时回落到 smtp_username
    smtp_from: str = ""
    # True = 隐式 TLS（465）；587 端口的 STARTTLS 场景暂未支持
    smtp_use_tls: bool = True

    # ---------- 初始化管理员 / 演示数据 ----------
    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: str = _DEFAULT_ADMIN_PASSWORD
    seed_demo_data: bool = True

    @field_validator(
        "cors_origins",
        "allowed_image_types",
        "allowed_file_types",
        "image_variant_widths",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        """把字符串形式的列表环境变量统一解析成列表。

        同时接受两种写法：``A,B,C``（逗号分隔）与 ``["A","B"]``（JSON 数组）。
        JSON 分支在这里显式解析，而不是依赖 pydantic-settings 的自动解析。
        """
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def check_production_safety(self) -> list[str]:
        """生产环境的安全门禁：返回所有「用开发默认值上生产」的问题清单。

        为什么需要它：``is_production`` 原本只用来决定要不要关掉 ``/docs``，
        也就是说一个只改了 ``APP_ENV=production`` 的实例，会带着
        **写在开源仓库里的 JWT 密钥和管理员密码** 正常启动并对外服务。
        这类配置错误的代价是不可逆的——密钥一旦公开就不能再算作秘密。

        返回清单而不是直接抛异常，是为了把问题一次报全：运维改一轮就该过，
        而不是修一个报一个。

        Returns:
            人类可读的问题描述列表；生产环境无问题时为空列表。
        """
        if not self.is_production:
            return []

        problems: list[str] = []

        # 密钥：默认值人人可见，长度不足则 HS256 强度不够
        if self.jwt_secret_key == _DEFAULT_JWT_SECRET:
            problems.append(
                "JWT_SECRET_KEY 仍是仓库里的开发默认值——任何人都能伪造登录态。"
                "请用 `openssl rand -hex 32` 生成后写入环境变量。"
            )
        elif len(self.jwt_secret_key.encode("utf-8")) < 32:
            secret_bytes = len(self.jwt_secret_key.encode("utf-8"))
            problems.append(
                f"JWT_SECRET_KEY 长度不足 32 字节（当前 {secret_bytes}），"
                "HS256 签名强度不够。请用 `openssl rand -hex 32` 重新生成。"
            )

        # 管理员口令：默认值同样是公开的。首次启动会用它建号，
        # 即使之后在后台改过密码，这个默认值留在 .env 里也仍是隐患。
        if self.admin_password == _DEFAULT_ADMIN_PASSWORD:
            problems.append(
                "ADMIN_PASSWORD 仍是仓库里的开发默认值——等于把管理员账号公开。"
                "请改成强口令（首次启动 / 重置数据库时才会真正生效）。"
            )
        elif len(self.admin_password) < 8:
            problems.append("ADMIN_PASSWORD 少于 8 位，请改成更强的口令。")

        # 生产必须由 Alembic 管表结构。开着它意味着「改错模型 = 生产库被静默改表」
        if self.db_auto_create:
            problems.append(
                "DB_AUTO_CREATE=true：生产必须设为 false，改由 `alembic upgrade head` 管理表结构，"
                "否则一次模型误改就会在生产悄悄改表。"
            )

        # 演示数据会往生产库里塞 4 篇假文章，且是 ensure_seed 的触发条件
        if self.seed_demo_data:
            problems.append("SEED_DEMO_DATA=true：生产必须设为 false，否则会写入演示文章。")

        # Debug 打开时异常会带堆栈细节
        if self.debug:
            problems.append("DEBUG=true：生产必须设为 false，避免把内部堆栈暴露给访客。")

        return problems

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
