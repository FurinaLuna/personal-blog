# 代码库结构化分析报告

> 生成日期：2026-10-01 ｜ 分析方式：只读静态分析（未运行测试、未修改任何代码）
> 分析范围：项目根目录配置、后端 `backend/src/app`、前端 `frontend/src`、部署与工具链
> 分析基线：工作区当前状态（HEAD = `422a2ec`，另有未提交改动，见 §5.2）

---

## 1. 项目整体用途与架构设计

### 1.1 用途

一套**可直接上线的个人博客系统**（`README.md`）：Markdown 写作、分类/标签/系列、草稿保护、两级评论审核、点赞与阅读统计、媒体库、友链、留言板、RSS/sitemap、评论邮件通知。仓库自带 Hexo 旧站迁移工具（`tools/hexo_import/`），且已实际迁移 23 篇历史文章——不是空壳模板。

工程目标是「写出来能跑、改起来可验证」：后端 822 条 pytest 用例（SQLite / PostgreSQL 双方言各跑一遍）、前端 54 个 spec 文件 849 例、4 套真实浏览器 CDP 回归脚本、1 套部署产物验证脚本（`tools/deploy-check.mjs`，49 项断言）。

### 1.2 架构设计

**前后端分离，单体后端，五层严格分层。**

```
浏览器 ── nginx(frontend 容器) ──> FastAPI(backend 容器) ──> SQLite / PostgreSQL
                                          │
                              /media 静态挂载 + /api/v1 JSON
```

后端分层（由 `backend/.importlinter` 声明、CI 强制，实测 2 条契约 KEPT）：

```
api（路由，只做参数解析与编排）
 └─> services（业务规则唯一所在地，抛领域异常，不知道 HTTP）
      └─> repositories（数据访问，只 flush 不 commit）
           └─> models（SQLAlchemy ORM，15 张表）
schemas（Pydantic 请求/响应模型）与 config（配置）为旁路依赖，utils 为叶子
```

几个有代表性的架构决策（均出自源码注释与 `docs/MODULES.md`）：

| 决策 | 位置 | 要点 |
|---|---|---|
| 领域异常翻译层 | `backend/src/app/main.py:192` | service 层只抛 `DomainError` 子类，HTTP 映射集中在入口，「换 CLI 入口不动业务代码」 |
| 仓储只 flush、路由显式 commit | `backend/src/app/db/session.py:70` | 一个 HTTP 请求一个事务；teardown commit 只是安全网（FastAPI yield 依赖收尾在响应发送后，曾实测破坏 read-your-writes） |
| 凭证方案 | `backend/src/app/api/cookies.py` + `frontend/src/api/http.ts` | refresh token 走 httpOnly Cookie（Path=`/api/v1/auth`）、access token 只存内存；非 httpOnly 的 `blog_session` 提示 Cookie（Path=`/`）只用来省请求、不参与授权 |
| 纯读详情 + 计数分离 | `backend/src/app/api/cache.py:41` | 详情接口不再回写 view_count（改走 `POST /articles/{id}/view`），从而可进 ETag/304 缓存白名单 |
| fail-closed 配置门禁 | `backend/src/app/config.py:380` | `check_production_safety()`：生产带默认密钥/口令/DEBUG/SQLite 直接拒绝启动；`APP_ENV` 白名单校验防笔误静默降级 |

前端架构：Vue 3 SPA + 三布局（`frontend/src/layouts/`：Default/Admin/Blank），布局由路由 `meta.layout` 表达而非路由嵌套（`frontend/src/router/index.ts:120` 有完整的反例注释）；设计令牌单一来源 `frontend/src/styles/tokens.css`；测试约定「不 mock 业务模块，只替换网络出口」。

---

## 2. 技术栈与主要依赖

### 2.1 后端（`backend/pyproject.toml`）

