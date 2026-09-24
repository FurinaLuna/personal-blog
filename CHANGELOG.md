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

- **部署产物验证 `tools/deploy-check.mjs`（`make deploy-check`，已接入 CI）**：这个仓库
  此前**所有**自动化跑的都是源码（uvicorn 跑 `app/`、Vite dev server 跑 `src/`），
  而线上跑的是两个镜像 + nginx + PostgreSQL。新脚本真的构建镜像、真的用 compose 起一套栈，
  逐条断言那一层里的东西：compose 插值（含"缺必填变量必须失败"的**负向**验证）、
  起栈后的 HTTP 行为、容器内的生产形态与迁移 head、**备份的真恢复演练与轮转**、
  以及 TLS 覆盖文件那套。用独立项目名与临时目录，不碰开发者的 `.env`、
  已有 compose 项目、`./backups` 与 `./deploy/certs`，跑完自清理；报告写入 `shots/deploy/`
- **定时备份 sidecar**：compose 新增 `backup` 服务（`deploy/backup.sh`），用与数据库
  **同一个镜像**（`pg_dump` 版本必须 >= 服务端，同镜像让这条约束天然成立）每天落一份
  custom 格式 dump 到宿主 `./backups/`。每份先写 `.part`、再用 `pg_restore -l` 校验归档
  可读、通过才改名；失败改走 300s 短重试而不是等满一天；轮转只保留 `BACKUP_KEEP` 份
  （与 `backup_db.py` 的 `--keep` 同语义、同命名）。`BACKUP_ONCE=1 docker compose run --rm backup`
  可手动触发一次
- **自管证书的 TLS 模板**（`deploy/nginx.https.conf` + `docker-compose.tls.yml`）：
  把 README 里"自己在 nginx 加个 443 server 块"变成可直接用的配置 —— 80 端口只做
  ACME 挑战与跳转、443 承载站点、HSTS 无条件、certbot 的 webroot 与证书目录都已就位，
  覆盖文件一叠即用（`docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build`），
  退回纯 HTTP 不需要改文件
- **compose 的可覆盖项**：`APP_PORT`（8080 被占用时换端口）、`BACKUP_HOST_DIR`
  （备份落到别的盘）、`CERTBOT_WEBROOT`、`TLS_CERTS_DIR` —— 前两个是给部署用的，
  后两个同时让回归脚本能在临时目录里跑完整流程而不污染真实文件
- `docs/MODULES.md` 扩「部署相关改动落在哪」一节点名了这几个文件的分工
- **友情链接（`/links` 从占位页变成真功能）**：完整垂直切片 —— `friend_links` 表（迁移
  `c1f4b8e2d6a3`，`url` 唯一 + `sort_order`/`is_active` 索引）、仓储/服务/Schema/API、
  前台页面与后台管理页（`/admin/links`）、3 条演示数据。
  - 接口按「公开读 + 后台读」拆开：`GET /api/v1/links`（匿名，只含启用项，按
    `sort_order`→`id` 稳定排序）与 `GET /api/v1/links/manage`（站长，含未启用）；
    写操作（POST/PATCH/DELETE）一律限站长
  - 公开列表自动进入缓存白名单：`ETag` + `Cache-Control: max-age=60` +
    **`Vary: Authorization`**，`/manage` 因路径约定被自动排除
  - **URL 校验抽成唯一实现** `utils/url.py::normalize_http_url`：评论的 `author_site`
    改为调用它，友链的 `url`/`avatar_url` 也用同一套判定（先拒控制字符 → 再识别自带协议
    → 才决定补不补 `https://` → 最后结构校验）。三处各写一份迟早在某个入口漏掉一种
    伪协议，而那是存储型 XSS
  - 后台可原地编辑、一键隐藏/显示（隐藏的条目只对站长可见）、删除带确认
- **`docs/MODULES.md` 的扩展点表补上这次沉淀的三条**：新业务域的标准路径（含
  `/manage` 路径约定的理由）、公开读接口如何吃到缓存、用户可控 URL 一律走
  `utils/url.py`；并修正了两处过期内容（中间件顺序的结论、写死的测试数字）
