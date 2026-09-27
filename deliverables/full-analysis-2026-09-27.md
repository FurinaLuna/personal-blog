# personal-blog 全面分析报告

> 分析日期：2026-09-27 ｜ 方式：**只读**静态分析 + 实测运行验证（未修改任何项目文件，`git status` 全程干净）
> 实测环境：Python 3.13.14 ／ Node v24.20.0 ／ Windows

## 实测基线（本报告结论的机器证据）

| 项 | 结果 |
|---|---|
| 后端 pytest（`--cov=app`） | **735 passed, 5 skipped, 0 failed**，6 分 31 秒；覆盖率 **83.37%**（门槛 80%，S=4096／Miss=681） |
| ruff check ／ ruff format --check | All checks passed ／ 158 files already formatted |
| import-linter 分层契约 | **2 kept, 0 broken**（101 文件 / 328 依赖） |
| 前端 vue-tsc ／ eslint | exit 0 ／ exit 0（0 error） |
| 前端 vitest | **745 passed / 46 spec**，14.16 秒 |
| 前端 vite build | 成功，dist **582 KB**（gzip ≈ 98 KB）；CSS 59.2 KB（gzip 10.8 KB） |
| 规模 | 后端 src 8,280 行 ／ tests 9,398 行；前端 21,652 行（.ts/.vue）；12 迁移 ／ 15 表 |

---

# 一、架构总览

## 1.1 目录结构与各层职责

仓库是**前后端分离 + 容器化部署**的单体应用（非微服务），顶部目录各司其职：

```
personal-blog/
├── backend/          后端（FastAPI），代码在 src/app，测试在 tests
├── frontend/         前端（Vue 3 SPA）
├── deploy/           Dockerfile ×2、nginx 配置与安全头、备份脚本
├── tools/            Node 端到端/冒烟/部署校验脚本（本地与 CI 同入口）
├── docs/             设计、模块边界、路线图、开发日志
├── deliverables/     交付物与历次审计报告
├── docker-compose.yml（默认形态）+ docker-compose.tls.yml（可选 TLS 覆盖）
└── Makefile          统一命令入口
```

后端 `backend/src/app/` 是最值得理解的部分，它是**严格的四层 + 两个底座**结构：

| 包 | 职责（单一职责表述） | 禁止事项 |
|---|---|---|
| `main.py` | 组合根：应用工厂、异常翻译、中间件与路由装配、`/health`·`/ready` 探针 | 不含业务规则 |
| `api/`·`api/v1/` | HTTP 适配层：参数声明与校验、响应模型标注、限流挂载、**写路由显式 commit** | 不 import `repositories`；不含业务规则 |
| `services/` | **业务规则唯一所在地**：可见性判定、状态流转、跨仓储编排、种子数据 | 不感知 HTTP；不写 SQL |
| `repositories/` | 数据访问：筛选/排序/分页 SQL、原子自增、存在性检查 | 不含业务规则；**只 flush 不 commit** |
| `schemas/` | DTO：请求/响应模型、`Page`/`PageParams` 值对象 | 无 IO |
| `models/` | ORM 映射与领域枚举 | 不含查询逻辑 |
| `db/` | 基础设施：`Base`、会话工厂与事务策略、SQLite 方言类型、全文索引 DDL | 不依赖 models |
| `utils/` | 横切工具：异常族、安全、文本、slug、存储、限流、日志 | **必须保持叶子**（不反向依赖业务层） |
| `config.py` | 环境配置（支持 CSV/JSON 双格式列表字段） | — |

依赖方向一句话：**箭头只能向下**（`.importlinter` 机器校验）：

```
api → services → repositories → schemas → models → db → utils → config
```

前端 `frontend/src/` 是自底向上的同构分层：

```
types（叶子）→ api → stores/utils → composables → components → layouts → views
                                        router → stores/auth
```

- `api/http.ts` 是**唯一传输层**：承载 token 存放、401 静默续期单飞锁、错误归一 `ApiError`、参数清洗；
- `composables/` 是交互单元（`useAsyncData`/`useAction`/`useConfirmDelete`/`useUpload`/`useDraftAutosave`/`useHead`/`useToast`/`useTagInput`）；
- `views/` 分公开页（13 个）与 `views/admin/`（11 个后台页），布局由 `route.meta.layout` 决定（`App.vue:21-27`）。

