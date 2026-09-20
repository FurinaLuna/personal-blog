# personal-blog 端到端（E2E）测试报告

> 执行时间：2026-09-19 ｜ 执行方式：真实进程 + 真实数据库 + HTTP 全链路 + 直连数据库二次校验
> 自动化套件：`tools/e2e_live/e2e_run.py`（可复现）；原始数据：`reports/e2e-results-20260919-222644.json`

---

## TL;DR

搭建了一整套**真实环境**端到端测试（空库 → `alembic upgrade head` 建表 → 独立 uvicorn 进程 → HTTP 请求 → 直连 SQLite 校验落库），共 **62 条用例**：**PASS 58 / FAIL 4 / ERROR 0**，通过率 93.5%，项目真实库 `backend/blog.db` 全程零污染。

4 条 FAIL 全部为**实测确认的真实缺陷**（非误报），其中 **1 条是本次新发现**：仅一个环境变量填错邮箱域名，就能让关于页和站长登录态全线 500，且服务正常启动、无任何告警。

---

## 一、待测功能清单与优先级

### A. 后端 API 入口（共 58 个端点）

| 模块 | 端点数 | 覆盖 | 优先级 | 说明 |
|---|---|---|---|---|
| 文章 `articles` | 10 | 10 | **P0** | 列表/搜索/归档/后台列表/详情/新建/更新/删除/点赞/相关文章 |
| 评论 `comments` | 5 | 5 | **P0** | 评论树/发表/后台列表/审核/删除 |
| 认证授权 `auth` | 11 | 7 | **P0** | login/token/refresh/logout/me/改密/用户增删改查 |
| 版本 `revisions` | 3 | 3 | **P0** | 版本列表/详情/回滚 |
| 附件 `attachments` | 4 | 3 | P1 | 上传/媒体库/删除/变体回填 |
| 系列 `series` | 4 | 4 | P1 | 全周期 CRUD（含 SET NULL） |
| 分类/标签 `taxonomy` | 10 | 5 | P1 | 分类 CRUD、标签 CRUD、空标签清理 |
| 站点 `site` | 3 | 2 | P1 | 档案读/写、全站统计 |
| 统计 `stats` | 2 | 2 | P1 | 每日趋势、访问日志清理 |
| 通知 `notifications` | 1 | 1 | P2 | 邮件退订 |
| Feed `feed.xml / sitemap.xml` | 2 | 2 | P1 | RSS 与站点地图 |
| 运维探针 `/health /ready /` | 3 | 2 | P1 | 存活/就绪探针 |

**端点级覆盖率：约 74%（43/58）**

### B. 关键业务流程（P0 主流程，全部实测跑通）

| # | 流程 | 链路 | 结果 |
|---|---|---|---|
| 1 | 访客浏览 | 列表 → 详情（浏览量+1、写 visit_logs）→ 搜索/归档/分类标签系列 → RSS/Sitemap | ✅ |
| 2 | 评论闭环 | 游客发表（待审核）→ 访客不可见 → 站长审核 → 前台可见 → 二级回复 → 级联删除 | ⚠️ 三级回复有缺陷 |
| 3 | 点赞 | 点赞计数递增落库；草稿拒绝点赞 | ✅ |
| 4 | 内容创作 | 登录 → 建草稿 → 上传图片（落盘+可访问）→ 发布 → 编辑产生版本 → 版本回滚 → 删除（级联清评论） | ✅ |
| 5 | 账号体系 | 登录/刷新/改密吊销旧令牌/创建作者/越权 403 | ⚠️ 登出不吊销令牌 |
| 6 | 后台管理 | 系列全周期、分类标签 CRUD 与清理、媒体库删除同步磁盘、用户改删、统计与日志清理 | ✅ |

### C. 任务 / 回调 / 后台入口

- **启动期任务**：`alembic upgrade head`（生产路径，本次即据此建表）、FTS 索引补建、visit_logs 过期清理（已通过 `/stats/visit-logs/prune` 单独验证）
- **启动种子**：站长账号 + 站点档案 + 4 篇演示文章（已验证幂等且哈希落库）
- **异步通知**：评论通知 fire-and-forget（已用不可达 SMTP 验证不阻塞主流程）

---

## 二、测试环境（真实而非模拟）

| 项 | 配置 |
|---|---|
| 应用进程 | `backend/.venv` 解释器启动独立 `uvicorn`（新 breeds，端口 8099） |
| 数据库 | 临时目录新建**空 SQLite**，**用 `alembic upgrade head` 建表**（与生产同路径，未用 ORM `create_all`） |
| 文件存储 | 独立 `storage` 目录，与项目 `backend/storage` 完全隔离 |
| 断言方式 | HTTP 响应 + **直连 SQLite 复核真实落库/落盘结果**，不接受「接口说成功了」 |
| 污染校验 | 测试前后比对 `backend/blog.db` 大小与 mtime → **一致，未被污染** |
| 前端层 | 未启 dev server（见「降级说明」） |

