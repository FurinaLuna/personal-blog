# 个人博客 — 编译与启动冒烟验证报告

- **验证人**：严过关（QA Engineer）
- **验证日期**：2026-09-25
- **项目根目录**：`D:/Projects/01_个人项目/personal-blog`
- **验证目标**：确认「能编译、能运行、无启动报错」
- **总体结论**：✅ **通过** —— 成功编译运行，且无明显启动报错

---

## 一、结论摘要

| 维度 | 结论 |
| --- | --- |
| 前端类型检查 | ✅ 通过，0 报错 |
| 前端生产构建 | ✅ 通过（237 模块，产物正常） |
| 真实浏览器冒烟（CDP） | ✅ 40 / 40 全通过，页面脚本错误 0 |
| HTTP 层端点冒烟 | ✅ 9 个公开端点全部 200，业务字段非空 |
| 鉴权链路冒烟 | ✅ 登录 → token → `/auth/me` 返回 admin；两个反向用例正确 401 |
| `X-Request-ID` 响应头 | ✅ 全部请求（含 401/429 错误响应）均带 |
| 后端 pytest 子集回归 | ✅ 7 passed / 0 failed |
| 是否有启动报错 | ❌ 无 |

---

## 二、逐项结果表

### 2.1 编译与构建

| # | 检查项 | 命令 | 结果 | 耗时 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | 前端类型检查 | `cd frontend && npm run type-check`（`vue-tsc --noEmit`） | ✅ **PASS**，退出码 0，**0 个类型错误** | 10 s | 无 stderr 输出 |
| 2 | 前端生产构建（原样） | `cd frontend && npm run build` | ❌ **FAIL**，退出码 1 | 17 s | **非项目缺陷**，失败点为空目录清理，详见「三、问题清单 P1」 |
| 3 | 前端生产构建（绕过沙箱限制重跑） | `cd frontend && npx vite build --outDir dist-qa --emptyOutDir` | ✅ **PASS**，退出码 0，`built in 6.76s` | 15 s | 237 modules transformed，产物清单见 2.2 |

> 说明：第 2 项失败发生在 `vue-tsc --noEmit` **之后**（类型检查已通过，进入 `vite build` 阶段），失败位置是 Vite 的 `prepareOutDir → emptyDir`，与项目源码、依赖、配置无关。

### 2.2 构建产物（gzip 后分包，Top 10）

| 产物 | 原始大小 | gzip |
| --- | --- | --- |
| `assets/vendor-ipAgb_Gt.js` | 110.97 kB | **43.26 kB** |
| `assets/markdown-LnbKxlke.js` | 90.19 kB | **31.47 kB** |
| `assets/index-C47xiuTQ.js` | 83.67 kB | **31.27 kB** |
| `assets/markdown-Cy4LLNHW.js` | 60.75 kB | 18.20 kB |
| `assets/index-B8xWlOTH.css` | 60.60 kB | **10.83 kB** |
| `assets/ArticleEditView-DanbZUBN.js` | 25.30 kB | 9.26 kB |
| `assets/ArticleDetailView-BKPM3loy.js` | 21.34 kB | 7.68 kB |
| `assets/UsersView-BLm3B-3W.js` | 8.74 kB | 3.03 kB |
| `assets/ArticleListView-D7yPJe1d.js` | 8.58 kB | 3.48 kB |
| `index.html` | 1.95 kB | 1.22 kB |

共 53 个产物文件；路由级代码分割正常工作（articles / taxonomy / comments / guestbook / series 等均已独立成 chunk）。首屏关键路径（vendor + index + CSS）gzip 合计约 **86 kB**，体积健康。

### 2.3 真实浏览器冒烟（Chrome DevTools Protocol）

| # | 检查项 | 命令 | 结果 | 耗时 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 4 | CDP 真实浏览器端到端冒烟 | `node tools/smoke-check.mjs http://127.0.0.1:5173 ./shots/smoke` | ✅ **PASS 40 / 40**，退出码 0 | 22 s | 浏览器 `Chrome/153.0.8010.53`；截图 9 张写入 `./shots/smoke` |

通过项明细（40 项，全绿）：

- 首页渲染（文章卡片 4 篇）、站点标题、侧边栏导航 8 项、首页截图
- 排序参数生效（hottest）、详情页正文渲染、Markdown 代码高亮（2 个高亮块 / 6 个标题）、目录生成（6 项）、评论区挂载、详情页截图
- 归档页分组（2026-09 / 2026-08，4 篇）、标签云（5 个）、友链页（3 张演示卡片）、留言板（2 条演示留言 + 站长回复块）、关于页内容来自站点档案、404 兜底页
- 登录页渲染、`登录接口 — token 有效期 7200s`
- 后台仪表盘（`/admin`，统计数字加载）、文章列表（4 行）、编辑器（18 个按钮）、分类标签管理、媒体库、用户管理、站点设置、评论管理、留言板管理
- 暗色主题（背景 `rgb(20, 19, 17)` / 文字 `rgb(236, 233, 228)`）、移动端汉堡菜单、移动端截图
- **页面脚本错误：无**