## 1.2 技术栈及每项技术在项目中的具体作用

**后端**

| 技术 | 在本项目中的实际作用 |
|---|---|
| FastAPI 0.141 | 路由、`Depends` 依赖图（认证/授权/限流门禁）、`UploadFile`、异常处理器、`openapi.json` |
| Starlette（传递依赖，**被直接使用**） | 两个自定义中间件的 `BaseHTTPMiddleware` 基类、`CORSMiddleware`、`StaticFiles` 挂载 `/media` |
| SQLAlchemy 2.0 + greenlet | 异步 ORM/Core；方言无关的查询构造；异步惰性加载靠 greenlet |
| aiosqlite / asyncpg | 开发默认 SQLite；生产 PostgreSQL（`pool_size=10, max_overflow=20, pool_pre_ping, pool_recycle=3600`） |
| Alembic | 生产结构变更的唯一来源（`DB_AUTO_CREATE=false`） |
| Pydantic v2 / pydantic-settings | 请求响应模型**兼**配置对象；`NoDecode` 让 CSV 形式列表环境变量可用 |
| PyJWT + bcrypt | access/refresh 双令牌、`token_version` 代次、退订令牌；bcrypt cost 12 且显式处理 72 字节上限 |
| Pillow | 上传类型真实探测（`Image.open`）、缩略图、480/800/1600 三档变体（优先 AVIF，无编码器降级 WEBP） |
| aiosmtplib | 评论/留言通知邮件（fire-and-forget，默认全关） |
| python-multipart / email-validator | 表单与文件上传；`EmailStr` 在**配置解析时**就拦住非法邮箱 |
| ruff + import-linter + pytest | 静态检查、**分层契约机器校验**、测试与 80% 覆盖率门槛 |

**前端**

| 技术 | 在本项目中的实际作用 |
|---|---|
| Vue 3.5 + TypeScript 5.7（strict） | 组合式 API + 全量类型；`vue-tsc --noEmit` 作为 `build` 的前置门禁 |
| Vite 6 | 构建与开发服务器；`manualChunks` 拆 vendor/markdown；dev 期代理 `/api`、`/media`、`/feed.xml`、`/sitemap.xml` |
| Vue Router 4（history） | 13 公开路由 + 13 后台子路由；**URL 是列表筛选状态的唯一来源**；`beforeEach` 守卫做登录态与角色门禁 |
| Pinia 2 | `auth`（会话恢复、用户）、`site`（站点档案缓存 + 兜底值）、`theme`（三态主题） |
| Tailwind 3 + `tokens.css` | 设计令牌单点定义，`darkMode: 'class'`；`maxWidth.content=700px`（中文行长） |
| axios 1.7 | 单例实例 + 请求拦截注入 Bearer + 401 重试一次 |
| marked + DOMPurify + highlight.js | Markdown 渲染 → 消毒 → DOM 后处理（标题锚点/目录/高亮/外链属性）；**全文仅 2 处 `v-html`** |
| Vitest 5 + jsdom | 745 条用例，spec 与源码同目录 |

## 1.3 主要功能模块及依赖关系

| 模块 | 职责 | 依赖 |
|---|---|---|
| 认证与用户 | 登录、刷新轮换与复用检测、`token_version` 吊销、用户 CRUD | RefreshSession/User 仓储、security、cookies |
| 文章（读写分离） | 可见性规则、定时发布、slug 唯一、版本快照、点赞 | taxonomy、series、revisions、attachments、visit_stats |
| 分类/标签 | 按名字 upsert、slug 唯一、孤儿清理 | articles（计数） |
| 系列 | 合集内有序阅读 | articles |
| 评论 | 两级树、审核、员工可见待审 | articles（共用可见性口径）、站点档案（访客开关） |
| 留言板 | 单层回复、先审后发 | notifications |
| 友情链接 | URL 唯一、排序 | `utils/url` |
| 附件/媒体 | 校验、Pillow 变体、磁盘与 DB 一致性 | `utils/storage` |
| Feed | RSS 2.0 + sitemap（根路径直出） | articles、站点档案 |
| 检索 | SQLite FTS5（bm25）或 PG pg_trgm+ILIKE，打分口径对齐 | `db/fulltext`、articles |
| 统计 | PV/UV（HMAC IP 摘要）、留存期清理 | articles |
| 版本历史 | 内容变更时快照、回滚、裁剪到 100 条 | articles |
| 通知 | 邮件、退订、退订令牌 | comments、guestbook、users |
| 站点设置 | 单行档案 + 仪表盘聚合统计 | 几乎各仓储（仅聚合） |
| 种子数据 | 幂等写入站长/档案/演示数据 | users、site、articles、links、guestbook |

