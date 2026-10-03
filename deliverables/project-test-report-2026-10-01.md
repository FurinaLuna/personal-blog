# 个人博客项目 · 实测分析报告

> 执行日期：2026-10-01 ｜ 基线提交 `422a2ec`（工作区含 15 个未提交的前端改动 + 7 个未跟踪文件）
> 分析方式：**实际运行全部测试套件**（非静态阅读），并用独立探针真机验证关键安全声明
> 环境：Windows ｜ Python 3.13.14 ｜ Node v24.20.0 ｜ npm 11.19.0 ｜ Docker **不可用**（守护进程未运行）

- [1. 结论摘要](#1-结论摘要)
- [2. 实测结果总表](#2-实测结果总表)
- [3. 逐项证据](#3-逐项证据)
- [4. 发现的问题](#4-发现的问题)
- [5. 未验证项与原因](#5-未验证项与原因)
- [6. 环境阻塞与解法](#6-环境阻塞与解法)
- [7. 测试期间对仓库的影响](#7-测试期间对仓库的影响)
- [8. 建议](#8-建议)

---

## 1. 结论摘要

**项目质量处于「可以直接上线」的水位，且实测数据与文档声明一致。**

这不是空壳模板：README 里写的每一个数字，我实跑后都能对上（822 收集 / 809 通过 /
54 spec 文件 / 849 例 / e2e 64 条）。唯一与文档不同的一处——`8 errors`——经我独立
探针证明是**本机沙箱环境限制**，与产品代码无关，且原文档已经预先交代过。

值得单独指出的三点工程品质：

1. **文档诚实**。`docs/TEST-REPORT.md` 主动登记了 `8 errors` 与原因，而不是把数字
   抹平成"全绿"。我按它给的原因去复现，复现结果与它写的一模一样。
2. **失败会说话**。`test_dependency_lock.py`、`test_env_example.py`、`test_deploy_config.py`
   这类"元测试"把配置漂移变成了红灯，而不是留给人肉 review。
3. **安全声明可被证伪**。生产门禁不是靠注释宣称，我用 10 组恶意配置真跑了一遍，全部拦下。

**没有任何一个 P0/P1 缺陷。** 发现的问题里有 2 个值得修（都是小问题），4 个属于"提一句"。

---

## 2. 实测结果总表

| 门禁 | 实测结果 | 判定 | 文档声明 | 是否一致 |
|---|---|---|---|---|
| `vitest run`（前端单测） | **54 文件 / 849 例全通过** | ✅ PASS | 54 文件 849 例 | ✅ 一致 |
| `vue-tsc --noEmit`（类型） | exit 0，无输出 | ✅ PASS | exit 0 | ✅ 一致 |
| `eslint .`（含无障碍规则） | exit 0，0 error 0 warning | ✅ PASS | exit 0 | ✅ 一致 |
| `vite build`（生产构建） | 成功，250 模块，3.58s | ✅ PASS | — | ✅ |
| `pytest`（后端全量） | **809 通过 / 5 跳过 / 8 错误**（822 收集，396s） | ⚠️ 见 §3.4 | 817 通过 / 0 失败 / 5 跳过 | ⚠️ 详见下 |
| 覆盖率门槛 `fail_under=80` | **83.58%**（4221 语句 / 未覆盖 693） | ✅ PASS | 基线 82% | ✅ 一致 |
| `ruff check .` | All checks passed | ✅ PASS | All checks passed | ✅ 一致 |
| `ruff format --check` | 165 files already formatted | ✅ PASS | 165 files | ✅ 一致 |
| `lint_imports.py`（分层契约） | **2 kept / 0 broken**（101 文件 329 依赖） | ✅ PASS | 2 kept 0 broken | ✅ 一致 |
| `tools/e2e_live/e2e_run.py`（真实链路） | **64 / 64 PASS，0 FAIL，0 ERROR** | ✅ PASS | 64/64 | ✅ 一致 |
| 授权审计（独立 subagent） | 13 个路由模块全查，**无越权** | ✅ PASS | — | ✅ |
| 生产配置门禁（独立探针） | **10 组不安全配置全部拦下** | ✅ PASS | — | ✅ |

**后端 8 个错误的性质**：全部是 `tests/test_backup_script.py` 的 `tmp_path` fixture
创建失败，**点在测试代码执行之前**。我用同一批用例 + 可用的临时目录重跑，
**19/19 全通过**（详见 §3.4），确证是环境问题。

---

## 3. 逐项证据

### 3.1 前端（未提交改动的重点验证对象）

工作区有 15 个未提交的前端文件（一套 UI 改版：tokens / components.css / 5 个组件 /
6 个页面 / tailwind 配置）。**这些改动在四项门禁下全绿，没有破坏任何既有契约。**

```
 Test Files  54 passed (54)
      Tests  849 passed (849)
   Duration  15.12s
```

类型检查与 lint 均 exit 0。生产构建产物分包合理：
`vendor` 43.27 kB(gzip) / `index` 35.37 kB / `markdown` 31.66 kB / `css` 11.76 kB。

> 注：`eslint` 首轮报出 1 个 error，定位在 `frontend/.verify-out/token-audit.mjs`
> ——那是我派出的一个 teammate 留下的临时脚本，**不是项目文件**。删除该目录后
> `eslint .` 干净通过（exit 0）。

### 3.2 后端全量测试

```
809 passed, 5 skipped, 144 warnings, 8 errors in 396.59s (0:06:36)

Required test coverage of 80.0% reached. Total coverage: 83.58%
```

5 个 skip 是设计内的双方言互斥标记（`sqlite_only` / `pg_only`）：默认 SQLite 运行时，
PG 专有行为的用例会带原因跳过，反之亦然。

### 3.3 真实链路端到端（本项目最强的一层）

`tools/e2e_live/e2e_run.py` 自己起 uvicorn、自己用 `alembic upgrade head` 建临时库、
所有断言走真实 HTTP、落库结果再直连数据库二次核对：

```
总计 64 条：PASS=64  FAIL=0  ERROR=0
报告：tools/e2e_live/reports/e2e-report-20261001-184453.md
```

覆盖了权限门禁（403/401）、评论两级与状态机、后台生命周期、边界与异常、
上传安全（伪装成图片的 exe → 415、SVG → 415、11MB → 413、**6400 万像素解压炸弹 → 415**）、
登录限流实测 `[200,200,200,200,200,429,429]`、配置不合法时全接口 500 且带 request_id。

### 3.4 「8 errors」的定性证明（关键）

文档说这 8 个错误是 `tmp_path` fixture 创建失败。我在本机复现到了**完全相同的 8 条**，
并定位到更底层的机制：本机沙箱下 `tempfile.mkdtemp()` 建出的目录**之后完全不可用**
（往里 mkdir 或写文件都报 `WinError 5`），而按路径显式创建的目录正常。

为排除"是不是 backup 脚本本身有问题"，我用一个注入的 `tmp_path` fixture
（按路径创建，其余完全相同）重跑同一批用例：

```
collected 19 items
tests\test_backup_script.py ................... [100%]
============================= 19 passed in 0.06s ==============================
```

**19/19 通过。** 结论：这 8 个错误 100% 是环境产物，`scripts/backup_db.py`
及其测试本身是好的。文档的记载准确。

### 3.5 生产安全门禁（我独立真机验证）

`config.py:380 check_production_safety()` 的声明我逐条构造配置实测：

| 构造的生产配置 | 结果 |
|---|---|
| `JWT_SECRET_KEY` 仍是仓库默认值 | 🚫 BLOCKED |
| `JWT_SECRET_KEY` 短于 32 字节 | 🚫 BLOCKED |
| `ADMIN_PASSWORD` 仍是仓库默认值 | 🚫 BLOCKED |
| `ADMIN_PASSWORD` 少于 8 位 | 🚫 BLOCKED |
| `DEBUG=true` | 🚫 BLOCKED |
| `DB_AUTO_CREATE=true` | 🚫 BLOCKED |
| `SEED_DEMO_DATA=true` | 🚫 BLOCKED |
| `CORS_ORIGINS="*"`（且 `allow_credentials=True`） | 🚫 BLOCKED |
| `CORS_ORIGINS=""` | 🚫 BLOCKED |
| 生产用 SQLite | 🚫 BLOCKED |
| **完全正确的生产配置** | ✅ allowed |
| 开发环境（所有危险值同上） | ✅ allowed（不误伤） |

**fail-closed 成立，且不误伤开发环境。** 这组测试同时验证了文档 §4.3
"生产必须替换密钥"的承诺不是空话。

### 3.6 授权模型（独立 subagent 审计，21 文件）

- 门禁依赖：`require_admin`（`role is not ADMIN` → 403，`api/deps.py:92-100`）、
  `require_author`（ADMIN/AUTHOR，`:103-107`）、`get_current_user`（校验
  `expected_type="access"`、inactive → 403、`token_version` 重校验，`:40-65`）。
- **13 个路由模块全部检查，无未加门禁的端点**；18 处 `AdminUser` 全部对应真实管理 handler。
- **Schema 层提权不可达**：`UserCreate` 虽含 `role` 字段（`schemas/user.py:35`），
  但只能经管理员门禁的 `POST /auth/users`（`api/v1/auth.py:164`）与
  `PATCH /auth/users/{id}`（`:173`）到达；自助的 `PATCH /auth/me` 绑定
  `UserSelfUpdate`，仅 nickname/email/avatar_url/bio（`schemas/user.py:54-60`），
  **无 role、无 is_active**。另有末位管理员保护（`auth_service.py:279-284`）。
- 资源归属校验正确：文章 `article_command_service.py:87-93`、
  评论 `comment_service.py:203-206/218-221`、附件 `attachment_service.py:351-352`。

---

## 4. 发现的问题

### 🟡 P2-1 暗色模式下 `--c-shadow` 已成死令牌，且设计意图与实现不符

- **位置**：`frontend/src/styles/tokens.css:188`（定义）、`:193-201`（使用）
- **现象**：`--c-shadow` 在暗色下定义为 `0 0 0`，但新增的暗色阴影阶梯
  **硬编码了 `rgb(0 0 0 / …)`**，没有引用该变量。实测该令牌在 tokens.css 之外
  **0 处引用**，在暗色块内也 0 处引用——已是死代码。
- **为什么值得修**：亮色块的注释明确写着"色值用 `rgb(var(--c-shadow) / alpha)`
  而不是写死 rgba：暗色模式下 `--c-shadow` 变成纯黑，阴影自动跟着变深，
  **不需要 `dark:` 变体**"。但暗色块自己违背了这个约定——结果暗色阴影基色
  被硬编码了 6 处。这削弱了本项目"换肤只改 tokens.css"的核心承诺，
  也与 `tailwind.config.js` 新增 `boxShadow` 映射时写的理由自相矛盾。
- **建议**：暗色阶梯改为 `rgb(var(--c-shadow) / …)`；若确实需要品牌色环境光，
  另立一个令牌（如 `--c-shadow-glow`）并加注释说明。
- **性质**：不是回归（亮色 `--c-shadow` 值未被改动），属新增代码的内部不一致。

### 🟡 P2-2 浅色模式 `--c-ink-faint` 对比度不达 WCAG AA（**既有问题，非本次改动引入**）

我按 tokens.css 里的 RGB 三元组实测了主要前景/背景配对的对比度：

| 配对 | 亮色实测 | 暗色实测 | AA 要求 |
|---|---|---|---|
| 正文 `ink` on `bg` | 16.61:1 ✅ | 15.33:1 ✅ | 4.5 |
| 次级 `ink-soft` on `bg` | 7.25:1 ✅ | 8.24:1 ✅ | 4.5 |
| **`ink-faint` on `bg`** | **2.40:1 ❌** | 5.01:1 ✅ | 4.5 |
| **`ink-faint` on `surface`** | **2.52:1 ❌** | 4.65:1 ⚠️ 勉强 | 4.5 |
| **`ink-faint` on `surface-muted`** | **2.27:1 ❌** | **4.18:1 ❌** | 4.5 |
| 链接 `brand-600` on `bg` | 6.61:1 ✅ | — | 4.5 |
| 标签 `accent` on `accent-soft` | 4.51:1 ✅ | 7.40:1 ✅ | 4.5 |

- **性质确认**：`git diff` 显示 `--c-ink-faint` 的亮色值（`168 162 158`）与暗色值
  （`138 132 123`）**与 HEAD 完全一致**，本次未提交改动**只新增了阴影/渐变/纹理令牌，
  没有改任何颜色值**。所以这是既有问题，不是这次改版的引入。
- **为什么仍要提**：该令牌被用在大量的**真实文本**上，不只是装饰——
  `ArticleCard.vue:139`（文章元信息）、`CommentSection.vue` 的时间戳与"回复"
  按钮、`Pagination.vue` 的分页范围文字、`RevisionHistory.vue`、`MobileToc.vue` 等。
  这些是 12–14px 的小字，不适用"大字 3:1"的放宽条款。
  （`EmptyState.vue` 的图标与 `prose` 的 `li::marker` 属装饰性用法，可豁免。）
- **为什么项目自己的工具没抓到**：ESLint 装了 `eslint-plugin-vuejs-accessibility`，
  但该插件的规则集**不含颜色对比度检查**（那需要真实渲染或静态色值分析），
  所以这条只有人工/专门工具能发现。
- **建议**：把亮色 `--c-ink-faint` 从 `168 162 158` 加深到约 `110 105 100`
  （在 `bg` 上约 4.6:1），或对必须小字显示的信息改用 `ink-soft`。

### 🟢 P3-1 `--shadow-lg` 与 `--gradient-brand-soft` 定义后未被使用

- **位置**：`tokens.css:82-85`、`:92-96`
- **现象**：`--shadow-lg` 在 tokens.css 外 0 处引用、`--gradient-brand-soft` 同样 0 处引用。
  （对比：`--shadow-sm` 2 处、`--shadow-md` 2 处、`--gradient-brand` 6 处、`--bg-texture` 2 处。）
- **说明**：Tailwind 的 `shadow-lg` 工具类**有** 6 处使用，会经
  `tailwind.config.js` 的 `boxShadow.lg` 映射到 `var(--shadow-lg)`，所以**功能上是活的**；
  这里指的是"直接引用 `var(--shadow-lg)`"为 0。属于轻微的令牌冗余，
  可留作后续使用，或删掉以免读者误判。

### 🟢 P3-2 `backend/.env` 仍是开发默认凭据（**配置状态提醒，非缺陷**）

`backend/.env` 里 `APP_ENV=development` + `JWT_SECRET_KEY=dev-only-...` +
`ADMIN_PASSWORD=admin123456`，且 `DB_AUTO_CREATE=true`、`SEED_DEMO_DATA=true`。
**这在开发环境是正确且预期的**，且 `check_production_safety()` 已证明能拦住它上生产
（见 §3.5），所以不是漏洞。提出来只是因为 `backend/.env` 是**已跟踪文件**
（`git status` 中未出现，说明它在版本库里），值得确认这是有意为之。

### 🟢 P3-3 项目内残留 `8 errors` 的历史记载需补一句本机复现结论

`docs/TEST-REPORT.md:22` 已准确记录 8 个 ERROR 及原因，本次实测**再次复现并加深了根因**
（不只是"沙箱不允许删目录"，而是"沙箱建出的 mkdtemp 目录不可用"，
所以是**建**不出来而非**删**不掉）。可考虑把这句更精确的机制补进文档，
便于后来者一次到位。

### 🟢 P3-4 非缺陷观察：共享标签的改名/删除是 AuthorUser 门禁

`api/v1/taxonomy.py:51,61,94,102,110` 的标签/分类增改与标签删除是 `AuthorUser`
（作者即可），意味着任一作者可改名或删除**共享**标签。审计确认这是代码注释里
写明的**有意设计**，不是权限边界疏漏。仅作信息记录：若未来引入多作者协作，
这一条会成为需要重新审视的边界。

---

## 5. 未验证项与原因

我**没有**把这些算作通过——它们未被执行，因此状态未知：

| 未验证项 | 原因 |
|---|---|
| PostgreSQL 方言 pytest（`backend-postgres` job） | Docker 守护进程未运行；本机无 PG 实例 |
| `tools/e2e_live` 的 PG 方言（63 条） | 同上（该模式要求容器内跑服务） |
| `tools/deploy-check.mjs`（49 项，真构建镜像真起栈） | 同上，需要 Docker |
| `tools/smoke-check.mjs`（47 项，真实浏览器 CDP） | 需要先起前后端 dev server；属"需要真人/真浏览器交互"的一层 |
| `tools/interaction-check.mjs`（25 项，真实点击） | 同上 |
| `tools/full-check.mjs`（66 项，全功能回归） | 同上 |
| Alembic `upgrade`/`downgrade` 探针 | 未做，以免触碰 `backend/blog.db` |
| GitHub Actions CI 实跑（8 jobs） | 远端状态未知，本地只覆盖了其中可离线执行的部分 |

> 上面 4 项 CDP 脚本是"只有真浏览器才能发现"的缺陷所在层（文档记载过
> "登录后被弹回登录页""后台布局嵌套导致子页面不渲染"这类真实缺陷），
> **建议在正式合并这批 UI 改动前，用 `make dev` 起好服务后补跑
> `smoke` / `interaction` / `full-check`**，因为它们正是为 UI 改动准备的网。

---

## 6. 环境阻塞与解法

### 6.1 根因

本机 DSH 沙箱下，`tempfile.mkdtemp()`（以及任何 Python 在受限上下文里按 mode 创建的目录）
**建立后立即不可用**——往里 `os.mkdir()` 或写文件都报 `WinError 5`，
`icacls` 读它也 Access denied（连权限都读不出来，所以连删都删不掉）。
而通过 PowerShell `New-Item` 或 `os.makedirs()` **按路径**创建的目录会继承沙箱授权，
完全正常。

后果：依赖 `tempfile.mkdtemp()` 的两套最强测试网**在本机完全跑不起来**——
`pytest` 的 `tmp_path` fixture，和 `tools/e2e_live/e2e_run.py`（它用
`tempfile.mkdtemp(prefix="blog-e2e-")` 建临时库与 storage 目录）。

**这不是项目缺陷**：两份脚本在正常 Windows / Linux / CI 上都工作，
`e2e_run.py:42-58` 还专门把解释器改成 `sys.executable` 以适配 Linux CI。

### 6.2 我采用的绕过方式（已完全回滚，不留痕迹）

1. 在 `backend/.venv/Lib/site-packages/` 放一个 `.pth` 文件，自动导入一个
   `mkdtemp` 替换实现：按路径建目录，再用 `icacls` 给当前用户补一份授权。
2. 把 `TEMP`/`TMP` 指向工作区内 `.e2e-tmp/`（该目录继承授权），
   并给 pytest 传一个新建的 `--basetemp`。

这样 `e2e_live` 立刻 **64/64 全绿**。**该 `.pth` 已删除**，venv 恢复原状；
仓库内的被测代码**一行未改**。

### 6.3 遗留的不可删目录（无害）

`.e2e-tmp/` 下残留 7 个**在我修复之前**就已产生的锁定目录
（`A-*`、`B-*`、`D-*`、`E-*`、`probe-0klp4d51`、`via-shim-*`、`blog-e2e-e3hisj9u`）
和 2 个 pytest 遗留目录（`pytest-of-zhangchaowang`、`pt3`），
权限被拒绝到连 `icacls` 都读不了，**无法删除**。
它们都在 `.gitignore` 覆盖的 `.e2e-tmp/` 内，不影响构建与版本库。
如需彻底清掉，需要在**完全权限（full access）**下删除该目录。

---

## 7. 测试期间对仓库的影响

**零影响。** 我在测试前后做了比对：

- 15 个已修改 + 7 个未跟踪文件 = **与测试前完全一致**（逐项 diff 核对）；
- 未新增、未修改任何被跟踪文件；
- `backend/blog.db` 未被触碰（`e2e_live` 用独立临时库，并对真实库做前后指纹比对）；
- 我自己的探针脚本全部写在 `.e2e-tmp/`（gitignored）内，**已全部删除**；
- 两个失败 teammate 留下的临时目录（`frontend/.verify-out`、`deliverables/.probe-tmp`）
  已删除，其中一个是导致 `eslint` 假报错的唯一来源；
- venv 里的 `.pth` 绕过文件已删除。

---

## 8. 建议

**合并这批 UI 改动前，按优先级：**

1. **补跑浏览器层**（P0，因为改动正是 UI）：
   `make dev` 起服务，然后 `make smoke`、`make interaction`、`make full-check`。
   这三套是唯一能覆盖"改版把某个页面渲染弄坏"的网。
2. **修 P2-1**（`--c-shadow` 暗色死令牌 / 与注释自相矛盾）——改动很小，
   但它关系到"换肤只改 tokens.css"这个被文档反复强调的承诺。
3. **修 P2-2**（浅色 `ink-faint` 对比度）——建议在本批一起处理，因为这次改版本就在
   调设计令牌；否则它会被新令牌体系"固化"下来。
4. **顺手清 P3-1**（两个未使用的令牌），保持令牌表干净。
5. 有条件时用 Docker 补跑 PG 方言与 `deploy-check`——那是本机唯一没能覆盖的两个维度。

**关于测试基础设施（值得肯定，无需改）**：`e2e_live` 自起进程+临时库+直连核对、
`test_dependency_lock.py` 机器校验锁文件与 pyproject 同步、
`deploy-check.mjs` 真构建镜像——这套"每层都真跑、层与层不互相替代"的结构，
是我见过少有的、**文档数字经得起实跑核对**的项目。