- **浏览器回归网覆盖到友链**：`smoke` 加 2 条（页面渲染 + 演示数据可见），
  `full-check` 加 4 条 **A5c 生命周期**（后台建 → 公开接口立即可见 → 隐藏后前台消失
  → 删除入口），跨了「后台写 → 前台缓存读」两层
- `sitemap.xml` 收录 `/series` 与 `/links`（前者是既有遗漏）：有真实内容的聚合页
  不该只在应用内可达
- **views 层补齐到「每个视图都有 spec」**：本轮新增 15 个 spec / 222 条
  （LoginView 20、SearchView 23、ArchiveView 12、CategoriesView 10、SeriesView 11、
  SeriesDetailView 15、UnsubscribeView 15、AboutView 16、NotFoundView 3、PlaceholderView 5、
  admin：SettingsView 22、CommentListView 24、SeriesView 23、DashboardView 18、TagsView 9），
  累计 **22 个视图全部有测试**（216 + 222 条）。审计里排第一的前端覆盖空洞就此关闭
- **article 域按读写拆分**（ROADMAP 4.4，只搬移不改写）：
  `article_service.py`（642 行）→ `article_query_service.py`（432）+ `article_command_service.py`（241）；
  `article_repository.py`（697 行）→ `article_query_repository.py`（641）+ `article_write_repository.py`（80）。
  方法体逐字照搬、公开签名不变、测试零改动（541 → 666 passed 是另一批测试新增的结果）。
  保留 13 行的 `article_service.py` 只为一个纯函数 —— 有一条测试直接 import 它，
  而本次约束是不动测试；理由写在模块 docstring 里
- **标签云不再拉全量**（ROADMAP 1.7 的实质问题）：`TagsView` 此前一次取回**全部**标签
  （含每条的篇数计数），再在浏览器里 `filter(count > 0)` —— 没有文章的标签白白下载，
  也白白让后端做一轮计数子查询。现把过滤交给服务端（`minCount=1`）并带上上限
  （后端 `limit` 上限 200），触顶时页面明确说明"只显示最常用的 200 个"
- **回填变体接口返回 `remaining`**：前端以前只能固定说"还剩待处理可再次运行"，
  已经跑完时那句话是错的；或者拿 `processed` 去猜后端的分批上限（改一次上限就说谎）。
  现在由后端给出真实剩余数，前端据此说"还剩 N 张"或"已全部处理完"
- `frontend/src/views/TagsView.spec.ts`（9 条）：请求参数（服务端过滤 + 上限）、
  标签云渲染与链接、隐藏篇数、零篇标签不渲染、空态、骨架屏、失败态与重试、
  触顶与未触顶两种提示
- **刷新令牌轮换 + 复用检测 + 按会话吊销**（`refresh_sessions` 表，迁移 `b8d2f5a1c3e9`）：
  此前 refresh token 是**完全无状态**的（`jti` 生成了却从不落库），"吊销"只能靠
  `token_version += 1`（全端下线）。两个后果：泄露后在 7 天有效期内可反复换出
  新 access token 且服务端看不见；每刷新一次就多一枚可用凭证（泄露面单调增长）。
  现在每次签发登记一条会话（**只存 jti 的 SHA-256**），刷新即轮换旧令牌；
  已轮换的令牌再次出现即判定盗用并**吊销整族**（OAuth 2.0 的标准处理），
  30 秒宽容窗口内例外（容忍两个标签页同时刷新 —— 前端的单飞锁只在单页内生效）；
  过期会话在启动时清理（与访问日志同一取舍）
- `tests/test_refresh_rotation.py`（13 条）：会话登记与只存哈希、轮换后旧令牌失效、
  宽容窗口内外两种重放处理、未知令牌（升级前签发的）被拒、登出/改密码吊销会话、
  过期清理只删该删的