依赖图**无环**，唯一的跨服务组合集中在 `services/`（文章写服务组合 taxonomy/series/revisions/query），且都是单向。

## 1.4 数据流总览（端到端）

```
Vue 组件 ──► api/*.ts ──► api/http.ts(axios 单例，内存 access token 注入 Bearer)
                                 │  401 → 单飞 refresh(POST /auth/refresh，httpOnly Cookie) → 重放一次
                                 ▼
        ┌──── 后端中间件（实测顺序，外→内）────┐
        │ RequestContext → Head → PublicCache → CORS → 路由 │
        └──────────────────────────────────────┘
                                 ▼
  路由(api/v1) ─ Depends：get_session / get_current_user|optional_user / require_admin|author / rate_limit
                                 ▼
  Service（业务规则）──► Repository（SQL，只 flush）──► Model/DB
                                 ▼
  写路由末尾显式 await session.commit()
                                 ▼
  DomainError / IntegrityError / RequestValidationError / Exception
        → main.py 统一信封 {detail, code, request_id}
```

中间件顺序是**语义性**的（`main.py:336-356` 有实测记录）：`Head` 必须在 `PublicCache` 外层，否则 ETag 会变成"空正文的哈希"；`RequestContext` 必须最外，否则被 CORS 拒掉的请求不留日志。我复核了装配结果与注释一致。

---

# 二、核心流程解析

## 2.1 启动流程（`main.py:46-83`）

`lifespan`：`storage.ensure_dirs()` →（`db_auto_create` 时）`create_all` → **无条件** `_ensure_fulltext_index()`（按方言建 FTS5 触发器或 pg_trgm+GIN，失败只降级不拦启动）→ `ensure_seed()` + commit（**失败 re-raise，应用拒绝启动**）→ `_prune_visit_logs()` → `_prune_refresh_sessions()` → yield → `engine.dispose()`。

`create_app()` 先跑 `_enforce_production_safety()`（带默认密钥/口令/DEBUG/`DB_AUTO_CREATE`/SQLite 上生产 → **拒绝启动**），再 `setup_logging`，再 `_enforce_worker_invariant()`（多 worker 会按 worker 数放大进程内限流配额 → 生产拒绝启动）。

## 2.2 后台发文/发布（写链路）

```
POST /api/v1/articles
 └─ AuthorUser = require_author → get_current_user
     decode_token(access) → UserRepository.get → is_active → payload.token_version == user.token_version
 └─ ArticleCommandService.create
     ├─ taxonomy.ensure_category / series.ensure_series
     ├─ _resolve_slug → unique_slug(exists=queries.slug_exists)
     ├─ _resolve_tags（上限 10）→ TaxonomyService.ensure_tags
     ├─ reading_time = estimate_reading_time(content_md)
     ├─ published_at = _resolve_published_at（未来时间 = 定时发布）
     └─ ArticleWriteRepository.create（add + flush，不 commit）
 └─ 路由 await session.commit()
 └─ FTS 索引由 SQLite AFTER INSERT 触发器隐式维护
```

`PATCH` 的关键设计：**先快照旧值再改**（仅当 title/summary/content 真的变化），并强制不变式"已发布 ⇒ `published_at` 非空"——代码注释记录了曾因此让"编辑器发布的文章全部不可见"的回归。

## 2.3 前台读链路

**详情** `GET /articles/{slug_or_id}`：`OptionalUser`（坏 token 静默降级为访客）→ 数字优先按 id、否则按 slug 解析 → `_can_view`（草稿非作者返回 **404 而非 403**，不暴露存在性）→ `increment_view`（原子自增）→ `_build_detail` → 路由写 `VisitLog` 并 commit。

