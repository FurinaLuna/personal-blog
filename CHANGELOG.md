# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的组织方式。

> 说明：1.0.0 之前的提交是项目从零建成的连续过程，这里按主题归并，
> 逐条的决策与踩坑记录见 [`docs/devlog/`](docs/devlog/)。

## [Unreleased]

> 本节按 Keep a Changelog 归类：**新增**（新能力）、**已变更**（行为或契约变了）、
> **已修复**（原来的行为是错的）。2026-09-22 的四轮改动与更早的条目合并在一起，
> 逐条决策见 [`docs/devlog/2026-09-22.md`](docs/devlog/2026-09-22.md)。

### 新增

- **CI 新增 `e2e-live` 作业**：在 `ubuntu-latest` 上跑 `tools/e2e_live/e2e_run.py`
  的 SQLite 方言全套 62 条用例（自起 uvicorn、自建临时库、自清理，
  不需要任何 service），失败时上传逐用例报告；此前这套脚本从未进过流水线
- **CI 新增 `backend-postgres` 作业**：整套 pytest 跑在真 PostgreSQL 上
  （service 容器），并在 PG 上执行 `upgrade head → downgrade base → upgrade head`。
  这条路径此前**从未被执行过**——生产方言的迁移、`pg_trgm` 分支、外键级联
  全都只有 SQLite 的验证
- `tools/lib/chrome.mjs`：三个浏览器脚本共用的浏览器探测与启动参数
  （环境变量覆盖 → 平台候选路径 → `PATH` 查找；失败时打印全部尝试过的位置；
  含容器/CI 需要的 `--disable-dev-shm-usage`，以及**仅在 root 下**追加的
  `--no-sandbox`——不无条件削弱沙箱）
- `make e2e-live`：本地一条命令跑同一套端到端用例
- **测试用例可按方言互斥**：新增 `sqlite_only` / `pg_only` 两个 marker，
  由 `conftest.py` 在另一条方言上自动 skip 并写明原因（不假装通过）
- `tests/conftest.py` 支持 `TEST_DATABASE_URL` 切换测试库方言；
  PG 上改用 `create_all + TRUNCATE ... RESTART IDENTITY`（每例重建 11 张表
  的 DDL 要 1.6s，442 例就是十几分钟）
- `tests/test_password_threading.py`：以**行为**而非实现断言 bcrypt 不阻塞
  事件循环（同步实现必然失败：实测事件循环调度 0 次）
- `tests/test_search_indexes.py`：方言分流 + 索引定义 + `ILIKE` 可用性
  （`enable_seqscan = off` 下验证计划里出现 trgm 索引）
- `frontend/src/utils/markdown.spec.ts` 补 13 例：覆盖式钓鱼（工具类 / `id` /
  密码框）、协议相对外链的 `rel`、站内链接不带 `target`、任务列表复选框仍在
  （安全加固最容易顺手把正常功能一起修坏，这条是护栏）
- `frontend/src/router/guard.spec.ts` 补 4 例、`frontend/src/api/http.spec.ts`
  补 4 例：公开页面不阻塞导航、恢复请求挂起超时后放行、后台内部 404 留在后台布局、
  凭证被判死时通知订阅者、匿名 401 不被说成"登录已过期"
- 媒体库上传进度条（`role="progressbar"`）：`attachmentApi.upload` 早就支持
  `onProgress`，但此前没有任何调用方传过

### 已变更

- `useAction` 的 `success` 支持传函数（成功那一刻才求值）。原因是一个真实缺陷：
  `success: \`欢迎回来，${auth.displayName}\`` 是**调用前**求值的，
  而 `displayName` 要等登录成功才有值 —— 登录成功却提示"欢迎回来，访客"
- 路由守卫不再对公开页面 `await auth.restore()`：身份恢复只影响顶栏显示，
  不该阻塞内容渲染；需要登录的页面仍会等待，但**加上限**（6s）
