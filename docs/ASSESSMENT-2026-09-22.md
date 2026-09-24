# 项目全面评估 · 2026-09-22

> 与 `docs/ASSESSMENT.md`（2026-09-12）的区别：那份是功能/风险/升级视角的全面评估，
> 本份是**架构 · 代码质量 · 依赖管理 · 性能与安全**五个切面的实测快照，数字全部本次重测。
> 两份建议对照读：老文件的「待办」里有一部分已在 CHANGELOG 的 Unreleased 中完成。

> ⚠️ **历史快照（基线 `a03bbd9` 之前）**：本文写于 2026-09-22 的**改动之前**，
> 结论与数字都对应那一刻的代码。同日后续的提交已经修掉了其中相当一部分，**不要按它判断现状**；
> 当前数据看 README 的「测试与验证」与 `docs/ROADMAP.md` 第 0 节。
>
> **本快照之后已被修掉的条目**（同日提交，逐条见 CHANGELOG 与 devlog 批次 1–11）：
> - P0「远程 CI 从未执行」→ 代码侧阻塞已清（三个浏览器脚本跨平台化、覆盖率门槛、
>   `e2e-live` 与 `backend-postgres` 作业）；**账号账单锁仍在，远端仍未实证**
> - P1「PG 生产无 FTS，搜索退化为 LIKE 全表扫」→ 已补 `pg_trgm` + 3 条 GIN
> - P1「前端 views 层 4049 行零单测」→ **22 个视图全部有 spec**（333 条）
> - P1「依赖漏洞审计从未执行」→ 与 CI 账单锁同源，仍未解
> - P2「`is_production` fail-open」→ `APP_ENV` 改为白名单，未知值拒绝启动
> - P2「公开 GET 无 ETag / Cache-Control」→ 已补（含 `Vary: Authorization`）
> - P2「标签全量加载」→ 服务端过滤 + 上限 200（真正的分页未做）
> - P2「CSS 60KB 未复查」→ 已复查并结案：**gzip 10.8KB，无需优化**
> - P2「缺 HSTS」→ 已加（条件式，按 `X-Forwarded-Proto`）
> - P2「article 路径 1339 行未拆分」→ 已按读写拆分（4.4）
>
> 仍然成立的：无 ETag 之外的缓存层、`seed.py` 混在服务层、锁文件手工维护（无 Dependabot）。

---

## 0. 总体判定

**健康度：良好，且工程纪律明显高于同规模个人项目的平均水平。**

最硬的证据不是"测试多"，而是**把容易出错的地方做成了机器可验证的约束**：
生产安全门禁拒绝带默认密钥启动、依赖锁定且有测试守着同步、import-linter 把分层方向变成
可执行的契约、端到端同时覆盖两种数据库方言。这四件事合起来，让"改动是否会悄悄破坏东西"
这个问题在大部分路径上有答案。

真正的问题集中在两处：**验证链条有一整段从未真正执行过（远程 CI）**，以及
**前端 views 层 4049 行没有任何单测**。前者是流程空洞，后者是覆盖空洞——
都不影响当前运行，但决定了"再往前走多远会开始失控"。

---

## 1. 架构与目录结构

### 分层现状

```
backend/src/app/
├── api/v1/         路由层（含 deps.py 统一依赖与限流常量）
├── services/       业务层（article 642 / seed 408 / attachment 401 / taxonomy 234 ...）
├── repositories/   数据访问层（article_repository.py 697 行，最大文件）
├── models/         ORM 模型（21 处 index=True）
├── schemas/        Pydantic v2 出入参
├── db/             会话与迁移脚手架
└── utils/          security 225 / ratelimit 等（import-linter 契约：工具层必须保持叶子）
```

前端 `frontend/src/`：views（前台 13 + 后台 9）/ components / composables /
stores（Pinia: auth/site/theme）/ api / router / utils / types / styles。

### 做得好的地方

- **分层名副其实**：`api → services → repositories → models` 的调用方向由
  `backend/.importlinter` 机器校验（2 契约 kept / 0 broken），不是靠自觉。
- **应用工厂与测试同构**：`create_app()`（main.py:238）被测试复用，测试环境的 app
  与生产同一条装配路径——这类设计能挡住"只在生产才出现的中间件问题"。
- **异常契约完整**：`_register_exception_handlers`（main.py:121-193）覆盖
  DomainError / IntegrityError / RequestValidationError / Exception 四类，
  兜底 handler 返回统一信封 + `request_id`。