**列表**：`PageParams`（`1<=page_size<=50`）→ `ArticleFilter(statuses=('published',), visible_at=now)` → `count()` + `list_paged()`。

**剥离策略**：不靠删字段，而是"全有或全无"的 404 + 评论待审过滤；留言/评论的读 Schema **物理上不含** `author_email`/`ip_address`/`user_agent`——这也是公开响应可安全共享缓存的前提。

## 2.4 认证与续期（前端 + 后端协同）

登录：`LoginView` → `auth.login` → `POST /auth/login` → **access token 只存内存**（刷新页面即丢），refresh token 走 httpOnly Cookie（`Path=/api/v1/auth`），另下发非 httpOnly 的 `blog_session=1` 提示 Cookie。

冷启动：受保护路由无条件尝试恢复（6 秒竞速）；公开页走 `restoreIfLikely()`，靠提示 Cookie 短路 → **匿名访客零 auth 请求**（有专门测试守住这一点）。

任意请求 401：单飞 `refreshSession()` → 重放一次；再失败才 `forceLogout()`，且**仅在当前路由需要鉴权时**才跳登录页。

后端刷新：`assert_same_origin` → 读 Cookie → decode → 用户有效 → `token_version` 一致 → 会话行存在/未吊销/未过期 → 轮换 + `mark_rotated`。登出同时 `token_version += 1` **和**吊销全部会话行，因此无状态 access token 在 120 分钟内也立即失效。

## 2.5 后台写作（草稿/上传/版本）

`ArticleEditView` 用 `loadGeneration` 计数器丢弃迟到的详情响应（防 A→B 快速切换把 A 的内容写进 B），草稿自动保存按 key 分桶、**表单就绪前禁写**、隐私模式静默失效、30 天过期；封面走 `useUpload`，正文图片粘贴后插入 Markdown；版本历史面板按需挂载、展开时才拉单条正文。

---

# 三、问题审查（按影响程度排序）

> 先说结论：**没有发现高危安全漏洞**。认证授权骨架完整（每个 admin 端点都有门禁，对象级写入在服务层复查归属，未发现越权；JWT 算法固定；500 信封不泄漏细节；`.env` 未被 git 跟踪；依赖已锁死且 CI 跑 `pip-audit`）。下表按"影响"排序，含 7 项高影响问题。

## 3.1 高影响

### H1 文章详情的 ETag 永远无法命中 304，且每次访问固定 2 次写库
- **证据（实测）**：我写了临时用例做条件请求：第一次 `GET` 拿到 ETag，第二次带 `If-None-Match` 重新校验，**返回 200 而不是 304**。
- **成因**：`api/cache.py:141-143` 对**完整响应体**取哈希生成 ETag；而 `api/v1/articles.py:138-140` 每次详情请求都 `increment_view`（自增 `view_count`，它是响应体字段 `schemas/article.py:52`）并 `INSERT` 一条 `visit_logs`。正文每次都在变 → ETag 每次都在变。
- **后果**：为公开读精心实现的 166 行缓存中间件在最热路径上**收益接近于零**；`view_count` 可被无成本刷高（无去重、无 bot 过滤、该端点无限流）；`visit_logs` 按请求量增长（只在启动时清理），仪表盘"趋势数据"可被污染。
- **波及**：`docs/ROADMAP.md` 把"3.5 加 ETag"列为未做项，实际代码已实现，但被这条写放大抵消。

### H2 归档接口 N+1 次全表扫描
- **证据**：`services/article_query_service.py:378-393` 按月循环查询，每月一个查询；`repositories/article_query_repository.py:606-617` 的谓词是列的函数：`func.substr(cast(_EFFECTIVE_DATE, String), 1, 7) == year_month`。
- **成因**：谓词作用于表达式而非裸列，任何索引都用不上；且表达式需要 `created_at`，索引里没有，必须回表取整行。
- **后果**：3 年博客 = 1 + 36 次全表扫描；端点公开、无鉴权、无限流。