- **支持 HEAD 请求**（此前 `HEAD /api/v1/articles` 是 **405**）：FastAPI 不像 Starlette 的
  `Route` 那样给 GET 自动补 HEAD（`starlette/routing.py` 有 `if "GET" in methods: add("HEAD")`，
  `APIRoute` 没跟这条）。而 HEAD 是缓存/CDN 再验证、监控探活与 `curl -I` 的常用手段。
  `HeadMethodMiddleware` 的做法是：只把**内层** scope 的方法名改写成 GET，外层
  （日志/限流/CORS）看到的仍是 HEAD；正文一律丢掉但**头部原样保留**（含
  `Content-Length`，这正是 HEAD 的语义）。位置很关键 —— 必须在 PublicCache
  **外层**，否则 ETag 会对"空正文"取哈希，所有资源的 ETag 变成同一个值
- **前端 views 层测试第二梯队**：`ArticleListView`（32 条）、`MediaView`（30 条）、
  `TaxonomyView`（33 条）、`UsersView`（23 条），共 **118 条**；
  加上第一梯队共 7 个 view / 216 条
- `tests/test_public_cache.py` 扩到 **55 条**：新增 `Vary` 契约、
  HEAD 的头部一致性/条件请求/日志方法名、`merge_vary` 的合并规则
- `tools/full-check.mjs` 的 A5b 用例改为**失败会自证**：找不到行 / 找不到编辑按钮 /
  编辑器没打开分别返回不同 reason，并带上当时的列表文案与条目 ——
  这条用例此前失败时只有一个空 detail（正是它让我多花了一轮才定位到缓存问题）
- **公开读接口的 HTTP 缓存**（`api/cache.py`）：前台所有只读接口此前没有任何缓存头，
  浏览器每次二次访问、每次前进后退都要重新全量查库，中间任何一层缓存也无从判断
  内容有没有变。现补 `ETag`（对响应体取 sha256，弱验证器）+ `Cache-Control: public,
  max-age=60`，并处理 `If-None-Match` → 304。**只对匿名 GET 生效**（带
  `Authorization` 一律跳过：同一 URL 对不同用户可能不同，共享缓存命中即越权），
  且排除 `/manage`、`/revisions` 与所有非 200 响应
- **前端 views 层补单测**（此前 22 个 view / 4049 行零 spec、项目最大的覆盖空洞）：
  `ArticleEditView.spec.ts` 40 条、`ArticleDetailView.spec.ts` 34 条、
  `HomeView.spec.ts` 24 条，共 **98 条**，覆盖状态流转、失败路径、
  草稿分桶、发布设置、标签输入、版本历史、URL 即唯一状态源、权限分支与竞态
- 新增 `tests/test_public_cache.py`（41 条）：可缓存请求的判定规则、
  ETag 生成与条件请求匹配、响应体重建后的 `content-length` 一致性、
  以及**中间件顺序**的两条契约（被 CORS 拒绝的预检仍带 request_id；
  访问日志记录的是折叠后的 304 而不是 200）
- 新增 `TestAppEnvWhitelist`（8 条）：`APP_ENV` 白名单拒绝与大小写/空格归一化
- `tests/test_deploy_config.py` 补 2 条：HSTS 必须是条件式（map 的两个分支 +
  add_header 用变量）、nginx.conf 必须落到 `conf.d/`（`map`/`upstream` 是 http 级指令）
- **条件式 HSTS**（`deploy/nginx.conf`）：`map $http_x_forwarded_proto $hsts_header`
  —— 只有外层真的在用 HTTPS 才发 `Strict-Transport-Security`
- **PostgreSQL 备份与恢复路径**：`scripts/backup_db.py` 此前对 PG 明确拒绝，
  而 compose 里生产库就是 postgres:16 —— 也就是说**生产部署的两个数据卷
  完全没有备份**，精心打磨的 `VACUUM INTO` 只服务于开发用的 SQLite。
  现支持 `pg_dump -Fc`（宿主有 pg_dump 时直接用，否则
  `docker exec <容器> pg_dump`），轮转同时认 `.db` 与 `.dump`，
  备份失败时删除半成品文件；README 补「备份与恢复」一节
  （含**真实恢复演练**：dump → 空库 pg_restore → 核对 13 张表 / 3 条 trgm 索引 /
  alembic head），以及「HTTPS」一节（两种接 TLS 的方式与必须同步改的配置）