| 依赖 | 约束 | 作用 |
|---|---|---|
| fastapi | >=0.115,<1.0 | Web 框架：异步 + 依赖注入 + OpenAPI |
| uvicorn[standard] | >=0.32,<1.0 | ASGI 服务器（生产 `--workers 1 --no-proxy-headers`，见 §5.4） |
| sqlalchemy[asyncio] + greenlet | 2.0.x | 全异步 ORM；`greenlet` 是异步桥 |
| aiosqlite / asyncpg（`[postgres]` extra） | — | 双方言驱动；默认 SQLite 零依赖，一行环境变量切 PG |
| alembic | >=1.14,<2.0 | 迁移（18 条，升降级双方言验证过） |
| pydantic + pydantic-settings | v2 | 校验与配置注入 |
| pyjwt + bcrypt | — | 双 Token 认证；密码只存哈希 |
| pillow | >=11.3,<13.0 | 上传安全核心：真实解码判型（不信任 Content-Type）、像素炸弹防护、AVIF/WEBP 多尺寸变体 |
| aiosmtplib | >=3.0,<6.0 | 评论邮件通知（fire-and-forget，失败只记日志） |
| email-validator / python-multipart | — | 邮箱校验 / 表单解析 |

依赖策略：**下界 + 兼容上界**（`pyproject.toml:9-14` 注释记录了只写下界曾装出跨两个主版本环境的教训）；`backend/requirements.lock` 是权威锁，`tests/test_dependency_lock.py` 机器校验两者同步；Dockerfile 按 lock 安装 + `--no-deps` 装项目本身（`deploy/Dockerfile.backend:33-42`）。

开发工具：pytest / pytest-asyncio / pytest-cov（覆盖率门槛 `fail_under=80`，防退化用）、ruff（lint+format）、import-linter（分层契约）、httpx（测试客户端）。**未使用 uv**，约定 `python -m venv` + `pip install -e ".[dev]"`。

### 2.2 前端（`frontend/package.json`）

| 依赖 | 作用 |
|---|---|
| vue 3.5 + vue-router 4.5 + pinia 2.3 | 框架 / 路由 / 状态（auth、site、theme 三个 store） |
| axios | HTTP 客户端，全部收口在 `frontend/src/api/http.ts` |
| marked + dompurify + highlight.js | Markdown 渲染/白名单消毒/代码高亮（按需注册 17 种语言省 60% 体积）；消毒排在增强之前 |
| tailwindcss 3.4 + typography 插件 | 样式；语义色变量集中在 `styles/tokens.css` |

构建：Vite 6（vendor/markdown 手动分包，`frontend/vite.config.ts:36`）；质量链：vue-tsc、ESLint（含 `eslint-plugin-vuejs-accessibility` 无障碍规则）、Vitest 5。

### 2.3 部署与验证链

- `docker-compose.yml`：4 服务——db（postgres:16-alpine，不对宿主发布）、backup（同镜像 pg_dump 每 24h 到宿主 `./backups`，先写 `.part` 再 `pg_restore -l` 校验）、backend（不发布端口，入口先 `alembic upgrade head`）、frontend（nginx，唯一对外入口 8080）。
- `deploy/`：两份 Dockerfile + nginx 配置族（HTTP/HTTPS 共用 `nginx-server.inc` 防漂移；HSTS 条件式下发；80 端口 ACME 挑战不被重定向吃掉）。
- CI：`.github/workflows/ci.yml` 单文件 8 作业（lint / 双方言后端测试 / 前端类型+lint+单测 / e2e_live / 构建等）。
- 验证分层：pytest（单元）→ `tools/e2e_live/e2e_run.py`（真实进程+临时库 64 条）→ CDP 三脚本（smoke 47 / interaction 25 / full-check 66）→ `deploy-check.mjs`（真构建镜像真起栈 49 项）。**层与层刻意不互相替代**（如 full-check 不覆盖 e2e_live）。

---

## 3. 目录结构与模块职责

### 3.1 后端 `backend/src/app/`