### H3 任意 UPDATE 都重写 FTS 索引（把浏览/点赞计入索引写放大）
- **证据**：`db/fulltext.py:56-63` 的 `articles_fts_au` 触发器没有 `WHEN` 守卫，`UPDATE articles` 的**任何**列变更都会触发 FTS 的 `delete`+`insert` 对。
- **后果**：`increment_view`（每次浏览）与 `increment_like` 都会重新分词整篇正文。子代理在 30k 行副本上实测：0.11 ms @2 KB → 1.42 ms @30 KB → **4.73 ms @100 KB**，与正文长度线性相关，且每次浏览多一次索引写。

### H4 列表排序的 `coalesce` 让索引失效
- **证据**：`repositories/article_query_repository.py:86-101` 用 `is_top DESC, coalesce(published_at, created_at) DESC, id DESC`。
- **后果**：`coalesce` 使索引有序扫描不可用，必须对整个已发布集合做临时 B 树排序（`page_size` 上限 50 也救不了——排序发生在取页之前）。匿名流量被 `max-age=60` 部分掩盖，但**任何已登录请求都不走该缓存**。

### H5 标签筛选与标签计数缺少反向索引
- **证据**：`article_tags` 建表只有 `PRIMARY KEY (article_id, tag_id)`；`Article.tags.any(Tag.slug == ...)`（`article_query_repository.py:405-406`）与 `TagRepository.list_with_counts`（`taxonomy_repository.py:80-100`）都需要 **tag_id → article** 方向。
- **后果**：标签页与按标签筛文章都在扫 `articles`，随文章数线性变慢。

### H6 refresh token 复用窗口会滑动 → 可无限续期
- **证据**：`services/auth_service.py:147-171`。容忍期内复用会走 `mark_rotated(record, ...)`，把 `rotated_at` **重置为新时间**。
- **后果**：攻击者持有一枚已用过的 refresh token，每 <30 秒重放一次即可持续换取全新合法令牌对，"复用即吊销整个家族"的检测**永远不触发**。现有测试只覆盖单次窗口内复用与窗口外复用，没有覆盖连续滑动。
- **加重**：`POST /auth/refresh` 是**唯一没有限流的 auth 端点**，且每次调用都 `INSERT` 一行 `refresh_sessions`（清理只在启动时做）。

### H7 前端：文章页无条件下载整套 Markdown+高亮管线
- **证据（实测）**：`markdown-LnbKxlke.js` 90.2 KB（gzip 31.3）+ `markdown-Cy4LLNHW.js` 60.7 KB（gzip 18.2）= **49.5 KB gzip**，其中 `utils/markdown.ts:19-37` 静态导入 hljs core + **17 种语法**。
- **后果**：一篇纯文字文章也要先下载 49.5 KB gzip 才会渲染首段；这是全站最大的一笔可避免开销（首屏关键路径 entry+vendor+CSS 仅 85.2 KB gzip，控制得不错）。`ROADMAP.md:95` 自己把它列为未做的 3.2。

## 3.2 中影响