- `tests/test_backup_script.py` 补 9 例：PG URL 解析（百分号编码密码、
  默认端口、无库名拒绝）、`pg_dump` 参数构造（custom 格式、密码不进命令行、
  容器内不传 -h/-p）、跨后缀轮转
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

- **nginx 配置拆成"两套外壳 + 一份共享 server 体"**：新增 `deploy/nginx-common.inc`
  （http 上下文的 upstream）、`deploy/nginx-server.inc`（站点 location 与缓存策略）、
  `deploy/security-headers.inc`（安全响应头）。`nginx.conf` 与 `nginx.https.conf`
  只在"外壳"上不同（listen、证书、跳转）。抄成两份的代价是"改了 HTTP 版、HTTPS 版没改"
  不会有任何报错，只会行为不同 —— 这正是 nginx 上最难发现的那类漂移
  - 文件名刻意用 `.inc`：官方镜像的主配置里有 `include /etc/nginx/conf.d/*.conf;`，
    叫 `.conf` 的片段会被当成第二个 server 加载
  - 前端镜像新增 `NGINX_CONF` 构建参数来选主配置（默认 `nginx.conf`），
    声明在该 ARG 之后的层才会失效，所以切换 TLS 时 npm ci 与 vite build 仍走缓存
- **静态缓存的写法统一**：`/assets/` 与 `/media/` 改为显式 `Cache-Control`
  （不再用 `expires` 再叠一条 `add_header Cache-Control`）—— 此前响应里会出现
  两条 `Cache-Control`，合法但排查缓存问题时极难读
- **`/feed.xml` 与 `/sitemap.xml` 合并为一个正则 location，并删掉两条死配置**：
  原先写着 `proxy_cache_valid 200 1h` 却没有定义 `proxy_cache`，那是**空操作**，
  读起来却像"已经缓存了"。当前由应用侧的 `Cache-Control` + `ETag` 负责，
  注释里写明了为什么不在这里做 micro-cache（`Vary` 的处理，见缓存那一节踩过的坑）

> **升级须知（1.0.0 之前）**：本次引入了刷新会话表。**此前签发的 refresh token
> 在库里没有对应记录，会一律被拒** —— 也就是升级后需要重新登录一次。
> access token 不受影响（它不查这张表），会在 120 分钟内自然过期。
> 若将来变成多用户系统，这里需要一个"识别为旧令牌则放行一次并补登记"的过渡逻辑。

- `useAction` 的 `success` 支持传函数（成功那一刻才求值）。原因是一个真实缺陷：
  `success: `欢迎回来，${auth.displayName}`` 是**调用前**求值的，
  而 `displayName` 要等登录成功才有值 —— 登录成功却提示"欢迎回来，访客"
- 路由守卫不再对公开页面 `await auth.restore()`：身份恢复只影响顶栏显示，
  不该阻塞内容渲染；需要登录的页面仍会等待，但**加上限**（6s）
- `forceLogout()` 只在**当前路由需要登录**时才整页跳转（探针由 router 注入，
  避免 `router → stores/auth → api/http` 的循环依赖）；公开页面只清凭证，
  并通过 `onCredentialsCleared` 通知 store 清掉内存里的 `user`
- `make seed` 不再无声删库：先自动备份一份再重建演示数据
- 文档基线收敛：`docs/CODE-REVIEW.md` 与 `docs/TEST-REPORT.md` 加上
  「历史快照」横幅（它们分别是 2026-09-12 的旧结论，此前读起来像现状）；
  PR 模板 interaction 项数 16 → 22；`docs/ROADMAP.md` 第 0 节快照刷新到实测值
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