- `forceLogout()` 只在**当前路由需要登录**时才整页跳转（探针由 router 注入，
  避免 `router → stores/auth → api/http` 的循环依赖）；公开页面只清凭证，
  并通过 `onCredentialsCleared` 通知 store 清掉内存里的 `user`
- 上传单独使用 120s 超时（全局 20s 是给 JSON 接口定的：5MB 封面图在 2Mbps
  上行需要约 20 秒，必然超时）
- 后端引入 import-linter 分层契约（`backend/.importlinter`），
  模块依赖方向机器可验证，纳入 `make lint`；
  seed 迁出 db 包、`BaseRepository` 收敛存在性检查、分页值对象统一下沉
- 前端新增 `useConfirmDelete` / `useUpload` / `useTagInput` 组合式函数，
  消除 5 处删除确认与 3 处上传的重复实现；types 按领域拆分
- 新增 `tools/showcase-demo.mjs`：用户视角完整旅程回放（11 步截图断言）
- 新增「文章系列 / 合集」：系列模型与 API、后台管理、详情页系列导航（上下篇）
- 新增图片多尺寸变体：上传时生成 480/800/1600 三档（优先 AVIF，无编码器降级 WEBP），
  列表封面与详情页用 `srcset` 响应式选图；媒体库支持为存量图片补生成
- 新增访问趋势统计：详情访问按天落 `visit_logs`（IP 只存 HMAC 摘要），
  仪表盘出近 30 天 PV/UV 曲线
- 新增评论邮件通知：有人回复你的评论时发信（含退订链接），新评论提醒站长；
  SMTP 默认关闭，发信失败只记日志，绝不影响评论接口
- 端到端脚本的截图与报告产物统一收敛到 `shots/`（gitignore），
  Makefile 三个验证目标同步指向新目录，根目录不再散落多个 `*-shots/`
- 测试基线更新：后端 275 例、前端 86 例（Vitest）
- 整理仓库结构：删除与 `backend/storage/` 重复的根目录空 `storage/`；
  `docs/OPTIMIZE-quick-wins.md` → `docs/OPTIMIZE-QUICK-WINS.md`（与同目录全大写命名对齐），
  中文名测试报告改英文 kebab-case；
  `.gitignore` 补 `.import_linter_cache/` `backend/uv.lock` `.trae/`；
  `Makefile clean` 补齐缓存目录清理

- 新增**文章版本历史**：内容（标题/摘要/正文）真正变化时自动存快照，
  支持查看与一键恢复；恢复前会先为当前内容留一版，恢复错了可以退回
- 新增**定时发布**：`published_at` 传未来时间即为排期，到点自动可见，
  不需要任何定时任务；后台列表标记「定时发布」并提示
- 搜索升级为 **SQLite FTS5 全文检索**（trigram 分词 + bm25 相关度排序），
  两字以内的中文查询退回 LIKE 兜底；新增独立搜索页与「相关度 + 时间衰减」排序
- 新增 **canonical 与 JSON-LD 结构化数据**（BlogPosting / WebSite + Person），
  补齐 `og:site_name` / `og:locale` / twitter 卡片
- 详情页显示「修订于」（内容确实改过时才出现，阈值 1 天）
- 前端接入 **ESLint**（定位为抓 bug 与无障碍，不管排版风格），
  纳入 `make check` 与 CI
- CI 新增 **e2e job**：起前后端 + 真实浏览器跑 `smoke` 与 `interaction`，
  失败时上传截图与双份服务日志
- 新增 `backend/scripts/backup_db.py`（`VACUUM INTO` 一致性备份 + 轮转）
  与 `backend/scripts/lint_imports.py`（跨平台分层契约检查）