| 路径 | 职责 | 关键文件 |
|---|---|---|
| `main.py` | 应用工厂：安全门禁 → 日志 → worker 检查 → 中间件栈 → 异常处理 → 路由 → lifespan（建表/检索索引/seed/清理） | `create_app()`（:367）、`lifespan`（:47） |
| `config.py` | 全部配置（pydantic-settings，环境变量注入）+ 生产门禁 + worker 检查 | `Settings`、`check_production_safety()`（:380） |
| `api/` | 路由层：`v1/` 13 个资源模块（auth/articles/comments/taxonomy/series/attachments/guestbook/links/site/stats/notifications/revisions）；`deps.py` 依赖注入（会话/认证/角色门禁/限流）；`middleware.py`（RequestContext + HEAD 改写）；`cache.py`（公开读 ETag）；`cookies.py`（refresh/提示 Cookie 下发）；`feed.py`（RSS/sitemap） | `api/v1/__init__.py`（注册顺序注释：revisions 必须先于 articles，防 `5/revisions` 被当 slug） |
| `models/` | 15 张表：13 实体（User/Article/ArticleRevision/Comment/Category/Tag/Series/Attachment/FriendLink/GuestbookMessage/SiteProfile/VisitLog/NotificationOptOut/RefreshSession）+ `associations.py` 关联表 | `models/__init__.py` 全量导出（Alembic autogenerate 依赖）；`article.py` 含 ORM 事件维护的派生列 `published_ym` |
| `schemas/` | Pydantic 请求/响应模型，含防提权逻辑（如 `SocialLinkInput` 写入校验协议、读模型刻意不校验旧数据） | `schemas/article.py`、`schemas/site.py` |
| `services/` | 业务规则唯一所在地；文章按 CQRS 拆为 `article_query_service.py` / `article_command_service.py` | `auth_service.py`（轮换/吊销）、`attachment_service.py`（Pillow 判型+变体）、`seed.py`（幂等 seed） |
| `repositories/` | 数据访问，只 flush 不 commit；文章同样拆 query/write 两份 | `base.py`、`article_query_repository.py`、`article_write_repository.py` |
| `db/` | `session.py`（引擎/会话/SQLite PRAGMA：外键+WAL）、`base.py`（Base+TimestampMixin）、`types.py`（UTCDateTime）、`fulltext.py`（双方言检索索引） | |
| `utils/` | `security.py`（JWT/bcrypt）、`ratelimit.py`（进程内限流）、`storage.py`（上传目录）、`logging.py`（request_id + JSON 日志）、`exceptions.py`（领域异常族）、`email.py` | |

`backend/alembic/versions/`：18 条迁移（2026-09-11 初始结构 → 09-28 `contact_qrcodes`），含双方言全文检索迁移 `20260922_1840`。

### 3.2 前端 `frontend/src/`

| 路径 | 职责 |
|---|---|
| `main.ts` | 入口：样式顺序（highlight 主题先于自定义）→ pinia → router → 等 `router.isReady()` 再挂载（防首屏闪错布局） |
| `api/` | `http.ts` 统一 HTTP 客户端（token 附加、401 静默续期+单飞锁+重放、错误归一化为 `ApiError`）；其余按资源一模块（articles/auth/comments/...） |
| `stores/` | `auth.ts`（登录态唯一真相，restore/restoreIfLikely 两入口）、`site.ts`（站点档案）、`theme.ts`（三态主题） |
| `router/index.ts` | 路由表 + 守卫（受保护路由带 6s 超时等 restore；公开页只 `restoreIfLikely()`） |
| `composables/` | `useAsyncData` / `useAction` / `useDraftAutosave` / `useTagInput` / `useUpload` 等，全部有同名 spec |
| `views/` | 前台 14 页 + `admin/` 11 页，每页均有同名 spec（25/25 覆盖） |
| `styles/` | `tokens.css`（颜色/字体/动效单一来源）→ base → components → prose → vendor |
| `utils/` | `markdown.ts`（渲染+消毒）、`highlight.ts`、`prefetch.ts`、`format.ts` 等 |

### 3.3 其余顶层

- `tools/hexo_import/`：独立 venv 的迁移工具（parse/transform/preflight/import，141 条用例），走 HTTP API 写入以复用服务层校验。
- `tools/*.mjs`：CDP 回归脚本族 + `deploy-check.mjs`。
- `docs/`：`DESIGN.md`（设计权威）、`MODULES.md`（模块边界）、`STYLEGUIDE.md`、`TEST-REPORT.md`（时点快照登记）、`devlog/`（逐日决策）。
- `Makefile`：全流程命令入口（Windows 无 make 时注释里有原始命令）。