- **目录职责清晰**：`backend/scripts/`（运维脚本）、`tools/`（端到端与浏览器脚本）、
  `deploy/`（Dockerfile ×2 + nginx.conf）、`docs/`（设计与评估）边界干净，没有混放。

### 结构风险

| 问题 | 证据 | 影响 |
|---|---|---|
| 文章路径过度集中 | `repositories/article_repository.py` 697 行 + `services/article_service.py` 642 行 = **1339 行** | 4.4「读写拆分」未做；`/articles/{slug_or_id}` 的数字/字符串双解析、FTS 回退、分页排序都在这一条链上，改动定位成本高 |
| 演示数据混在服务层 | `services/seed.py` 408 行 | 生产镜像里躺着 4 篇假文章的生成逻辑，靠 `SEED_DEMO_DATA` 开关隔离（门禁会拦），但仍是杂音 |

---

## 2. 代码质量

### 量化现状（本次实测）

| 项 | 数值 |
|---|---|
| 后端源码 / 测试 | 83 文件 8,827 行 / 32 文件 8,108 行（**测试代码量约为源码的 92%**） |
| 前端 src / 测试 | 45 个 .vue + 67 个 .ts = 12,424 行 / 20 个 spec 2,886 行 |
| 静态检查 | ruff / ruff format / eslint / vue-tsc 全干净 |
| 遗留标记 | **0 处** TODO / FIXME / HACK |
| 运行时验证 | 442 pytest 全绿 · 200 vitest 全绿 · e2e SQLite 62/62 · PG 60 pass + 1 skip |

后端测试不是"凑数"：`test_dependency_lock.py`、`test_production_gate.py`、
`test_env_example.py`、`test_deploy_config.py` 这四个是**元测试**——
守的是"配置与依赖不会悄悄漂移"，比多几个 CRUD 用例有价值得多。

### 覆盖空洞（唯一实质缺口）

| 区域 | 规模 | 测试 |
|---|---|---|
| 前端 `views/` | **22 个组件 / 4,049 行** | **0 个 spec** |
| 最大三个 view | `ArticleEditView` 619 行、`ArticleDetailView` 400 行、`HomeView` 346 行 | 无任何单测 |

前端 200 个用例集中在 `api/`（http 320 行）、`composables/`、`utils/`、`components/`。
views 层的回归完全依赖 34 项浏览器冒烟——冒烟能抓"页面白屏"，抓不到
"编辑页改了状态却没同步草稿"这类逻辑回归。

---

## 3. 依赖管理（本项目最强的一项）

### 后端

- **版本策略是"下界 + 兼容上界"**，且 `pyproject.toml` 里写明了为什么：
  曾经因为只写 `>=`，实际装进来 bcrypt 5.0、aiosmtplib 5.1、pillow 12.3、pytest 9.1，
  各跨一到两个主版本——"只是恰好没坏"。上界把升级变成显式动作。
- **`requirements.lock` 锁定 51 个包（含全部传递依赖）**，Dockerfile 用它安装：
  `COPY pyproject.toml README.md requirements.lock ./` → `pip install -r requirements.lock`
  → `pip install --no-deps .`（`--no-deps` 保证装进镜像的就是 lock 里的版本）。
- 有 `test_dependency_lock.py` 守着 lock 与 pyproject 的同步。
- `backend/uv.lock`（误产物）已不在版本库内。

### 前端

- 运行时依赖**只有 7 个**：axios、dompurify、highlight.js、marked、pinia、vue、vue-router。
- `package-lock.json` 在库，`engines.node >= 20` 有约束。

### 残余风险

1. **锁文件是手工维护的**，没有自动更新流程（Dependabot/Renovate 均未配置）——长期看会漂移。
2. **漏洞审计从未真正执行**：CI 的"依赖·漏洞审计" job 与其余 4 个 job 一样，因账单锁从未启动。
3. pinia 停在 2.x（Vue 3.5 时代主流已到 3.x），落后约一个主版本。

---

## 4. 性能风险

### 做得好的地方

- **N+1 控制是刻意的、且有注释**：`article.author/category/series` 用 `lazy="joined"`、
  `tags` 用 `lazy="selectin"`、版本历史**刻意不预载**（models/article.py:91-100 写明理由）。
  这是"知道自己在做什么"的写法，不是碰巧没踩坑。