- `visit_logs` 增加留存清理（180 天，启动时自动执行，另有站长端可按需触发）
- 新增 `tools/e2e_live/e2e_run.py`：真实环境端到端（62 条用例）。临时空库走
  `alembic upgrade head` 建表（与生产同路径，而非 ORM 的 `create_all`）、
  独立 uvicorn 进程与独立 storage 目录，断言一律 HTTP + 直连 sqlite 二次校验，
  首尾比对 `backend/blog.db` 指纹确保零污染；`probe.py` 用于定点复现单个 500
- 前端补齐两处此前完全无测试的收口：`api/http.ts`（错误归一化 / 401 静默续期的
  单飞锁与重放 / 强制登出，12 例）与 `router/index.ts` 的后台守卫
  （redirect 保留 query、角色不足回首页、网络抖动不缓存成未登录，9 例）
- 前端补组件层测试（此前 18 个 spec 里只有 1 个组件测试）：
  `Pagination` 10 例（页码窗口与省略号、首尾禁用、区间文案端点）、
  `ConfirmDialog` 9 例（aria-modal 的四条无障碍契约：焦点进出 / Tab 循环 /
  ESC 取消 / 标题关联，外加锁滚动与 loading 禁用）、
  `CommentSection` 13 例（空值与游客昵称拦截必须拦在发请求之前、提交数据 trim
  与空值转 null、回复带 parent_id、`javascript:` 站点地址不渲染成链接、
  正文纯文本渲染、待审核与站长徽标）
- 测试基线更新：后端 **442** 例、前端 **200** 例
- `tools/e2e_live/e2e_run.py` 打通 **PostgreSQL 方言**：`E2E_DB=postgres` 时
  后端跑在真实容器里、库为独立的 `blog_e2e`（61 条用例，X01 跳过）。
  宿主机没有 psycopg 驱动，读取侧经 `docker exec psql` 取数，因此补了一整套
  结果解析：NULL 用 `-P null=` 哨兵还原（psql 默认打成空串，与空文本无法区分）、
  记录分隔符替换（正文自带换行会把一条记录撕成多行）、末尾换行只删一个、
  剔除 `(N rows)` 行数脚注、数值与布尔类型还原。
  这条方言是 SQLite 验不到的防线——**SQLite 默认不开外键，级联行为只有 PG 能验证**

### 已修复

- **正文可以伪造全站浮层（覆盖式钓鱼）**：消毒器禁了 `style`（注释里写明理由是
  "内联 style 可以做覆盖式钓鱼"），却把 `class` 整体放行 —— 而
  `fixed inset-0 z-50 bg-white` 正是本站 `ConfirmDialog` 在用的工具类，
  作者在正文里写这几个词就能盖住整个页面，配一个 `<input type="password">`
  就是站内钓鱼页。现改为：`class` 只保留 marked 生成的 `language-*`
  （代码高亮需要），`id` 直接不放行，`<input>` 只保留任务列表的复选框
  （`type="password"` / `type="text"` 整段删除）
- **协议相对外链漏了 `rel`**：外链判定用的是 `/^https?:\/\//` 正则，
  `<a href="//evil.com">` 不匹配 → 拿不到 `noopener`，但它照样开新窗口、
  照样能通过 `window.opener` 反向操作本页。现改用 `new URL(href, origin)`
  判同源；顺带把站内链接上作者手写的 `target="_blank"` 去掉（目录/相关文章
  不该开新标签页）
- **路由守卫把「网络抖动」当成「你没登录」**：`/auth/me` 失败（非 401）时
  `restored` 保持 false，而守卫只要 `isAuthenticated` 为假就跳登录页 ——
  网络一抖，已登录的作者就"被登出"了。现区分两种"不知道"：401 是确定答案
  （跳登录页），网络失败/超时是未确定（**放行**，让页面表达失败，下次导航再试）
- **公开页面被 401 整页踢去登录页**：`forceLogout()` 此前无条件
  `window.location.replace('/login')`，访客正在读文章时任何一个后台请求
  （如未读统计）401 就会把他弹走