---

## 4. 程序入口、关键执行流程与数据流转路径

### 4.1 后端启动流程

```
uvicorn app.main:app（Makefile:54 裸机 / deploy/Dockerfile.backend:106 容器）
 └─ create_app()（main.py:367）
     1. _enforce_production_safety()   ← 生产带默认密钥/口令/DEBUG/SQLite → RuntimeError
     2. setup_logging()                ← request_id + JSON 结构化日志
     3. _enforce_worker_invariant()    ← 生产多 worker 拒绝启动（限流是进程内的）
     4. FastAPI 实例（生产关 /docs /redoc）
     5. 中间件栈（注册顺序 = 外→内，实测踩出来的）：
        RequestContext（X-Request-ID，最外，被 CORS 拒的请求也留痕）
          → Head（HEAD 改写 GET）
            → PublicCache（对完整正文取哈希算 ETag）
              → CORS
                → 路由
     6. 异常处理注册 + api_router(/api/v1) + feed_router(/feed.xml,/sitemap.xml)
        + /health /ready / + /media 静态挂载
 └─ lifespan（main.py:47）
     storage.ensure_dirs → (db_auto_create ? create_all)
     → _ensure_fulltext_index（SQLite FTS5 / PG pg_trgm+GIN；失败只降级不阻断）
     → ensure_seed（失败 → 应用拒绝启动，日志与行为一致）
     → _prune_visit_logs（180 天）/ _prune_refresh_sessions
     → yield → engine.dispose()
```

### 4.2 一次写请求的数据流（以发评论为例）

```
POST /api/v1/comments/article/{id}
 → RequestContextMiddleware（分配 request_id）
 → 限流依赖 comment:5/分（api/deps.py:191，key = 规则名+客户端 IP）
    （IP 判定：TRUST_PROXY_HEADERS=false 时不信 XFF；信时取 X-Real-IP 或 XFF 最右一跳——
     曾有「取最左=限流可绕过」的真实漏洞，deps.py:129 注释留档）
 → 路由 handler（api/v1/comments.py）解析参数
 → CommentService（业务规则：草稿 404、长度校验、敏感词、状态机）
    领域异常 → DomainError → 4xx 信封（main.py:199）
 → CommentRepository（SQLAlchemy 写入，只 flush）
 → handler 末尾显式 session.commit()（session.py:70 的事务契约）
 → 响应；邮件通知为 fire-and-forget 后台任务，失败只记日志
```

### 4.3 认证与凭证流转（本项目的安全核心）

```
登录：POST /auth/login（5/分）
 ├─ 响应体：access_token（前端只存内存变量，frontend/src/api/http.ts:94）
 ├─ Set-Cookie: blog_refresh（httpOnly, Path=/api/v1/auth, SameSite=lax）
 └─ Set-Cookie: blog_session=1（非 httpOnly, Path=/ —— 仅「可能有会话」的提示位）

冷启动恢复（frontend/src/router/index.ts:249 守卫）：
 ├─ 受保护路由：auth.restore() 无条件续期（带 6s 超时；restored=false 时放行而非踢登录页）
 └─ 公开页：restoreIfLikely() —— 无提示 Cookie 即短路，省掉匿名访客那次注定 401 的请求

请求中 401（http.ts:336 拦截器）：
 内存有 access token → refreshSession()（单飞锁，多请求并发 401 只发一次 refresh）
   → POST /auth/refresh（空 body {}，Cookie 自动带；带 Origin 的请求必须落在
     CORS_ORIGINS 白名单——它兼作 CSRF 防线）
   → 成功：换新 token 并重放原请求一次
   → 重放仍 401 或续期失败：forceLogout()（只有当前页需要登录才跳登录页）

轮换与吊销（backend/src/app/services/auth_service.py）：
 refresh token 落库只存 jti 哈希，每次刷新轮换；已轮换令牌再现 → 判盗用吊销整族
 （30 秒宽容窗口容忍多标签页）；登出 = token_version += 1 + 按行吊销全部会话；
 access token 每次请求比对 token_version（api/deps.py:63）
```

### 4.4 读路径缓存流