---

## 三、执行结果总览

| 指标 | 数值 |
|---|---|
| 用例总数 | **62** |
| PASS | **58** |
| FAIL | **4**（均为确认缺陷） |
| ERROR | **0** |
| 通过率 | **93.5%** |

### 按流程分布

| 流程 | 用例数 | PASS | FAIL |
|---|---|---|---|
| 环境与探针 | 2 | 2 | 0 |
| 迁移/建表 | 1 | 1 | 0 |
| 启动种子 | 1 | 1 | 0 |
| 访客读取 | 8 | 8 | 0 |
| 账号与权限 | 9 | 8 | **1**（U05） |
| 文章创作/发布/版本/删除 | 7 | 7 | 0 |
| 评论 | 10 | 9 | **1**（C05） |
| 后台管理（系列/分类/媒体库/用户/统计/通知） | 6 | 6 | 0 |
| 点赞 | 2 | 2 | 0 |
| 边界输入 | 4 | 4 | 0 |
| 上传安全 | 3 | 3 | 0 |
| 冲突/错误契约/空数据/重复操作 | 5 | 5 | 0 |
| 限流 | 1 | 1 | 0 |
| 配置健壮性 | 2 | 0 | **2**（X01/X02） |

---

## 四、逐用例明细

| 编号 | 流程 | 优先级 | 用例 | 状态 | 关键实测证据 |
|---|---|---|---|---|---|
| E01 | 环境与探针 | P1 | /health 返回 ok | PASS | `{status:ok, env:testing}` |
| E02 | 环境与探针 | P1 | /ready 报 database=up | PASS | `{database:up}` |
| E03 | 迁移/建表 | P0 | alembic 建表完整 | PASS | 18 张表，含 FTS 虚拟表 |
| E04 | 启动种子 | P0 | 站长账号 + 4 篇演示文章入库 | PASS | 密码 `$2b$` 哈希；4 篇已发布 |
| R01 | 访客读取 | P0 | 列表条数与库内一致 | PASS | 4 条 = 库内 4 篇 |
| R02 | 访客读取 | P0 | 详情：浏览量+1 且写 visit_logs | PASS | 37→38；日志 0→1 |
| R03 | 访客读取 | P0 | 全文检索命中并返回 snippet | PASS | 命中 1 条 |
| R04 | 访客读取 | P1 | 按月归档 | PASS | 2 个月份分组 |
| R05 | 访客读取 | P1 | 分类/标签/系列列表一致 | PASS | 3 / 7 / 0 |
| R06 | 访客读取 | P2 | 相关文章接口 | PASS | 返回列表 |
| R07 | 访客读取 | P1 | RSS + Sitemap 含真实链接 | PASS | 标题与 slug 均在 |
| R08 | 访客读取 | P1 | 站点档案与库内一致 | PASS | owner_name 比对一致 |
| A01 | 账号权限 | P0 | 站长登录取双 token | PASS | /auth/me 校验通过 |
| A02 | 文章创作 | P0 | 草稿入库（状态/正文/作者/标签） | PASS | id=5，标签 ['E2E','测试'] |
| A03 | 文件上传 | P0 | PNG 上传落库 + 落盘 + 可访问 | PASS | id=1，静态路由 200 |
| A04 | 文章发布 | P0 | 发布后前台可见、published_at 落库 | PASS | status=published |
| A05 | 版本管理 | P0 | 编辑自动生成修订版本 | PASS | 版本数 0→1 |
| A06 | 版本管理 | P0 | 回滚到指定版本（正文真实还原） | PASS | 库内正文与版本一致 |
| A07 | 冲突处理 | P1 | 重复 slug 不产生重复记录 | PASS | 自动退让 `...-2`，库内唯一 |
| A08 | 删除级联 | P1 | 删文章级联清理评论 | PASS | 文章与评论均无残留 |
| U01 | 账号权限 | P0 | 错误密码 401 | PASS | 401 |
| U02 | 账号权限 | P0 | 伪造 token 401 | PASS | 401 |
| U03 | 账号权限 | P0 | refresh 换取可用新令牌 | PASS | 新 token 可用 |
| U04 | 账号权限 | P0 | 改密后旧令牌立即失效 | PASS | access/refresh 均 401 |
| **U05** | 账号权限 | P1 | **登出后旧令牌是否失效** | **FAIL** | 登出后 `/auth/me` 仍 200 |
| U06 | 账号权限 | P0 | 站长创建作者并可登录 | PASS | id=2，bcrypt 哈希 |
| U07 | 边界输入 | P2 | 弱密码/非法邮箱 422 | PASS | 422 |
| A09 | 权限 | P0 | 作者访问站长接口 403 | PASS | 403 |
| A10 | 权限 | P0 | 匿名访问受保护接口 401 | PASS | 401 |
| C01 | 评论 | P0 | 游客评论真实入库且待审核 | PASS | is_approved=0 |
| C02 | 评论 | P0 | 待审核对访客不可见 | PASS | 树中无该 id |
| C03 | 评论 | P0 | 审核通过后前台可见 | PASS | 库内翻转为 1 |
| C04 | 评论 | P0 | 二级回复 parent_id 与嵌套 | PASS | 进入 replies |
| **C05** | 评论 | P1 | **三级评论（回复的回复）** | **FAIL** | 落库但树中永不可见 |
| C06 | 评论 | P0 | javascript: 伪协议被拒 | PASS | 422 且未入库 |
| C07 | 评论 | P1 | 空白内容 422 且不入库 | PASS | 422 |
| C08 | 评论 | P1 | SMTP 不可达不阻塞主流程 | PASS | 评论仍 201 落库 |
| C09 | 评论 | P1 | 删父评论级联删回复 | PASS | 父子均无残留 |
| C10 | 评论 | P2 | 后台列表按状态过滤与库内一致 | PASS | 数量一致 |
| S01 | 后台管理 | P1 | 系列全周期 + 删除 SET NULL | PASS | 文章 series_id 置空 |
| T01 | 后台管理 | P1 | 分类 CRUD + 空标签清理 | PASS | 空标签被清理 |
| M01 | 后台管理 | P1 | 媒体库删除同步清理磁盘 | PASS | 磁盘文件已消失 |
| N01 | 后台管理 | P1 | 改用户后删除，且不可再登录 | PASS | 记录删除+401 |
| ST01 | 后台管理 | P1 | 统计/趋势/日志清理 | PASS | article_total=6 等 |
| NT01 | 后台管理 | P2 | 伪造退订 token 返回 4xx | PASS | 未出现 500 |
| L01 | 点赞 | P0 | 点赞计数递增落库 | PASS | 5→6 |
| L02 | 点赞 | P1 | 草稿点赞 404 | PASS | 404 且计数未变 |
| B01 | 边界输入 | P1 | page=0 → 422 | PASS | 422 |
| B02 | 边界输入 | P1 | 超大 page_size 被收敛 | PASS | 收敛 ≤50 |
| B03 | 边界输入 | P1 | 非法状态值 422 | PASS | 422 |
| B04 | 边界输入 | P1 | 201 字标题 422 | PASS | 422 |
| B05 | 上传安全 | P1 | 伪造后缀 exe / SVG 被拒 | PASS | 415 / 415 |
| B06 | 上传安全 | P1 | 超 10MB 被拒 | PASS | 413 |
| B07 | 上传安全 | P0 | **解压炸弹被拦截** | PASS | 198KB/6400 万像素 → 415 |
| B08 | 冲突处理 | P1 | 重复分类 409 | PASS | 409 |
| B09 | 错误契约 | P1 | 404 统一信封含 request_id | PASS | 含 code + request_id |
| B10 | 错误契约 | P1 | 异常输入 5xx 格式 | PASS | 本次走到 415，未触发 500 |
| B11 | 空数据 | P1 | 空结果返回空列表 | PASS | items=[] |
| B12 | 重复操作 | P1 | 重复标签无脏数据 | PASS | 库内仅 1 条 |
| B13 | 限流 | P1 | 登录超限 429 | PASS | `[200×5, 429, 429]` |
| **X01** | 配置健壮性 | P1 | **保留域邮箱配置 → 读取 500** | **FAIL** | /site/profile 与 /auth/me 均 500 |
| **X02** | 配置健壮性 | P1 | **500 无统一信封/request_id** | **FAIL** | 响应体 `Internal Server Error` |