- **有 access 无 refresh 时落进 401 死局**：该分支条件不成立，于是既不续期
  也不清凭证，表现为"守卫放行、请求全 401、页面永远空白"。现按"凭证已死"
  处理（刻意带 `tokenStore.access` 判断：完全匿名的 401 仍是普通未授权）
- **顶栏显示已登录、请求全 401**：http 层只清 token，内存里的 `user` 还在，
  而公开页面又不再整页跳转 —— "没登录却显示昵称头像"会一直挂着
- **后台 404 掉回前台布局**：`/admin/typo` 落到顶层兜底路由（没有 layout），
  侧边栏消失、用户以为被登出。现后台内部有自己的兜底路由，保留 admin 布局
- **上传没有进度、且用 20 秒超时**：见上一条"已变更"里的超时说明；
  进度条此前完全没接线
- **PostgreSQL 上的搜索是「假加速」**：`articles_fts` 是 SQLite 专有虚拟表，
  非 SQLite 方言上 `fulltext_available()` 恒为 False，于是生产库
  （compose 用 postgres:16）的搜索永远是 `title/summary/content_md ILIKE '%kw%'`
  三列**全表扫描** + 同条件 COUNT。现补 `pg_trgm` 扩展与 3 条 GIN
  （`gin_trgm_ops`）索引，迁移 `a7c1e9d4b2f8`；实测 5 万篇语料下
  7 字关键词从 181ms 降到 6.6ms，3 字以内 PG 仍选顺序扫描（三元组索引的
  固有粒度限制，与 FTS5 trigram 同一条限制，已写进文档不夸大）
- **搜索端点无限流**：它是唯一"一次请求 = 一次全表扫描 + 一次 COUNT"的读接口，
  可被脚本廉价放大；现 30 次/分（真人不可能触发）
- **bcrypt 阻塞事件循环**：`hash_password` / `verify_password` 是同步 CPU 调用
  （cost=12，实测 185ms/次），而部署是单 worker —— 一次登录能让所有并发请求
  排队 185ms。现业务路径统一走 `asyncio.to_thread` 包装，
  实测哈希期间事件循环从「0 次调度」变为「13 次调度」
- **测试数据写超列长度**：`test_stats.py` 造 `ip_hash` 用了 65 字符而列是
  `varchar(64)` —— SQLite 不校验长度所以长期没暴露，PG 上直接
  `StringDataRightTruncationError`
- **CI 里的端到端作业从来不可能通过**：`tools/interaction-check.mjs` 把
  Chrome 路径写死成 `C:/Program Files/Google/Chrome/Application/chrome.exe`，
  而该作业跑在 `ubuntu-latest` 上 —— 于是「交互验证」这一步必然失败，
  它后面的「全功能回归」（41 项，本仓库最强的浏览器防线）因步骤中断**从未被执行**
- **`tools/e2e_live/e2e_run.py` 写死 Windows 解释器**：`PY` 曾是
  `backend/.venv/Scripts/python.exe`，Linux 上直接 `FileNotFoundError`，
  导致 62 条端到端用例只能在作者本机运行；现改为 `sys.executable`
- **CI 的 pip 缓存键指向错误的文件**：`cache-dependency-path` 写的是
  `backend/pyproject.toml`，而实际安装的是 `requirements.lock`，
  锁文件更新时缓存不会失效（三个 job 全部修正）
- **覆盖率没有门槛**：`--cov` 只在本地手工跑过，CI 里 `pytest -q` 不带覆盖率，
  82% 这个数字没有任何机制阻止它下滑；现于 `pyproject.toml` 设
  `[tool.coverage.report] fail_under = 80`，CI 与 `make test-cov` 都带上 `--cov`
- **README 数字与实物不一致**：模型表数「10 张」（目录树）/「12 张业务表」
  （基线表）实为 **11 张**；迁移 8 条实为 9 条；测试基线 442 已过时
  （现为 SQLite 452 passed / 5 skipped、PostgreSQL 456 passed / 1 skipped）