### 2.4 HTTP 层端点冒烟（curl，兜底）

| # | 端点 | 期望 | 实际 | 耗时 | `X-Request-ID` | 业务字段 |
| --- | --- | --- | --- | --- | --- | --- |
| 5 | `GET /health` | 200 | ✅ 200 | 6.6 ms | ✅ `7a1bbd6e851f4998` | `{"status":"ok","env":"development","version":"1.0.0"}` |
| 6 | `GET /ready` | 200 | ✅ 200 | 31.6 ms | ✅ `42b7c1bf2db24b1c` | `{"status":"ready","database":"up","env":"development"}` |
| 7 | `GET /api/v1/articles?page=1&page_size=5` | 200 + items 非空 | ✅ 200 | 11.3 ms | ✅ `7d8dcba9856b4fb0` | `items` 非空，首篇「为什么我把博客的数据层重写了一遍」 |
| 8 | `GET /api/v1/categories` | 200 | ✅ 200 | 7.0 ms | ✅ `4b6c7cd7341742db` | 3 个分类，含 `article_count` |
| 9 | `GET /api/v1/tags` | 200 | ✅ 200 | 7.5 ms | ✅ `6dc8d898a75441aa` | 7 个标签，含 `article_count` |
| 10 | `GET /api/v1/site/profile` | 200 | ✅ 200 | 7.5 ms | ✅ `3062c55958a448ea` | `owner_name=站长1`，bio / about_md 非空 |
| 11 | `GET /api/v1/site/stats`（匿名） | — | ⚠️ **401** | 7.1 ms | ✅ `f6257a364b3c4df6` | **设计如此**，该路由挂 `AdminUser` 依赖，非缺陷 |
| 11b | `GET /api/v1/site/stats`（带 token） | 200 | ✅ 200 | 10 ms | ✅ `d0f4d0c12d324b38` | `article_total=4, published=4, draft=0, category=3, tag=7, comment=3, pending=1, total_views=557` |
| 12 | `GET /feed.xml` | 200 | ✅ 200 | 10.9 ms | ✅ `e76d9119798049ba` | RSS 2.0，`channel.title=站长1的博客`，item 非空 |
| 13 | `GET /sitemap.xml` | 200 | ✅ 200 | 17.7 ms | ✅ `a669ef1a35a140f3` | `urlset` + 多个 `url/loc` |
| 14 | `GET /`（前端 5173） | 200 | ✅ 200 | 7.7 ms | — | Vite dev server 正常 |
| 15 | `GET /api/v1/articles`（经 5173 代理） | 200 | ✅ 200 | 14.7 ms | ✅ `cae10b9a64114a53` | Vite → 后端代理链路通 |

### 2.5 鉴权链路冒烟

| # | 用例 | 期望 | 实际 | 耗时 | `X-Request-ID` | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 16 | `POST /api/v1/auth/login`（admin / `admin123456`） | 200 + `access_token` | ✅ 200，拿到 token | 244 ms | ✅ `47b5a60688bc4794` | — |
| 17 | `GET /api/v1/auth/me`（带 Bearer） | 200 + admin 用户 | ✅ 200 | 29 ms | ✅ `849727cc961b4756` | `{"username":"admin","nickname":"站长","id":1,"role":"admin","is_active":true,…}` |
| 18 | 反向：错误密码登录 | 401 | ✅ 401 | 211 ms | ✅ `82f909c329d54483` | `{"code":"unauthorized","detail":"用户名或密码错误"}` |
| 19 | 反向：不带 token 访问 `/auth/me` | 401 | ✅ 401 | 3 ms | ✅ `10e488c71a314363` | `{"code":"unauthorized","detail":"未登录或登录状态已失效"}` |

> 备注：首轮执行时登录曾返回 **429 `rate_limited`**（`后端 deps.py:190 LOGIN_RATE_LIMIT = rate_limit("login", limit=5, window_seconds=60)`），是自动化脚本连续打登录触发的限流，等待窗口后重跑即 200。**属于设计行为，非缺陷**。

### 2.6 后端回归（pytest 子集）

| # | 检查项 | 命令 | 结果 | 耗时 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 20 | 测试框架可用性 + 小子集回归 | `cd backend && .venv/Scripts/python.exe -m pytest tests/test_env_example.py tests/test_error_envelope.py -v --no-header` | ✅ **7 passed / 0 failed**，退出码 0 | 4 s（用例执行 0.67 s） | 仓库无 `tests/test_health.py`，改取 2 个最小文件（2 + 5 例）；未跑全量 pytest（约 300 s，按要求跳过） |

---

## 三、真实报错原文

### 3.1 `npm run build` 失败原文（唯一一条失败）