- 模型层 21 处 `index=True`，覆盖主要外键与排序字段。
- 搜索有兜底：FTS 索引缺失时探测到并安静退回 LIKE，不会因为"没建索引"变成 500。

### 风险清单

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1 | **PostgreSQL 部署下全文检索退化为 LIKE 全表扫** | `articles_fts` 是 SQLite FTS5 虚拟表；PG 下该表不存在，探测逻辑（article_repository.py:121）静默回退 LIKE，对 title/summary/content_md 三列做 `%x%` 匹配（:281-285）。PG 端到端 E03 实测：FTS 虚拟表=无 | compose 生产环境正是 PostgreSQL。文章上百后每次搜索 = 三列全表扫描，`LIKE '%x%'` 永远走不了索引 |
| P2 | 公开 GET 完全没有缓存头 | 全仓 grep 无 `ETag` / `Cache-Control`（仅 nginx 对静态资源有） | 每次访问都全量查库；二次访问与 CDN 命中率为 0 |
| P2 | 标签列表全量加载 | taxonomy_service.py:121 `limit` 为 None 时直接 `tags.list_all()` | 标签上百后首屏等待（ROADMAP 1.7 已列，未做） |
| P2 | CSS 60KB，比 09-12 的 52KB 涨 16% | 本次 build 实测 | Tailwind `content` 扫描范围与 typography 插件从未复查（ROADMAP 3.6 未做） |
| P3 | 列表无缓存层 | 无 LRU/Redis（ROADMAP 3.8 未做） | 单机 SQLite/PG 都能扛住当前量，暂不迫切 |

---

## 5. 安全风险

### 结论：**没有 P0**。这在小团队个人项目里不多见。

### 防护到位的地方

- **生产安全门禁（fail-closed）**：`config.check_production_safety()`（config.py:205-259）
  检查默认 JWT 密钥 / 密钥短于 32 字节 / 默认管理员口令 / `DB_AUTO_CREATE` / `SEED_DEMO_DATA` /
  `DEBUG`，命中任意一条即由 `main.py:229` **抛 RuntimeError 拒绝启动**，并把逐条修正方法写进错误信息。
- **CORS 默认收敛**：仅放行 localhost:5173 / 127.0.0.1:5173 / localhost:4173（config.py:63）。
- **注入面干净**：全仓唯一的 SQL 字符串拼接是 `article_repository.py:266`
  `text(f"SELECT count(*) AS n {where}")`，但 `where` 由代码内的状态枚举拼装（:252），
  用户输入全部走 `:query` / `:like_pattern` 绑定参数。
- **上传**：扩展名白名单每次重读 settings（attachment_service.py:186），SVG 刻意排除。
- **限流覆盖到"可被脚本滥用"的四类入口**（deps.py:190-196）：
  登录 5/60s、评论 5/60s、点赞 20/60s、搜索 30/60s，且有总开关。
- **响应头**（nginx.conf:33-45）：`nosiff` / `X-Frame-Options: SAMEORIGIN` /
  `Referrer-Policy` / CSP（含 `frame-ancestors`，无 `unsafe-eval`）。

### 残余风险

| 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|
| P2 | **生产判定 fail-open** | `is_production` = `app_env.lower() == "production"`（config.py:194）。`APP_ENV` 写成 `prod`、`production `（带空格）等，整个安全门禁**静默失效**，而服务照常启动 | 唯一拦住"带默认密钥上生产"的机制被绕过且不报警 |
| P2 | 缺 HSTS | nginx.conf 无 `Strict-Transport-Security` | 仅 HTTPS 部署下有意义，中等 |
| P3 | 锁文件手工维护 + 漏洞审计从未跑 | 见依赖管理节 | 长期漂移与 CVE 盲区 |

---

## 6. 问题分级汇总