---

## 五、缺陷清单（全部实测确认）

### DEFECT-1（新发现，P1）· 配置邮箱未校验，读取时 500 击穿多个页面

- **用例**：X01 / X02
- **影响范围**：**关于页整页不可用**；站长登录后任何依赖 `/auth/me` 的页面（后台、编辑器）全部失效。服务照常启动、日志无告警，故障只能由用户先发现。
- **现象**：`ADMIN_EMAIL=admin@e2e.test`（`.test` 属于 RFC 保留域）→ seed 写入时不校验 → 读取时被 Pydantic 拒绝 → 未捕获异常落到默认 500 处理器。
- **服务端日志证据**（`server.log`）：
  ```
  File "src/app/api/v1/site.py", line 18, in get_profile → SiteProfileRead.model_validate(profile)
  pydantic_core._pydantic_core.ValidationError: 1 validation error for SiteProfileRead
  email: The part after the @-sign is a special-use or reserved name
  INFO: "GET /api/v1/site/profile HTTP/1.1" 500 Internal Server Error
  File "src/app/api/v1/auth.py", line 74, in read_me → "GET /api/v1/auth/me HTTP/1.1" 500
  ```
- **修复建议**：① 在 `Settings` 里对 `admin_email` 加 `EmailStr` 约束，让非法配置在**启动时**失败（与既有生产安全门禁同策略，比运行时 500 早得多）；② `main.py` 注册 `@app.exception_handler(Exception)` 返回 `{detail, code, request_id}` 兜底。