- **生产环境的 CSP 实际上从未生效（首页文档一个安全响应头都没有）**：nginx 的
  `add_header` **不继承** —— 只要某一层自己写了哪怕一条，父层的全部失效。
  而 `location = /index.html` 为了加 `Cache-Control: no-cache` 写了一条，
  于是首页 HTML 文档（唯一会被浏览器当文档解析、也是 XSS 唯一有意义的落点）
  丢掉了 CSP、`nosniff`、`X-Frame-Options`、`Referrer-Policy` 与 HSTS；
  而 `/assets/`、`/media/` 上的 CSP 挂在 JS/图片响应上，浏览器根本不会应用。
  实测（修复前）：`curl -sI http://localhost:8080/` 没有任何 CSP 头。
  现在安全头抽成 `deploy/security-headers.inc`，每个自己写了 `add_header` 的
  location 都 include 它，`tools/deploy-check.mjs` 里有一条断言盯着首页 CSP
- **CSP 修好后会拦掉首页的内联主题脚本**（这是修上面那条时才暴露的连带问题）：
  `frontend/index.html` 里那段"在样式加载前给 `<html>` 打 dark 类"的脚本是内联的，
  而 `script-src 'self'` 不允许内联 —— 一旦 CSP 真正作用到文档上，症状是
  **暗色模式白闪回归 + 控制台报 CSP 违规**，页面其余部分完全正常，很容易被当成
  CSS 问题去查。现把它的 sha256 写进 `script-src`；改那段脚本后
  `make deploy-check` 会把该填的哈希直接打出来
- **`refresh_sessions` 迁移的两处漂移**（在真 PostgreSQL 上跑迁移 + 比对
  `compare_metadata` 时暴露，两条都是我写的）：① `jti_hash` 同时建了
  `UniqueConstraint` 与普通索引，而模型写的是 `unique=True, index=True`
  （SQLAlchemy 据此只生成一条唯一索引）—— PG 上就是两条功能重叠的索引，
  既浪费写入又让元数据比对永远报差异；② 漏了 `ix_refresh_sessions_created_at`
  （`TimestampMixin` 声明了 `created_at index=True`）。该迁移**从未被任何环境执行过**，
  所以直接改它而不是追加补丁：追加只会把一段本可以干净的历史留成两截
- **`full-check` 的友链用例会把后续用例连带打挂**（我在扩展脚本时自己引入的）：
  A5c 切到 `/admin/links` 后没有切回分类页，而紧随其后的 A6/A7 都假设停留在
  `/admin/taxonomy` —— 结果一次连带 4 条用例失败，且 A6 创建的标签漏登记、
  污染了数据基线（基线检查如实报出「tags: 7 → 8；残留 tags: 1」）。
  现已显式切回，并在注释里写明"为什么必须切回"
- **登录开放重定向（协议相对地址）**：`redirect` 用 `startsWith('/')` 校验，
  `//evil.example/phish` 能通过，而浏览器把它解析成 `https://evil.example/phish` ——
  history 模式 pushState 到跨源地址会抛 `SecurityError`，表现是**登录成功却停在登录页**
  外加一条没人处理的 rejection。改用 `/^\/(?!\/)/` 一次排掉外站与伪协议
- **站点设置页的静默数据丢失（最危险的一条）**：档案接口失败时 store 回落默认值、
  `loaded` 永远为 false，表单是空的、**没有错误提示也没有重试**，而保存按钮照常可用 ——
  站长随手一点就把站点名、签名、关于页、邮箱、备案号、评论策略整片覆盖成默认值，
  界面还提示"保存成功"。现在 store 如实暴露 `error`（前台仍优雅降级），
  后台显示错误条 + 重试，并在 `loaded === false` 时**禁用保存**
- **系列详情不跟随路由参数**：只绑 `onMounted`，`/series/a → /series/b` 是同一条路由记录、
  组件实例被复用 → 页面停在旧系列的标题与列表且不报错。现 `watch` slug（与文章详情同一口径）