| # | 问题 | 位置与后果 |
|---|---|---|
| M1 | 媒体库上传**串行** | `MediaView.vue:42-53` 逐个 `await`；12 张图耗时=累加，一张卡住全队阻塞，且失败数量不上报 |
| M2 | 前端**完全没有请求取消** | 全库 0 处 `AbortController`；`useAsyncData` 只丢弃过期**写入**，请求仍在飞行。快速点筛选会积压请求抢占 HTTP/1.1 的 6 连接预算；无响应缓存，来回导航重复取数 |
| M3 | 测试环境 jsdom 重复创建 | vitest 自己报告：46 个 spec 环境各建一次 jsdom，**116 秒 = 66% 的测试耗时** |
| M4 | FTS 触发器 DDL 两处重复且不检漂移 | `db/fulltext.py:30-64` 与 `migrations/20260917_1910`。都用 `IF NOT EXISTS`，触发器**体**改了不会更新已存在的库 |
| M5 | `IntegrityError` 一律报"内容已存在" | `main.py:181-203`。非唯一键冲突（NOT NULL、外键）也被答成"该内容已存在"，把排障引向错误方向 |
| M6 | 事务边界定义了两遍 | 文档规定"只有路由 commit"，但 `services/auth_service.py:101,172` 自己 commit（`:154` 还在抛异常前 commit）。今天能跑，但让服务层不再是可复用单元，也让路由级 try/rollback 无法撤销 |
| M7 | 检索排序公式重复两份 | `article_query_repository.py:160-200` 用 SQLAlchemy 表达式，`:286-296` 又用 f-string 原始 SQL 重写同一套分档/衰减；模块 docstring 自己记着"两路必须排序一致"的历史 bug |
| M8 | 后台列表静默丢筛选 | `article_query_service.py:187-203`：admin 分支构造 `ArticleFilter` 时**漏了 `category_slug`**，于是 `?category=<slug>` 返回全部文章且不报错 |
| M9 | `social_links[].url` / `avatar_url` 未做协议校验 | `schemas/site.py:10-13,37`。它是**唯一**绕过 `normalize_http_url` 的用户可控 URL，而前端 `SiteFooter.vue:38`、`AboutView.vue:56` 直接绑到 `:href`（Vue 不清洗动态 href）。写入需 admin 令牌，且参考部署的 CSP 会挡住 `javascript:`，但裸机部署无此保护——这是全项目唯一残留的存储型 XSS 类别 |
| M10 | 后台留言的 `author_site` 未做前端兜底 | `views/admin/GuestbookView.vue:182` 直接绑 `:href`，而公开页与评论区都用 `safeExternalUrl` 兜底（理由写在 `CommentSection.vue:149-151`：校验是后加的，存量数据可能有脏值）。偏偏最高权限会话没兜 |
| M11 | 匿名可写端点无限流 | `POST /notifications/unsubscribe`、`/auth/refresh`、`/attachments/upload`（无配额）、以及 `/health`·`/feed.xml`·`/sitemap.xml` 无限流且每次现算 |
| M12 | 死代码 9 处 | 含 `CommentRepository.count_by_article`——**本应用于列表批量化计数的函数没人用**，列表改用逐行相关子查询 |

## 3.3 低影响

- **架构不变量被自己破坏**：三处"硬约束"（服务层 commit／写路由显式 commit／仓储只 flush）彼此矛盾，是 M6 的根。
- **`tempfile.gettempdir()` 在本机返回工作目录**：测试媒体的上传文件与 pytest 临时件被写进**仓库内部**（我实测产生了 `backend/personal_blog_media/`、`backend/pytest-of-zhangchaowang/`），且未 `.gitignore`，`git add -A` 会入库用户上传物。
- **本机 5 个测试失败是环境问题**（已定位，非代码缺陷）：这些失败**只出现在本机**，SQLite 无法分配磁盘临时文件，凡需要 spill 的语句（级联删除等）都报 `unable to open database file`。加 `PRAGMA temp_store=MEMORY` 后 **735 passed / 0 failed**。修复位置：`db/session.py:29-35` 的 `enable_sqlite_pragmas`。
- **`lint-imports` 在 Windows 上默认失败**：`.importlinter` 含中文，默认编码（GBK）解码报错。设 `PYTHONUTF8=1` 即通过（Makefile 未设）。
- **文档漂移**：`ROADMAP.md:52` 声称"无 ETag / 缓存"，实际已实现；测试数、覆盖率（文档 82.96% vs 实测 83.37%）、前端用例数（文档 713 vs 实测 745）均已漂移；`utils/markdown.ts:113-114` 的注释与实测行为不符（`data:` URI 在 `<img src>` 上实际被放行）。
- **`content_md` 无长度上限**：`schemas/article.py:120,161`，且列表接口会 `select(Article)` 取整行（含正文）再由 `ArticleSummary` 丢弃——文档与代码相反。
- **`/docs` 生产已关但 `/api/v1/openapi.json` 仍可匿名获取**；裸机部署时应用自身不注入任何安全头（只在 nginx 里配）。
- **前端零散项**：`useAction` 把成功回调放在 try 内（UI 后置副作用抛错会被报成"操作失败"）；`ArticleDetailView.vue:115-132` 仍用 `window.confirm`；`<th>` 缺 `scope`；错误 toast 走 polite 而非 assertive；`useAsyncData`/`MarkdownRenderer`/`MarkdownEditor`/`theme`/`prefetch` 无 spec；18 个组件无 spec；`/search` 声称 `noindex` 但全库 0 处 robots。

---

# 四、优化建议与规划

## 4.1 逐问题的可操作建议