- 顺手修掉 `stores/auth.ts` 里 `defineStore(...) {` 与下一行粘连的排版

- **`ADMIN_EMAIL` 不校验导致运行时静默半残**：seed 写入时不校验邮箱，
  读取时才被 `SiteProfileRead` / `UserRead` 的 `EmailStr` 拦下 →
  `/site/profile` 与 `/auth/me` 一律 500，而服务照常启动且无任何告警
  （`ADMIN_EMAIL` 填 `admin@e2e.test` 这类 RFC 保留域即触发）；
  现 `admin_email` 本身就是 `EmailStr`，非法值在**解析配置时**就拒绝启动，
  与生产安全门禁同一取舍（默认值 `admin@example.com` 仍可正常启动）
- **缺少兜底 500 处理器**：任何未捕获异常此前会落到 Starlette 默认处理器，
  返回纯文本 `Internal Server Error` —— 前端无法归一化文案，运维拿不到
  `request_id` 对日志；现统一返回 `{"detail": "服务器内部错误", "code":
  "internal_error", "request_id": ...}`，细节只进日志
- **三级评论被接受却永远不可见**：回复一条二级回复会返回 201，
  但两级评论树（只查根 + 一层 replies）永远查不到它，用户以为回复成功、
  内容实际丢失；现直接拒绝并返回 400「仅支持两级评论，请直接回复根评论」
- **登出不吊销令牌**：`POST /auth/logout` 只回一句语义消息、服务端不做任何事，
  旧 access token（120 分钟）与 refresh token（7 天）仍可访问受保护资源；
  现复用令牌代次机制（`token_version += 1`）真正吊销，代价是**所有设备**
  同时登出——个人博客规模下这是可接受的取舍
- **登录成功后不跳转**：`auth` store 的 `login()` 成功时返回 `undefined`，
  被 `LoginView` 的 `ok === undefined` 误判为失败，用户卡在登录页；
  现改为成功返回 user 对象，并新增回归测试
- **评论审核越权**：`PATCH /comments/{id}` 与后台审核列表缺文章归属校验，
  任何作者都能放行/撤下并翻看别人文章下的评论；现与删除接口同口径
  （作者只管自己文章，站长不受限），并补回归测试
- **归档文章仍可点赞**：现仅已发布文章接受点赞（与浏览计数「仅已发布累计」
  同口径），前端详情页对非已发布文章不再渲染点赞按钮
- **站点设置无法清空可空字段**：headline / email / location / icp 等显式传
  null 现在真正清空；同时修复反向问题——布尔开关显式传 null 会把 None 写进
  非空约束列导致 500，现在布尔收到 null 一律视为「不修改」
- **标签列表 min_count 与 limit 冲突**：min_count 过滤前移到 SQL HAVING，
  截断发生在过滤之后，语义即「计数达标的前 N 个」，limit 名额不再被空标签占用
- **文章详情 GET 缺显式提交**：浏览计数自增补上显式 commit，与其他写路由
  同口径，规避依赖会话收尾提交的写后读竞态
- **前端计数格式化无空值防护**：`formatCount` / `formatBytes` /
  `formatReadingTime` / `formatYearMonth` 对 null / undefined / NaN 兜底，
  界面不再渲染出 "null" / "NaN"，新增 `format.spec.ts`
- 后端 3 个文件补齐 ruff format（series 提交时 CI 受账号账单锁影响而漏检）
- **Alembic 迁移与 ORM 建表索引漂移**：`create_notification_opt_outs` 迁移缺
  `(email, article_id)` 复合唯一索引，导致「`alembic upgrade` 建库」与「ORM 建表」
  两条路径产出的索引不一致、唯一约束实际失效；已在迁移中补齐并验证两条路径一致