- **系列详情硬编码 50 篇且无分页**：60 篇的系列只列前 50 条，第 51 篇起**没有任何入口**，
  而头部还写着"60 篇"。现按 URL 分页（`?page=`），并渲染分页器
- **搜索页页码口径与首页不一致**：分页器读 URL 的 `page` 而不是接口返回的，
  `?page=99` 被后端收敛到末页时会出现「981–42 / 共 42 条」这种自相矛盾的数字
- **评论管理页两处插值写成了字面量**：`#{item.article_id}` / `#{item.parent_id}`
  少了一层花括号，Vue 不插值 → 每一行都显示调试风格的 `#{...}` 文本
- **评论管理页删掉当前页最后一条后停在空页**：空态分支里没有分页器、`total` 仍大于 0，
  用户没有任何入口回到上一页；现在删除后会把页码收敛回上一页
- **系列编辑允许空名称**：新建有非空校验而编辑没有，清空名字会把 `name: ''` 发给后端
  （只能靠 422 兜底）；现与新建同一道校验
- **系列删除的权限判定靠正则匹配后端文案**：`/403|站长/.test(String(error))` ——
  后端换文案就静默退化，任何含"站长"的错误也会被误判成权限问题。改用 `error.isForbidden`
- **归档 / 分类 / 系列 / 系列详情失败态没有重试入口**：只有一行文案，
  用户只能自己猜"刷新一下试试"；现统一 `role="alert"` + 重试按钮
- **评论管理页把插值写成了字面量**：`CommentListView.vue` 两处用了单花括号
  （`查看文章 #{item.article_id}`、`回复 #{item.parent_id}`），Vue 不插值 ——
  每一行都显示 `#{item.article_id}` 这种调试风格的字面量。现改为 `#{{ ... }}`
- **用户管理的字段错误不随输入清除**：改完用户名后红字还挂在那里，
  用户以为"还是不对"。现与 `ArticleEditView` 同一口径：一改输入就清掉对应那条
  （只清自己那条，其它字段的错误保留）
- **`refresh_sessions` 的时间字段在 SQLite 上比较即抛异常**：SQLite 存的时间取出来是
  **naive**，而 PG 的 `timestamptz` 取出来是 aware，`naive <= aware` 直接
  `TypeError: can't compare offset-naive and offset-aware datetimes`。
  第一次跑测试就撞上了（refresh 的过期判断）。改用仓库里早就为此准备的
  `db/types.py::UTCDateTime`（读出来统一补 UTC）
- **公开读缓存击穿了"读己之写"（自己引入、被浏览器端到端抓到）**：
  加上 `Cache-Control: public, max-age=60` 之后，同一个 URL 既服务匿名公开页、
  也服务已登录后台（`/categories`、`/tags`、`/site/profile` 都是这样），
  而浏览器缓存**只按 URL 匹配**：匿名那次存下的响应会被登录后的列表请求命中 →
  表现为「后台新建分类成功，但列表里看不到新分类」。
  修法是在被缓存的响应上声明 `Vary: Authorization`（与 CORS 的 `Vary: Origin`
  合并而不是覆盖）。**单测看不见这一层**（API 被 spy 掉），是 `full-check` 的 A5b
  把它抓出来的 —— 这也是为什么浏览器脚本不能省
- **删除成功后确认框不关、列表不刷新（UsersView 真机必现）**：
  `useConfirmDelete` 拿 `action.run()` 的返回值判成败，而 `if (done === undefined) return`
  撞上了 `api.delete` 的默认泛型 `void` —— **成功**时 resolve 出来就是 `undefined`，
  恰好等于 useAction 表示失败的哨兵。于是绿色 toast 已经弹了「已删除」，
  而对话框不关、被删的行还在。UsersView 必现（store 的 `removeUser` 是 async void），
  另三个视图只是潜伏（204 → `''`、JSON → 真值）。现改为让任务显式返回 `true`，
  成败只由"有没有抛异常"决定 —— 一处修好四个视图