| 问题 | 修改位置与方案 |
|---|---|
| **H1** | 二选一：① 把 `view_count` 移出可缓存表示（详情单独提供计数端点，或对缓存路径不返回该字段）；② 计数改为**后台任务 + 自有会话**（照 `notification_service._spawn` 的写法）+ 按 `(article_id, ip_hash, 日期)` 去重 + 60 秒进程内记忆。同时给详情加 `rate_limit("article_view", 60, 60)` 兜底 |
| **H2** | 新增迁移加 `published_ym`（String(7)，写路径填充；或 PG 表达式索引 / SQLite 生成列），把按月谓词从"列的函数"改成裸列比较；或改为一次 `GROUP BY ym` + 窗口函数取每月前 N |
| **H3** | 给 `articles_fts_au` 加 `WHEN new.title IS NOT old.title OR new.summary IS NOT old.summary OR new.content_md IS NOT old.content_md`，**两处 DDL 都要改**（`db/fulltext.py` + 新迁移里 DROP/重建触发器） |
| **H4** | 回填 `published_at` 使已发布行永不为 NULL，然后去掉列表排序里的 `coalesce`；或补 `ix_articles_top_effective (is_top, coalesce(...), id)` |
| **H5** | 模型 + 迁移加 `Index("ix_article_tags_tag_id", "tag_id", "article_id")`，并在初始迁移里同步，保证新装库也一致 |
| **H6** | `auth_service.py:147-171` 不再推进 `rotated_at`：新增 `first_rotated_at` 列并只与它比较；仅在"替换者仍未被使用"时容忍；同一已轮换令牌第二次出现即吊销整个家族 |
| **H7** | 把 `utils/markdown.ts` 的 hljs 语法注册改为按需动态 `import()`（先渲染纯文本，语法块到达后升级），或用 `manualChunks(id)` 函数修掉两个同名 `markdown-*` 分包的命名冲突；CI 里加"无代码块文章不得加载超过 X KB"的体积预算断言 |
| **M1** | `MediaView.vue` 用固定并发（3）的 worker pool，汇总"成功 N / 失败 M"，列表只刷新一次 |
| **M2** | `useAsyncData` 接受可选 `signal`，`run()` 开头 abort 上一次，`onBeforeUnmount` 调用 `cancel()`；`api/http.ts` 把 `AxiosRequestConfig['signal']` 透传下去 |
| **M3** | `vitest.config.ts` 设 `pool: 'vmThreads'` 或 `isolate: false` |
| **M4** | 加测试断言 `sqlite_master.sql` 里三个触发器与常量逐字相等（防漂移），或给触发器名加版本号 |
| **M5** | `main.py:181-203` 先判断 `exc.orig` 是否唯一约束违反（`UNIQUE constraint failed` / SQLSTATE `23505`），是才 409，否则交给 500 信封 |
| **M6** | 删掉 `auth_service` 的两处 commit，改由 `api/v1/auth.py` 显式 commit；`:154` 那处保留但抽成专门方法并写明"此写必须活过 401" |
| **M7** | 抽出共享的 `_SCORE_SQL`/`_DECAY_SQL` 片段供两条路径复用；加"同一 fixture 下两路排序一致"的测试 |
| **M8** | `article_query_service.py:187-203` 两个分支都补 `category_slug=flt.category_slug`；加 `?category=<slug>` 的后台列表用例 |
| **M9** | `schemas/site.py` 给 `SocialLink.url` 与 `avatar_url` 加 `field_validator` 走 `normalize_http_url`；前端两处用现成的 `safeExternalUrl` 兜底存量数据 |
| **M10** | `admin/GuestbookView.vue:182` 改用 `safeExternalUrl` + `v-if` |
| **M11** | 匿名写端点补限流；`/feed.xml`·`/sitemap.xml` 加基于 `max(updated_at)` 的 ETag + 60 秒进程内 TTL 缓存 |
| **M12** | 删除死代码；把 `count_by_article` 接进列表路径，替换逐行相关子查询 |
| **环境缺陷** | `db/session.py:29-35` 补 `PRAGMA temp_store=MEMORY`（本机可让 5 个失败转绿）；Makefile 的 lint 目标加 `PYTHONUTF8=1`；测试夹具不再依赖 `tempfile.gettempdir()`，改用 `tmp_path_factory`，并把测试媒体目录与 `backend/storage/uploads|avatars/*` 加进 `.gitignore` |
| **文档** | 修正 `ROADMAP.md` 的 ETag/测试数/覆盖率漂移；修正 `utils/markdown.ts:113-114` 与 `article_query_repository.py:441-443` 与实现不符的注释 |