- **`X-Forwarded-For` 取值端写反导致限流可绕过 / 或全站被一个人锁死**：
  取的是最左一跳（客户端可随意伪造），而 nginx 的 `$proxy_add_x_forwarded_for`
  是**追加**语义、可信跳在最右；现优先取 `X-Real-IP`，否则取最右一跳
- **生产环境缺少启动门禁**：用仓库里公开的默认 JWT 密钥 / 管理员口令
  也能正常启动并对外服务；现 `APP_ENV=production` 时逐项校验并拒绝启动
- **评论「网站」字段未校验协议**：前端直接绑到 `:href` 而 Vue 不清洗动态 href，
  提交 `javascript:` 可存储并触发；现后端限定 http/https，前端兜底存量数据
- **文章间跳转后目录高亮永久失效**：`IntersectionObserver` 只在 `onMounted` 建立，
  而组件实例被复用；现随目录数据变化重建
- **阅读量/点赞自增会改 `updated_at`**：列上的 `onupdate` 对 Core `update()`
  同样生效，导致后台 `sort=updated` 变成「最近被看过的」、sitemap 的
  `<lastmod>` 每次访问都变
- **删光演示文章后重启应用起不来**：`seed_demo_content` 只按文章数判断，
  而分类/标签的 name 是唯一键，重复插入触发 `IntegrityError` 并冒到 lifespan
- **草稿与未到发布时间的文章的评论可被匿名读取**：读路径漏了可见性检查，
  与详情页的 404 口径不一致
- **搜索分页失效**：`total` 取的是本页条数，命中 12 条时显示 5 条，
  前端据此判断「没有下一页」，第 6 条之后永远打不开；现为真实 COUNT + LIMIT/OFFSET
- **搜索排序随查询字数变化**：短查询走列表接口因而「置顶优先」，
  搜「数据」第一篇是只在正文顺带提及的置顶文章；现两条路径共用同一套排序语义
- **前端静默失败面**：仪表盘 KPI 与用户列表加载失败时永远停在骨架屏/空表；
  瞬时网络错误被永久缓存成「未登录」；主题 store 裸访问 `localStorage`
  可能让整页空白
- **无障碍**：`ConfirmDialog` / `MobileToc` 声明了 `aria-modal` 却不移动焦点、
  不做 Tab 陷阱、移动目录从未锁定滚动；13 处表单控件只有 placeholder 没有可访问名称
- **编辑器默认模式下每敲一个字跑整条 Markdown 渲染管线**（`v-show` 仍渲染子树）
- `IntegrityError` 未翻译，并发写入冲突返回 500 而非 409
- `articles_fts` 是虚拟表，`Base.metadata.create_all` 不会创建它，
  而 autogenerate 会生成 `DROP TABLE articles_fts` —— 现补建 + 探测兜底 +
  `include_object` 护栏
- **上传白名单是死配置**：`settings.allowed_image_types` / `allowed_file_types`
  全仓零引用，真实白名单硬编码在 `attachment_service.py`，配了环境变量也不生效。
  现服务层每次调用重读配置（在 import 期固化成常量的话，环境变量就再也改不动）。
  同时修掉**单位不一致**：非图片附件实际按**扩展名**判定（没有可靠的内容嗅探，
  `Content-Type` 由客户端提供、可伪造），而默认值写的是 MIME，
  `application/pdf` 这类永远匹配不上；默认值统一改成扩展名并补 `image/bmp`
  （服务层本就允许、配置里却漏了）。常量表更名 `IMAGE_FORMAT_TABLE` 并补
  TIFF / ICO 两档，运维才有得可配
- **限流在并发下丢计数**：限流依赖是同步的、会被 FastAPI 放进线程池并发执行，
  而 `bucket.count += 1` 是读-改-写三步，GIL 只保证单条字节码原子，
  两个线程读到同一个旧值各自写回就会吞掉计数，配额形同放大；
  现整个「取桶 → 可能重置窗口 → 自增 → 判定」放进 `threading.Lock`