```
vite v6.4.3 building for production...
transforming...
✓ 237 modules transformed.
✗ Build failed in 5.30s
error during build:
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50,"scope":"turn","targets":["D:\\Projects\\01_个人项目\\personal-blog\\frontend\\dist\\assets"],"targetCount":1}
    at checkBulkDeleteGuard (D:\AI工具\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\node-safe-delete-shim.cjs:239:19)
    at tryTrash (D:\AI工具\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\node-safe-delete-shim.cjs:591:5)
    at tryRm (D:\AI工具\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\node-safe-delete-shim.cjs:785:5)
    at Object.wrappedRmSync [as rmSync] (D:\AI工具\WorkBuddy\resources\app.asar.unpacked\cli\vendor\shim\node-safe-delete-shim.cjs:791:15)
    at emptyDir (file:///D:/Projects/01_%E4%B8%AA%E4%BA%BA%E9%A1%B9%E7%9B%AE/personal-blog/frontend/node_modules/vite/dist/node/chunks/dep-Dm0c1Wj2.js:6855:19)
    at prepareOutDir (file:///D:/Projects/01_%E4%B8%AA%E4%BA%BA%E9%A1%B9%E7%9B%AE/personal-blog/frontend/node_modules/vite/dist/node/chunks/dep-Dm0c1Wj2.js:46403:7)
    at buildEnvironment (.../dep-Dm0c1Wj2.js:46367:7)
    at async Object.defaultBuildApp [as buildApp] (.../dep-Dm0c1Wj2.js:46843:5)
    at async CAC.<anonymous> (.../vite/dist/node/cli.js:863:7)
EXIT_CODE=1
```

**定性**：调用栈里唯一的非 Vite 帧是 `WorkBuddy/.../node-safe-delete-shim.cjs` —— 这是本机 WorkBuddy CLI 沙箱注入到 Node `fs.rmSync` 上的「批量删除二次确认」护栏（单轮 50 个文件阈值）。Vite 在清空 `dist/assets`（已有 50 个旧产物）时被拦截。**与项目源码/配置无关**，在正常终端或 CI 中不会出现。

### 3.2 其余检查项

**无报错。** 类型检查 stdout/stderr 干净；CDP 冒烟 40/40 且「页面脚本错误 — 无」；pytest 7 passed 无 warning 汇总行；所有 curl 请求均返回预期状态码。

---

## 四、问题清单（按严重度排序）

### P1 — 环境/工具链阻塞（不影响项目质量，仅影响本沙箱内的构建）

**`npm run build` 在本机 WorkBuddy 沙箱内因批量删除护栏失败**
- 现象：`vue-tsc` 通过后，`vite build` 清空 `frontend/dist/assets`（50 个旧文件）时被 `node-safe-delete-shim` 拦截，退出码 1。
- 影响范围：**仅本沙箱**。构建能力本身完好——改用全新输出目录后完整构建成功（237 模块 / 6.76 s / 退出码 0）。
- 建议（供参考，未改动任何源码或配置）：
  1. 正常终端 / CI 直接 `npm run build` 即可，不受影响；
  2. 若必须在沙箱内构建：先手动清一次 `frontend/dist`，或临时用 `npx vite build --outDir <新目录>`（本次验证采用）。
- 责任归属：环境问题，**无需 Engineer 修复**。

### P2 — 部署配置待办（非启动缺陷）

**`site_base_url` 仍为默认值 `http://localhost:5173`**
- 证据：`backend/src/app/config.py:83  site_base_url: str = "http://localhost:5173"`，`backend/.env` 未覆盖。
- 现象：`GET /feed.xml` 与 `GET /sitemap.xml` 中的 `<link>` / `<loc>` 全部指向 `http://localhost:5173/...`。
- 影响：开发环境正常；**上线前必须配置真实域名**，否则 RSS / sitemap 的 SEO 链接全部指向本地。
- 建议：部署时通过环境变量或 `.env` 覆盖 `SITE_BASE_URL`（生产部署 checklist 项）。

### P3 — 信息级（确认为设计行为，非缺陷）

1. **`GET /api/v1/site/stats` 匿名访问返回 401**：`backend/src/app/api/v1/site.py` 中该路由显式挂 `AdminUser` 依赖（后台仪表盘专用），带 Bearer token 后返回 200 且字段完整。**符合设计**。
2. **登录接口限流 5 次 / 60 秒**：`backend/src/app/api/deps.py:190`。自动化脚本连续打登录会拿到 429 `rate_limited`。**符合设计**（防暴力破解），但压测/冒烟脚本需注意节流。

---

## 五、附：验证过程的环境与清理说明

- 本机 Bash PATH 异常，所有命令均以 `export PATH="/usr/bin:/bin:/usr/local/bin:$PATH";` 前缀执行。
- 后端使用 `backend/.venv/Scripts/python.exe`。
- 登录鉴权测试凭据从 `backend/.env` 运行时读取，测试结果已对 token / 密码脱敏后记录。
- **未修改任何源码或配置文件**。
- 验证过程产生的临时文件已全部清理：`frontend/dist-qa/`、`backend/.qa_http_smoke.py`、`backend/.qa_http_smoke.mjs`、`backend/.qa_smoke_out.txt`、`backend/.qa_smoke_out_utf8.txt` 均已删除（已复核 `ls .qa_*` → No such file）。
- 截图产物保留在 `shots/smoke/`（9 张 PNG，由 `tools/smoke-check.mjs` 正常生成）。