## 4.2 功能提升方向

1. **内容协作**：文章草稿的多人协作与评论审核工作流（当前 `author` 与 `admin` 权限梯度较粗，标签删除这一处已出现权限不对称：`DELETE /tags/{id}` 是 author 级，而 `DELETE /categories/{id}` 是 admin 级，且服务层不检查引用归属）。
2. **检索升级**：文章过百后把 FTS5/LIKE 换成 PG `tsvector`+GIN 或 Meilisearch；顺带解决"纯数字 slug 会被 id 解析抢走"（`article_query_service.py:327-333`）这一真实歧义。
3. **多副本能力**：限流是进程内计数、SQLite 单机、启动期做建表与种子——要横向扩容必须先换 PG + 共享限流存储（`config.py:433-481` 已把这条代价写清楚）。
4. **可观测性**：`/ready` 已报检索索引状态，可再接轻量指标端点（P95、缓存命中率、写放大计数），让 H1/H3 的收益可量化。
5. **前端体验**：首页三请求合并为一个聚合接口（ROADMAP 3.4）；归档页按年折叠懒加载（3.7）；图片补 `width/height` 消除 CLS。
6. **媒体能力**：上传配额与按用户限流、SVG 走独立域名托管（当前刻意不在白名单，是对的）。

## 4.3 下一步行动计划（按优先级）

**第 0 批 · 半天内，低风险高确定性**
1. `social_links`/`avatar_url` 协议校验 + 前端两处 `safeExternalUrl` 兜底（M9/M10，约 10 行，关闭唯一残留的存储型 XSS 类别）
2. `db/session.py` 加 `PRAGMA temp_store=MEMORY`；Makefile lint 目标加 `PYTHONUTF8=1`（消除本机 5 个假失败与 lint 假失败）
3. `.gitignore` 补测试产物与 `storage/uploads|avatars/*`
4. `main.py` 的 `IntegrityError` 分类（M5）

**第 1 批 · 1～2 天，结构性收益**
5. H1：把 `view_count`/`visit_logs` 移出可缓存表示或改后台异步计数 —— 让已实现的 ETag 真正生效
6. H3：FTS 触发器 `WHEN` 守卫（一次迁移，去掉每次浏览的索引重写）
7. H6 + `/auth/refresh` 限流（唯一能让已持有令牌者无限延长期限的一类）
8. M8 后台筛选丢参修复（含回归用例）

**第 2 批 · 一周内，索引与查询形状**
9. H2 归档一次查询化 + `published_ym`
10. H5 `article_tags` 反向索引；H4 排序去 `coalesce`
11. M6 事务边界收敛到一处；M7 排序公式去重；M12 死代码清理
12. 前端 H7（hljs 按需加载）+ M1（上传并发）+ M2/M3（取消与 jsdom 复用）

**第 3 批 · 两周内**
13. 上传配额与限流、`/feed.xml`·`/sitemap.xml` 缓存与 ETag
14. 补 `useAsyncData`/`MarkdownRenderer`/`MarkdownEditor` 的 spec（它们是风险最集中却无测试的三处）
15. 文档校正（ROADMAP 漂移、与实际不符的注释）

---

## 附：验证方式说明

本报告所有结论分三类来源，已在正文标注：
- **实测**：我在本机真实运行 pytest／vitest／vue-tsc／eslint／vite build／import-linter／ruff，以及两个临时探针（ETag 304 探针、temp-store 探针）。
- **代码取证**：逐文件阅读后端 `api/services/repositories/db/utils` 与前端 `api/composables/stores/router/utils` 关键路径，并核对中间件真实装配顺序、端点门禁矩阵。
- **需二次确认**：子代理在 30k 行内存副本上给出的查询计划与耗时（SQLite 上测得，PG 上需按方言复核）；依赖 CVE 的版本核对（本机无网络凭据）。

两个临时探针文件与测试产物**已全部删除**，`git status` 干净，未改动任何源码或配置。