匿名 GET 命中 `CACHEABLE_PREFIXES`（articles/categories/tags/series/site/comments/links/guestbook 公开版；排除 `/manage`、`/revisions`）→ `PublicCacheMiddleware` 对响应体取 sha256 前 32 位作弱 ETag + `max-age=60` + `Vary: Authorization`（带 Authorization 一律跳过，防共享缓存越权读）。详情页已改纯读，计数分离到 `POST /articles/{id}/view`（原子自增，60/分限流）。

### 4.5 部署链

```
docker compose up（配置唯一来源：项目根 .env）
 → db（healthcheck pg_isready）
 → backend（entrypoint: alembic upgrade head → uvicorn --workers 1 --no-proxy-headers，
            非 root，storage 为卷，不发布端口）
 → frontend（多阶段：node 构建 → nginx；additional_contexts 挂 deploy/ 防配置双份漂移；
            8080 唯一入口，CSP + 安全响应头 + 条件式 HSTS）
 → backup（等 db 与 backend 双健康后才动手——避免备份出 0 张表的结构合法空 dump；
           每 24h pg_dump → .part → pg_restore -l 校验 → 改名，落宿主 ./backups）
```

---

## 5. 潜在问题、技术风险与可优化之处

> 以下按「建议尽快处理 → 结构性约束 → 可选优化」排序。本项目代码质量与文档密度显著高于平均水平（大量「为什么」注释、机器校验的文档-代码一致性用例），所列问题多为**已知取舍**或**配置层**问题，非低级缺陷。

### 5.1 建议尽快处理

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **根 `.env` 是开发形态，与 compose 的生产默认值相反** | 实测根 `.env`：`APP_ENV=development`、`SEED_DEMO_DATA=true`；而 `docker-compose.yml:122-129` 写 `${APP_ENV:-production}` / `${SEED_DEMO_DATA:-false}`——插值优先采用 `.env` 显式值 | 用当前 `.env` 直接 `docker compose up` = **开发模式对外服务**（`/docs` 敞开、DEBUG 带堆栈）+ 生产库灌入演示数据。且 `APP_ENV=development` 时 `check_production_safety()` 整体不触发，两道防线同时失效。**上线前必须改**（此前审计已标记待拍板） |
| 2 | **工作区有约 15 个前端文件未提交 + 未跟踪截图** | `git status`：`SiteHeader.vue` / `tokens.css` / 多个 views 修改、`docs/screenshots/after-*.png` 未跟踪 | 一批 UI 改版进行中且未入库；远端 CI（run #53，`422a2ec`）不覆盖这些改动，README 基线表已声明此口径。建议完成验证后尽快提交，避免长期游离 |
| 3 | **根目录 `storage/` 是残留目录** | 实测存在 `storage/uploads`、`storage/avatars`；真实上传目录是 `backend/storage/`（`config.py:189` `BASE_DIR / "storage"`） | 误导备份脚本与人工判断；建议确认内容后清理（历史审计已标记） |

### 5.2 结构性约束（设计取舍，扩容前必须知道）

| # | 约束 | 证据 | 说明 |
|---|---|---|---|
| 4 | **限流是进程内固定窗口 → 单 worker 天花板** | `utils/ratelimit.py:9`（自述边界）、`deploy/Dockerfile.backend:88-96`（`--workers 1` + 理由）、`config.py:473`（`check_worker_count` 运行时兜底） | 多 worker = 配额按进程数放大 + lifespan 副作用并发执行（SQLite 并发 DDL 实测 `disk I/O error`）。门禁自述**非完备**：程序化 `uvicorn.run(workers=4)` 检测不到。扩展路径明确：先换 PG，再把限流换共享存储 |
| 5 | **PG 全文检索可能静默降级** | `main.py:86-118`、`db/fulltext.py`、迁移 `20260922_1840` | 托管 PG 无 `CREATE EXTENSION` 权限时 pg_trgm 建不出 → 搜索退回无索引 ILIKE。已做到可观测（`/ready` 报状态、warning 留原因），但无主动告警——搜索变慢仍依赖人发现 |
| 6 | **`Settings` 属性无 `validate_assignment`** | `config.py` 模型定义（历史审计结论） | 正常入口有白名单校验，但 monkeypatch 绕过构造器改 `app_env` 时门禁失效。仅测试场景相关，残余风险已知 |
| 7 | **点赞无身份去重**（设计如此） | README / 历史审计 | 防刷仅靠 20/分 限流；数据可信度要求提高时需加指纹或登录门槛 |
| 8 | **限流窗口边界瞬时双倍放行** | `ratelimit.py:37` 自述 | 固定窗口固有特性，防刷场景够用；在意可换滑动窗口 |

