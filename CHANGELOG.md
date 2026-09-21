# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的组织方式。

> 说明：1.0.0 之前的提交是项目从零建成的连续过程，这里按主题归并，
> 逐条的决策与踩坑记录见 [`docs/devlog/`](docs/devlog/)。

## [Unreleased]

### 已变更

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

### 已修复

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