- **强制登出被执行两次**：`http.ts` 中「续期成功但重放仍 401」这条路径，
  内层拦截器判定凭证已死先调用一次 `forceLogout()`，异常冒泡到外层 `catch`
  又调用一次，`tokenStore.clear()` 与 `location.replace()` 重复触发；
  现以「凭证是否已清空」做幂等闸门
- **`articles.series_order` 缺 `server_default`**：只有 ORM 的 `default=0`，
  导致 `create_all` 建出的列没有默认值，与 Alembic 建库结构不一致，
  不含该列的裸 INSERT 会 NOT NULL 失败。SQLite 的 ALTER COLUMN 改不了默认值，
  故不新增迁移、只在模型侧对齐（迁移漂移至此清零）

### 计划中

- 服务层读写分离（`ArticleService` 进一步拆分）与全站 API 限流扩容方案

## [1.0.0] - 2026-09-12

首个可用版本：前后端分离的个人博客系统，含完整设计文档与端到端验证工具。

### 新增

**内容与前台**

- 文章三种状态（草稿 / 已发布 / 已归档）、置顶、封面图、阅读时长
- Markdown 写作与渲染：DOMPurify 白名单消毒、代码高亮（按需注册语言）、
  自动生成目录、代码块复制按钮、阅读进度条
- 分类与标签、按月归档、两级评论（先审后发）、点赞与阅读量
- 列表页服务端分页 + 五种排序 + 关键词/分类/标签筛选，**全部状态存 URL query**
- 移动端浮动目录、响应式布局、亮/暗/跟随系统三态主题
- RSS 2.0（`/feed.xml`）与站点地图（`/sitemap.xml`）
- 每页标题与 Open Graph / Twitter 卡片 meta
- 相关文章推荐（同分类或共享标签）

**后台**

- 文章编辑器（分栏预览、粘贴上传、本地草稿自动保存与恢复）
- 评论审核、分类与标签管理、媒体库、用户管理、站点设置
- 全局错误边界：组件抛错渲染兜底页而非白屏

**后端与工程**

- FastAPI + SQLAlchemy 2.0 全异步 + Pydantic v2，严格分层
- JWT 双 Token（access 120 分钟 / refresh 7 天），401 静默续期
- 三级角色（访客 / 作者 / 站长）与三层访问控制
- 上传安全：Pillow 真实解码判型、流式限流读、扩展名白名单、服务端生成文件名
- Alembic 迁移（含升降级验证）
- 请求 ID 透传 + 结构化 JSON 日志 + 限流（登录 / 评论 / 点赞）
- `/health` 存活探针与 `/ready` 就绪探针（真查数据库）
- 容器化部署（Dockerfile × 2 + nginx + docker-compose）

**验证与文档**

- 后端 171 个 pytest 用例、前端 40 个 Vitest 用例
- `tools/smoke-check.mjs`：CDP 驱动的真实浏览器冒烟（34 项）
- `tools/interaction-check.mjs`：点击驱动的失败路径验证（16 项）
- `docs/DESIGN.md` 完整设计方案、`docs/ROADMAP.md` 迭代路线图、
  `docs/devlog/` 逐日开发日志

### 修复

- 登录后访问后台被莫名弹回登录页（`restore()` 的并发早退未等待进行中的恢复）
- 后台侧边栏渲染两遍、子页面完全不渲染（父路由同时写 `component` 与 `meta.layout` 导致布局嵌套两层）
- 未标语言的代码块被 highlight.js 自动探测出错误语言并显示在角标上
- 评论接口盲信 `X-Forwarded-For`，会把伪造 IP 写入数据库

[Unreleased]: https://github.com/FurinaLuna/personal-blog/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/FurinaLuna/personal-blog/releases/tag/v1.0.0