| 级别 | 问题 | 具体表现 | 影响 |
|---|---|---|---|
| **P0（流程）** | 远程 CI 从未真正执行 | run #35 五个 job 全 failure，但总耗时 3 秒、零 step —— 账号账单锁，非代码问题 | Linux 侧的迁移升降级、真实浏览器、依赖审计、前端构建**四类验证长期空白**；唯一门禁是本地手动跑 |
| **P1** | PG 生产无 FTS，搜索退化为 LIKE 全表扫 | FTS5 仅 SQLite 有；PG 下静默回退 | 文章规模上去后搜索成为硬瓶颈，且是静默退化、无告警 |
| **P1** | 前端 views 层 4049 行零单测 | 22 个 view、0 个 spec；最大三个 619/400/346 行 | 改 UI 只能靠 34 项冒烟；逻辑回归抓不到 |
| **P1** | 依赖漏洞审计从未执行 | 与 CI 同源 | CVE 盲区 |
| **P2** | article 路径 1339 行未拆分 | repository 697 + service 642 | 改动定位成本高，4.4 未做 |
| **P2** | 公开 GET 无 ETag/Cache-Control | 全仓 grep 为空 | 每次全量查库，CDN 命中率 0 |
| **P2** | 标签全量加载 | taxonomy_service.py:121 | 标签上百后首屏白等 |
| **P2** | CSS 60KB 未复查 | 较 09-12 +16% | 可能砍掉 10-20KB |
| **P2** | `is_production` fail-open | config.py:194 | 安全门禁可被一次笔误绕过 |
| **P2** | 缺 HSTS | nginx.conf | HTTPS 部署下的中等风险 |
| **P3** | seed.py 408 行演示数据混在服务层 | services/seed.py | 生产镜像杂音 |
| **P3** | 锁文件无自动更新 | 无 Dependabot/Renovate | 长期漂移 |

---

## 7. 下一步行动建议（按优先级）

| # | 动作 | 预期收益 | 工作量 |
|---|---|---|---|
| 1 | **恢复 CI**：处理账号账单；若短期内无法解，把 5 个 job 的核心步骤固化成本地脚本（`tools/` 已有 smoke/interaction/full-check，只差"迁移升降级"与"依赖审计"两条） | 四类验证从"从未执行"变为"每次提交执行"，是后续所有改动能放心合入的前提 | 账号处理为主；代码侧 0.5 天 |
| 2 | **PG 全文检索**：加 `tsvector` + GIN 索引（迁移里按方言分支：SQLite 建 FTS5、PG 建 tsvector），service 层按方言选路径，并断言"PG 下不再回退 LIKE" | 搜索从三列全表扫变成索引命中；顺带消除"静默退化无告警" | 1–2 天 |
| 3 | **补 views 层单测**：先做 `ArticleEditView`（草稿/发布/标签/封面四个状态流转）、`ArticleDetailView`（系列导航与上下篇）、`HomeView`（分页与空态）三个 | 前端最大的覆盖空洞收口；后续 UI 改动有安全网 | 1–2 天 |
| 4 | **公开 GET 加 ETag / `Cache-Control: public, max-age=60`**：只给前台只读接口，后台与鉴权接口显式 `no-store` | 二次访问与 CDN 命中；数据库压力下降 | 0.5–1 天 |
| 5 | **`is_production` 改 fail-closed**：`APP_ENV` 用白名单校验（`development/testing/production`），未知值直接报错退出，而不是"不等于 production 就等于开发" | 消除"一次笔误绕过全部安全门禁"的风险 | 1 小时 |
| 6 | **依赖自动更新 + 漏洞审计**：配 Dependabot/Renovate；审计先在本地跑（`pip-audit` / `npm audit`）并纳入第 1 条的脚本 | 锁文件不再手工漂移，CVE 有出口 | 0.5 天 |
| 7 | **article 读写拆分**：`ArticleQueryService`（读）+ `ArticleWriteService`（写），只搬移不改写，测试只增不改 | 1339 行的改动定位成本降下来 | 1–2 天（需测试护持，风险较高，建议放在 3 之后） |
| 8 | 标签分页 + CSS 复查 | 首屏 TTFB、产物体积 | 0.5–1 天 |

**顺序建议**：1 → 5 → 4 → 3 → 2 → 6 → 8 → 7。
1 和 5 都是"让后面的改动能被验证/不会被悄悄绕过"的前置；7 收益明确但风险最高，放最后。

---

## 8. 本次评估的数据来源与边界

- 实测：文件规模、目录结构、配置与安全门禁、限流挂载点、索引与懒加载策略、
  SQL 拼接面、nginx 响应头、依赖锁定方式、前端产物体积（`npm run build`）。
- 引用昨日实测（2026-09-21）：442 pytest / 200 vitest / e2e 两方言 / 容器 healthy。
- **本次未跑**：浏览器三脚本（smoke 34 / interaction 22 / full-check 41），需先 `make dev`；
  远程 CI（账单锁）。这两块的结论沿用历史记录，未重新验证。