### 5.3 可选优化

- **SMTP 仅支持隐式 TLS(465)**，587 STARTTLS 未实现（`config.py:247-248`）——用 587 端口邮件服务商时需补。
- **Tailwind 3.4**：v4 已发布，升级非必须；升级时注意 `tokens.css` 单一来源约定与 style-baseline 比对工具链。
- **`articles_fts` 是 SQLite FTS5 虚拟表**：改搜索逻辑务必两种方言都验（项目已有 `sqlite_only`/`pg_only` marker 机制支持）。
- **visit_logs 启动期清理**：单实例无调度器的取舍（`main.py:138`），长期不重启的实例清理频率 = 重启频率；当前 180 天留存 + 走索引删除，量级无虞。
- 文档类：`README.md` 基线表与 `docs/TEST-REPORT.md` 是**时点快照不回填**（约定如此），判断「此刻绿不绿」只能实跑 `make check`。

### 5.4 值得肯定的做法（对比同类项目的突出优点）

- 生产门禁 fail-closed：`APP_ENV` 白名单、默认密钥拒启、多 worker 拒启、compose 必填变量 `:?` 语法——四层都在「起不来」时暴露问题而非运行中暴露。
- 中间件顺序、XFF 取最右、HEAD/Cache 顺序等**实测踩坑结论全部内嵌为注释**，且 `main.py:392` 用实测证据纠正过自己注释的错误。
- 文档-代码一致性靠机器钉住（如 `SESSION_HINT_COOKIE_NAME` 前后端一致性用例、`SITE_ARTICLE_PATH` 与前端路由表比对用例）。
- 验证分层职责清晰且不互相替代：浏览器 UI 脚本不覆盖真实 HTTP+DB 的 e2e_live，反之亦然。

### 5.5 信息不足、需要补充的内容

1. **运行时验证缺失**：本次为纯静态分析，未运行 `make check` / CDP 脚本 / e2e_live；测试通过数引用 README 与项目记忆的时点数据，不能代表当前工作区（含未提交改动）的实际状态。
2. **生产实例状态未知**：站点是否已实际部署、域名、TLS 接法、默认口令是否已改——无法从代码判断，§5.1-1 的紧迫性取决于此。
3. **未逐行审计的范围**：13 个路由模块与全部 service/repository 的实现细节、`tools/hexo_import` 与 `deploy-check.mjs` 的内部逻辑、43 个测试文件的内容——本报告对这些模块的结论基于入口/配置/核心链路抽样 + 既有审计文档（`docs/ASSESSMENT.md`、`deliverables/architecture-audit-2026-09-25.md`）。如需「每文件级」深度审计，请指定模块再深入。
4. **密钥类配置未检查**：按安全惯例未读取 `.env` 中的密钥字段值，仅核对了环境标记（APP_ENV/SEED_DEMO_DATA）；`JWT_SECRET_KEY` 是否为默认值需要另行确认（生产门禁会拦，但开发环境不拦）。

---

## 附：本次分析实际读取的关键文件清单

`README.md`、`Makefile`、`docker-compose.yml`、`backend/pyproject.toml`、`backend/src/app/{main.py, config.py}`、`backend/src/app/db/session.py`、`backend/src/app/api/{deps.py, cache.py}`、`backend/src/app/api/v1/__init__.py`、`backend/src/app/models/{__init__.py, article.py}`、`backend/src/app/utils/ratelimit.py`、`frontend/package.json`、`frontend/vite.config.ts`、`frontend/src/{main.ts, router/index.ts, api/http.ts}`、`deploy/Dockerfile.backend`，以及 alembic 版本目录、models/stores/api 目录结构、git 状态与根 `.env` 环境标记。