### DEFECT-2（P1）· 登出不吊销令牌，logout 形同虚设

- **用例**：U05 ｜ **位置**：`api/v1/auth.py:61-69`
- **影响范围**：共享/公共设备上点「登出」后，凭证在有效期内（access 120 分钟、refresh 7 天）仍可访问所有受保护资源；用户误以为已退出。
- **最小复现**：`POST /auth/login` → `POST /auth/logout`（200）→ `GET /auth/me`（**仍 200**）
- **修复建议**：低成本改法是 logout 时令该用户 `token_version += 1`（改密已走这条链路，基础设施现成），或把 refresh token 迁到 httpOnly Cookie + 服务端会话表。

### DEFECT-3（P1）· 三级评论被接受却永远不可见

- **用例**：C05 ｜ **位置**：`services/comment_service.py:155`（仅校验 parent 归属，未校验 `parent.parent_id is None`）
- **影响范围**：访客回复二级评论时，接口返回 201「成功」，但两级评论树查询永远查不到该条 → **用户以为回复成功，实际丢失**；仅后台扁平列表可见，站长也难以察觉。
- **最小复现**：
  ```bash
  # 1) 根评论 C1 → 2) 回复 C1 得到 C2 → 3) 再回复 C2 得到 C3
  curl -X POST .../comments/article/{id} -d '{"content":"三级","parent_id":<C2>}'  # 201
  curl .../comments/article/{id}                                                   # C3 查不到
  ```
- **修复建议**：create 时校验 `parent.parent_id is None`，否则 `raise BadRequestError("仅支持两级评论")`，或直接把它重挂到根评论 `parent.parent_id`。

### 已验证「看起来像问题但实际正确」的行为（避免误改）

| 行为 | 结论 |
|---|---|
| 重复 slug 返回 201 并自动退让成 `-2` | 设计如此，且库内 slug 唯一性成立 |
| 草稿文章不允许评论（404） | 与「草稿不可点赞/不计浏览」口径一致 |
| 点赞无去重，重复点赞计数递增 | 服务层注释明确说明「无身份计数」是有意选择 |
| 邮箱含超大 attachments 413、非白名单 415 | 防护工作正常 |

---

## 六、覆盖缺口与后续建议

**未在本轮覆盖的部分（诚实列出）**：
1. **浏览器渲染层**：本轮未启动 dev server 做真实 UI 点击验证，所有断言都在 HTTP/数据库层完成。页面渲染与交互建议与既有的 `make smoke` / `make full-check`（`tools/*.mjs`）互补执行。
2. **迁移链本身**：本轮用迁移建表（与生产同路径），但未验证「旧版本库 → 逐级 upgrade」的向后兼容性。
3. **PostgreSQL 路径**：仅验证了 SQLite；`asyncpg` 分支未测。
4. **并发写**：未做高并发压测（仅覆盖重复/idempotent 场景）。

**优先级建议**：先修 DEFECT-1（配置校验 + 兜底 500 处理器，合计约 25 行代码），再修 DEFECT-3（用户可感知的数据丢失，约 3 行），最后处理 DEFECT-2。三处修复后重跑本套件即可验证。

---

## 七、复现方式

```bash
# 在项目根目录执行（会用独立临时库，绝不触碰 blog.db）
backend/.venv/Scripts/python.exe tools/e2e_live/e2e_run.py

# 定点复现某个 500：
backend/.venv/Scripts/python.exe tools/e2e_live/probe.py
```
产物落在 `tools/e2e_live/reports/`（含逐用例 JSON、服务端日志摘录、临时工作目录路径）。