- **编辑页竞态：迟到的详情响应会覆盖表单（静默数据丢失）**：`loadArticle` 在
  `await articleApi.detail()` 后直接写 `form.value`，没有代次守卫。在两个编辑页之间
  快速切换（改地址栏 id、或前进/后退 —— 路由记录相同、实例复用）时，先发后到的
  响应会把表单改成**上一篇**的内容，而保存 PATCH 的是当前 id：用户以为在改 B，
  实际把 A 的内容写进了 B。现加加载代次，迟到的成功/失败一律丢弃
  （失败也丢：否则一篇 404 会把另一篇标成"加载失败"）
- **后台文章列表的筛选状态只活在内存里**：`?status=draft` 只在进入时读一次，
  之后切筛选/翻页/搜索都不回写地址栏 —— 刷新或分享出去的链接与屏幕上看到的不一致；
  非法 `?status=hacked` 还会原样发给后端。现与前台列表同一口径：**URL 是筛选状态的
  唯一来源**（回写用 `replace`，第 1 页不进 URL），并加白名单校验与非法 page 兜底
- **分类/标签接口失败被伪装成"空"**：`TaxonomyView` 从不消费 `categories.error` /
  `tags.error`，500 时页面显示「还没有分类。」—— 站长会以为分类被清空了。
  现失败给错误文案 + 重试入口，加载中给骨架屏（不再先闪一下空态）
- **重命名分类不校验空名称**：`createCategory` 有非空校验而 `saveCategory` 没有，
  清空名字仍会发出 `update(id, {name: ''})`，只能靠后端 422 兜底。现已对齐
- **列表失败态没有重试入口**：`ArticleListView` / `MediaView` 此前只有一行错误文案，
  用户只能自己猜"刷新一下试试"；现补 `role="alert"` + 重试按钮
- **第 3 页删一条会被弹回第 1 页**：删除走 `reload()`（默认重置页码），
  与切换状态的 `reload(false)` 口径不一致；现统一保留当前页
- **`APP_ENV` 写错就让生产门禁静默失效**：`is_production` 的判据是
  `app_env.lower() == "production"`，于是 `APP_ENV=prod`、`prd`、`production `
  （末尾空格）都会让整套安全门禁（默认密钥 / 默认口令 / DEBUG / DB_AUTO_CREATE /
  SEED_DEMO_DATA）**不生效且无任何告警**。现在 `APP_ENV` 是白名单校验：
  大小写与空格归一化，未知值直接**拒绝启动**（与 admin_email 用 EmailStr 同一取舍）
- **中间件注册顺序与注释意图相反**：注释写的是"即使请求被 CORS 拒绝也能留下
  record"，但 `RequestContextMiddleware` 注册在 CORS **之前**（= 更内层），
  实测被 CORS 拒掉的预检**既没有 X-Request-ID 也没有访问日志**。
  现按「内 → 外」重排为 CORS → PublicCache → RequestContext，
  并写清规则（**后注册的在外层**）与验证方式
- **编辑页竞态：迟到的详情响应会覆盖表单（静默数据丢失）**：
  `loadArticle` 在 `await articleApi.detail()` 后直接写 `form.value`，没有代次守卫。
  在两个编辑页之间快速切换（改地址栏 id、或前进/后退——路由记录相同、组件实例复用）时，
  先发后到的响应会把表单改成**上一篇**的内容，而保存 PATCH 的是当前 id ——
  用户以为在改 B，实际把 A 的内容写进了 B。现加加载代次，迟到的成功/失败一律丢弃
  （失败也丢：否则一篇 404 会把另一篇标成加载失败）
- **从加载失败的 A 切到正常的 B 时，旧的错误提示条不消失**：重新加载时先清 `formError`
- **删除文章用 `undefined` 当失败哨兵**：`articleApi.remove` 是 `Promise<void>`，
  判定依赖"204 空响应体被 axios 转成 `''`"这种实现细节；改为任务显式返回 `true`
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
