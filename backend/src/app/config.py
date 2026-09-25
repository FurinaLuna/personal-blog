"""应用配置：全部通过环境变量 / .env 注入，代码里不出现任何硬编码密钥。"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import EmailStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/ 目录（config.py -> app/ -> src/ -> backend/）
BASE_DIR = Path(__file__).resolve().parents[2]

# 开发默认值单独抽成常量：check_production_safety() 要拿它做「是否仍在使用
# 仓库里公开的默认值」的比对，写成两处字面量迟早会改漏一处而让门禁失效。
_DEFAULT_JWT_SECRET = "dev-only-secret-key-change-me-in-production-0123456789"
_DEFAULT_ADMIN_PASSWORD = "admin123456"

# 允许的运行环境白名单。
#
# 为什么必须是白名单，而不是"不等于 production 就当开发"：
# 整个生产安全门禁（拒绝默认密钥 / 默认口令 / DEBUG / DB_AUTO_CREATE /
# SEED_DEMO_DATA）都挂在 ``is_production`` 上。写成 ``APP_ENV=prod``、
# ``APP_ENV=prd``、``APP_ENV="production "``（末尾多一个空格）这类笔误，
# 会让 ``is_production`` 为假 ⇒ **门禁整体静默失效**，而服务照常启动、
# 日志里一句警告都没有。这是"配置写错却表现为一切正常"的典型形态，
# 所以宁可解析配置时就拒绝启动（与 admin_email 用 EmailStr 同一取舍）。
KNOWN_APP_ENVS = ("development", "testing", "production")

# uvicorn 会读的「worker 数」环境变量。
# WEB_CONCURRENCY 是 uvicorn（及 gunicorn）的约定名，UVICORN_WORKERS 是部分平台
# / 部署模板惯用的名字。两个都查，避免只堵住一个而漏掉另一个。
_WORKER_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS")
# uvicorn CLI 里声明 worker 数的短参数。
_WORKER_SHORT_FLAG = "-w"


def _worker_count_from_argv(argv: list[str]) -> str | None:
    """从命令行参数里取出显式声明的 worker 数，取不到返回 ``None``。

    支持 uvicorn CLI 的三种写法：``--workers N`` / ``-w N`` / ``--workers=N``
    （``-w=N`` 顺手也认，成本为零）。

    单独抽成函数只为让 ``check_worker_count()`` 保持短小；它**不是**一个
    "数进程"的探测，见 ``check_worker_count()`` 关于覆盖范围的说明。
    """
    for index, token in enumerate(argv):
        if token in ("--workers", _WORKER_SHORT_FLAG):
            if index + 1 < len(argv):
                return argv[index + 1]
            continue
        for prefix in ("--workers=", f"{_WORKER_SHORT_FLAG}="):
            if token.startswith(prefix):
                return token[len(prefix) :]
    return None


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

    # ---------- Refresh Token 的传输方式 ----------
    # 默认走 **httpOnly Cookie**：refresh token 一旦落到 localStorage，
    # 任何一次 XSS（第三方依赖漏洞、v-html 误用、浏览器扩展）都能把它读走，
    # 并顺着轮换链续期出新的 access token，形成长期冒充。
    # Cookie 上加了 httpOnly 之后 JS 读不到，这个攻击面就没了。
    #
    # 配套的取舍：access token 只能存内存（页面刷新即丢），
    # 所以前端每次冷启动都要先拿 refresh cookie 换一枚新的 access token。
    refresh_token_cookie_name: str = "blog_refresh"
    # 会话提示 Cookie：**非 httpOnly**、值恒为 "1"、不含任何秘密。
    #
    # 存在理由：access token 只存前端内存后，前端在页面加载时**无法从 JS 侧判断
    # 这台浏览器有没有会话**，于是连公开页面也会先打一次 ``POST /api/v1/auth/refresh``，
    # 匿名访客每次进站白吃一个 401。这个提示位让前端先判断"值不值得去续期"。
    #
    # 它的属性（secure / samesite / max_age / domain）与 refresh Cookie 对齐，
    # 但 **path 刻意不同**（提示 Cookie 用 ``/``，否则页面 JS 读不到）。详见
    # ``api/cookies.py`` 的 ``set_session_hint_cookie``。
    session_hint_cookie_name: str = "blog_session"
    # None = 跟着 is_production 走（生产自动 Secure）。显式设 true/false 可覆盖。
    cookie_secure: bool | None = None
    # lax：默认档。跨站 XHR 不会带上它，天然挡住大部分 CSRF；
    #     同站部署（nginx 反代 / Vite 代理，都是同源）完全够用。
    # strict：更严，但从外站点链接跳进来时第一次请求不带 Cookie。
    # none：**必须**同时 Secure，且只在前后端不同域的部署下才需要。
    cookie_samesite: str = "lax"
    # 留空 = 绑定当前完整域名（推荐）。填了才能跨子域共享（如 blog.example.com 与
    # api.example.com 要共用登录态时写 example.com）。
    cookie_domain: str | None = None
    # 是否在**响应体**里也返回 refresh token。
    # 只给非浏览器客户端（脚本、CI、curl 调试）开：浏览器一律走 Cookie，
    # 否则响应体里的那一份仍然能被页面 JS 读到，等于白改。
    refresh_token_in_body: bool = False

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
    # 允许上传的图片 MIME。
    #
    # 判定链是「Pillow 真解一遍 -> 拿到格式名（JPEG/PNG/...）-> 映射成 MIME -> 与
    # 这张表比对」，**不看客户端声明的 Content-Type**（那个头可以随便伪造）。
    # 所以这张表是真实生效的白名单，改它就会改变上传行为。
    #
    # 比表里多一个 image/bmp 是刻意的：服务层此前硬编码允许 BMP，而这张表漏了它，
    # 属于「文档与实际行为不一致」。统一以实际行为为准。
    allowed_image_types: Annotated[list[str], NoDecode] = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
    ]
    # 说明：SVG 刻意不在默认白名单里，而且**加了也没用**：Pillow 默认不解 SVG，
    # 这类文件会掉到「非图片附件」路径按扩展名判定，而 .svg 不在下面的扩展名表里。
    # SVG 是 XML，可以内嵌 <script>，若从本站同源直出，攻击者上传一个 SVG 就等于
    # 拿到了存储型 XSS，可以盗取所有访客的登录态。真要用 SVG，请单独放到独立域名下托管。
    #
    # 允许上传的非图片附件，单位是**扩展名（含前导点）**而不是 MIME：
    # 非图片附件没有可靠的嗅探手段（不解码内容就没法判型，而 Content-Type
    # 是客户端给的、可伪造），真实判定就是「扩展名在不在白名单里」——
    # 配置的单位必须与真实判定一致，否则又会变成配了却不生效的死配置。
    # 可执行 / 可被浏览器当同源脚本执行的一律不放进来（html/js/exe/svg...）。
    allowed_file_types: Annotated[list[str], NoDecode] = [
        ".pdf",
        ".zip",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".docx",
        ".xlsx",
        ".pptx",
        ".epub",
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
    # 必须是 EmailStr 而不是 str：这个值会被 seed 直接写进 users / site_profile，
    # 而写入路径不做校验（Pydantic 只管 schema 层），要等到读取时被
    # UserRead / SiteProfileRead 校验才炸。后果是——服务能正常启动、
    # 启动日志无任何告警，而 GET /site/profile 与 /auth/me 一律 500
    # （例如 ADMIN_EMAIL 填 RFC 保留域 admin@e2e.test 时）。
    # 这类「配置错了却静默降级成半残状态」的故障最难排查，所以宁可在
    # 解析配置时就失败：与 check_production_safety() 同一策略——
    # 启动失败一定会被处理，运行时 500 只会变成一条没人看的日志。
    admin_email: EmailStr = "admin@example.com"
    admin_password: str = _DEFAULT_ADMIN_PASSWORD
    seed_demo_data: bool = True

    @field_validator("app_env", mode="before")
    @classmethod
    def _validate_app_env(cls, value: Any) -> Any:
        """把 APP_ENV 收敛成白名单里的规范值，未知值直接拒绝启动。

        - 归一化：去首尾空白 + 转小写（``"Production "`` → ``production``），
          这样"大小写/空格写错"不再悄悄降级成开发环境；
        - 白名单：不在 ``KNOWN_APP_ENVS`` 里就抛错，让进程**起不来**。

        为什么值得为它写一个校验器：门禁的价值完全取决于
        ``is_production`` 判得准不准，而它的判据是用户手打的一个字符串。
        一次笔误的代价是"带着仓库公开的默认密钥对外服务"，且没有任何告警。
        """
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in KNOWN_APP_ENVS:
            raise ValueError(
                f"APP_ENV 只能是 {' / '.join(KNOWN_APP_ENVS)} 之一，当前为 {value!r}。"
                "写成 prod / prd 这类简写会让生产安全门禁静默失效（默认密钥、"
                "默认管理员口令、DEBUG、DB_AUTO_CREATE 都不会被拦下），"
                "所以这里选择直接拒绝启动。"
            )
        return normalized

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

    @field_validator("cookie_secure", "cookie_domain", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, value: Any) -> Any:
        """空串按「没配」处理。

        pydantic-settings 没开 ``env_ignore_empty``，空串会原样当成值传进来：
        ``COOKIE_SECURE=`` 会让 bool 解析直接报错、``COOKIE_DOMAIN=`` 会写出一个
        ``Domain=`` 为空的 Cookie。而 ``.env`` 里写 `KEY=`（留空但保留键）是
        很常见的写法，docker-compose 用 ``${COOKIE_SECURE:-}`` 透传时更是必然
        产生空串 —— 这两种形态都不该让应用起不来。
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cookie_samesite", mode="before")
    @classmethod
    def _validate_cookie_samesite(cls, value: Any) -> Any:
        """SameSite 只允许三个取值，写错就拒绝启动。

        为什么值得拦：`SameSite` 拼错（比如 `Lax;` 或 `nonee`）时浏览器会
        **按 Lax 处理**，于是"我明明配了 none 以便跨站部署"的预期静默落空，
        表现为"登录接口 200、set-cookie 也在，但下一个请求就是不带"——
        这类问题查起来极费时间，因为服务端看起来完全正常。
        """
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized not in ("lax", "strict", "none"):
            raise ValueError(f"COOKIE_SAMESITE 只能是 lax / strict / none 之一，当前为 {value!r}。")
        return normalized

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cookie_secure_flag(self) -> bool:
        """Cookie 是否加 Secure。

        未显式配置时跟着 ``is_production`` 走：生产必然是 HTTPS（或至少应该是），
        而开发是 http://localhost —— 对 localhost 加 Secure 会让 Chrome
        直接**拒绝写入** Cookie，表现为"登录 200 但刷新必掉线"。
        """
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_production

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

        # CORS：main.py 的中间件开着 allow_credentials=True，此时通配源等于
        # 「任意站点都能带着访客凭据读取响应」。空列表则是另一种错：
        # 前端请求会被浏览器全拦，人很容易为了"先让它跑起来"改成 "*"。
        if not self.cors_origins:
            problems.append(
                "CORS_ORIGINS 为空：生产必须填真实前端域名（逗号分隔）。"
                "留空会让浏览器拦掉所有跨源请求，进而诱导运维改成通配。"
            )
        elif "*" in self.cors_origins:
            problems.append(
                'CORS_ORIGINS 含 "*"：CORS 中间件开着 allow_credentials=True，'
                "通配源 + 携带凭据等于任意站点都能读取已登录访客的响应。"
                "请改成真实前端域名列表。"
            )

        # 数据库：SQLite 在本进程内独占文件锁，容器一旦多副本就是数据分裂，
        # 而且它没有并发写能力。这条此前完全没人管——带着 sqlite 上「生产」
        # 是能通过全部门禁检查的。
        if self.database_url.startswith("sqlite"):
            problems.append(
                "DATABASE_URL 指向 SQLite：生产请用 postgresql+asyncpg://…。"
                "SQLite 在多副本容器下会数据分裂，且不支持并发写入。"
            )

        # SameSite=none 的 Cookie 会被**任何站点**的请求带上，它唯一的合法搭档
        # 是 Secure（浏览器规范强制要求）。少了 Secure，refresh token 就变成
        # "谁都能在明文链路上拿到"，比放回 localStorage 还糟。
        if self.cookie_samesite == "none" and not self.cookie_secure_flag:
            problems.append(
                "COOKIE_SAMESITE=none 但 Cookie 未启用 Secure："
                "这种组合下 refresh token 会被任意站点在明文链路上带上。"
                "请设 COOKIE_SECURE=true（并确认站点确实是 HTTPS），或改回 lax。"
            )

        return problems

    def check_worker_count(self) -> list[str]:
        """检测进程是否被以多 worker 方式启动，返回问题清单。

        为什么需要它：限流器是**进程内**固定窗口（``utils/ratelimit.py``，计数在
        内存 dict 里，配额不跨进程共享）。多开 worker 的后果不是"性能变好"，
        而是**限流配额按 worker 数线性放大**——登录 5/分 在 4 worker 下变成 20/分，
        爆破成本直接降到四分之一，且没有任何告警。启动期的副作用（建表、灌种子、
        重建全文索引）也会被并发执行。

        此前只有 ``tests/test_deploy_config.py`` 静态断言 Dockerfile / compose 写了
        ``--workers 1``，挡不住"有人裸机用 ``uvicorn --workers 4`` 起"。这里补上
        运行时的检查，调用方（``main.py``）在生产直接拒绝启动、非生产只记警告。

        **覆盖范围的诚实说明（重要）**：本方法只能看到 ``sys.argv`` 与环境变量，
        因此**覆盖不了所有启动方式**——例如被进程管理器 / WSGI 包装以编程方式
        拉起（``uvicorn.run(app, workers=4)``）时，argv 里可能根本没有
        ``--workers``，此处的检查会漏过。它是一道"能挡住最常见误用"的门槛，
        **不是**一条完备的不变式。刻意不去数子进程或读 ``/proc``：那类做法在
        本仓库的跨平台约束（Windows 开发机 + Linux 容器）下不可靠，还会引入
        自己的一堆边界情况。

        Returns:
            人类可读的问题描述列表；未发现多 worker 隐患时为空列表。
        """
        problems: list[str] = []

        for name in _WORKER_ENV_VARS:
            raw = os.environ.get(name)
            # 没设或空串按「没配」处理（与 COOKIE_SECURE / COOKIE_DOMAIN 同一约定：
            # .env 里写 ``KEY=`` 很常见，不该被当成"配了个非法值"）。
            if raw is None or not raw.strip():
                continue
            if raw.strip() != "1":
                problems.append(
                    f"环境变量 {name}={raw!r}：限流器是进程内计数，多 worker 会让"
                    "登录 / 评论 / 点赞的配额按 worker 数放大，且没有任何告警。"
                    f"个人博客请保持单 worker（{name}=1）；真要扩容请先换 PostgreSQL "
                    "并把限流换成共享存储。"
                )

        argv_count = _worker_count_from_argv(sys.argv[1:])
        if argv_count is not None and argv_count != "1":
            problems.append(
                f"命令行参数指定了 --workers {argv_count}：限流器是进程内计数，"
                "多 worker 会让登录 / 评论 / 点赞的配额按 worker 数放大，且没有任何告警。"
                "请去掉该参数（uvicorn 缺省即单 worker）或显式写成 --workers 1。"
            )

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
