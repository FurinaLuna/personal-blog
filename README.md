# 个人博客 · Personal Blog

> 一套可以直接上线的个人博客系统。技术笔记、生活随笔、读书观影都往这里放。

[![CI](https://github.com/FurinaLuna/personal-blog/actions/workflows/ci.yml/badge.svg)](https://github.com/FurinaLuna/personal-blog/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node](https://img.shields.io/badge/Node-%E2%89%A520-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.5-4FC08D?logo=vue.js&logoColor=white)](https://vuejs.org/)

前后端分离架构：后端 FastAPI 全异步 + 分层设计，前端 Vue 3 + TypeScript。
**写出来能跑、改起来可验证** —— 183 个后端测试、48 个前端单测，
外加两个真实浏览器端到端脚本（冒烟 34 项 / 交互 16 项）。

---

## 目录

- [项目简介](#项目简介)
- [功能一览](#功能一览)
- [界面预览](#界面预览)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [常用命令](#常用命令)
- [目录结构](#目录结构)
- [配置项](#配置项)
- [API 概览](#api-概览)
- [测试与验证](#测试与验证)
- [部署](#部署)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 项目简介

一套自用的个人博客，两个目标：

1. **内容侧要好用** —— Markdown 写作、分类标签、草稿保护、评论审核、媒体库，
   后台该有的都有，不为了"简洁"牺牲功能。
2. **工程侧要可信** —— 分层清晰、类型完整、每个功能都有对应的测试或验证脚本。
   仓库里那些「只有真实浏览器才能发现」的坑（登录后被弹回登录页、
   后台布局嵌套导致子页面不渲染）都写进了文档，不是靠运气修好的。

默认 SQLite 零依赖启动，需要时一个环境变量切到 PostgreSQL。

## 功能一览

### 内容与前台

| 功能 | 说明 |
|---|---|
| 文章状态 | 草稿 / 已发布 / 已归档；支持置顶、封面图、阅读时长自动估算 |
| Markdown 渲染 | `marked → DOMPurify 白名单消毒 → 增强`（标题锚点、代码高亮、外链 `rel`、图片懒加载） |
| 代码块 | 按需注册 17 种语言（比 `lib/common` 省 60% 体积）、右上复制按钮、右下语言标签 |
| 阅读体验 | 自动目录（桌面侧栏 + 移动端浮动抽屉）、阅读进度条、相关文章推荐 |
| 组织方式 | 分类（一对一）、标签（多对多）、按月归档、关键词搜索 |
| 互动 | 两级评论（先审后发）、点赞、阅读量统计 |
| 列表页 | 服务端分页 + 五种排序 + 筛选，**全部状态存在 URL query**，刷新/分享/前进后退都能还原 |
| 主题 | 亮 / 暗 / 跟随系统三态，首屏无闪烁 |
| 订阅与分享 | RSS 2.0 与 sitemap，每页独立标题与 Open Graph 卡片 |

### 后台

- **文章编辑器**：分栏预览、工具栏、粘贴/拖拽上传图片、**本地草稿自动保存与恢复**
- **评论审核**：待审队列、逐条通过/撤下、删除
- **分类与标签**：增删改，附带文章计数
- **媒体库**：上传、预览、复制 Markdown 片段、删除
- **用户管理**：分配账号与角色（不开放自助注册）
- **站点设置**：站点信息、社交链接、关于页内容、评论开关
- **全局错误边界**：任一组件抛错渲染兜底页并提供重试，而不是整页白屏

### 后端与运维

- FastAPI + SQLAlchemy 2.0（全异步）+ Pydantic v2，严格分层
  （`api` / `services` / `repositories` / `models` / `schemas`）
- JWT 双 Token（access 120 分钟 / refresh 7 天），401 静默续期（带单飞锁防并发重复刷新）
- 三级角色：访客 / 作者 / 站长；三层防护：依赖注入门禁 → 资源归属校验 → Schema 层防提权
- 上传安全：Pillow 真实解码判型（不信任客户端声明的 `Content-Type`）、流式限流读、
  扩展名白名单（默认禁 SVG / HTML）、服务端生成文件名
- 请求 ID 透传 + 结构化 JSON 日志 + 限流（登录 5/分、评论 5/分、点赞 20/分）
- `/health` 存活探针与 `/ready` 就绪探针（真实查库，未就绪返回 503）
- Alembic 迁移（升降级都验证过）；Docker + nginx + docker-compose

## 界面预览

| 首页 | 文章详情 |
|---|---|
| ![首页](docs/screenshots/01-home.png) | ![文章详情](docs/screenshots/02-detail.png) |

| 后台仪表盘 | 文章编辑器 |
|---|---|
| ![仪表盘](docs/screenshots/04-admin-dashboard.png) | ![编辑器](docs/screenshots/06-admin-editor.png) |

| 后台文章管理 | 暗色主题 |
|---|---|
| ![文章管理](docs/screenshots/05-admin-articles.png) | ![暗色](docs/screenshots/08-home-dark.png) |

## 技术栈

| 层 | 选型 | 为什么 |
|---|---|---|
| 后端框架 | FastAPI | 原生异步 + 依赖注入 + 自动 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0（async） | 全链路异步，避免同步驱动拖住事件循环 |
| 校验 | Pydantic v2 | 请求/响应模型即文档，字段级错误可直接回填表单 |
| 数据库 | SQLite（默认）/ PostgreSQL | 开发零依赖，生产一行环境变量切换 |
| 迁移 | Alembic | 表结构变更可回滚，不靠手改生产库 |
| 认证 | PyJWT + bcrypt | 双 Token；密码只存哈希 |
| 前端框架 | Vue 3 + TypeScript | 组合式 API + 完整类型 |
| 构建 | Vite | 开发态秒级热更新；产物按路由与库分包 |
| 状态 | Pinia | 认证 / 站点档案 / 主题三个 store |
| 样式 | Tailwind CSS | 语义色变量集中定义，换肤只改一个文件 |
| Markdown | marked + DOMPurify + highlight.js | 渲染与消毒分离，消毒排在增强之前 |
| 测试 | pytest / Vitest | 后端 171 例、前端 40 例 |
| 端到端 | Chrome DevTools Protocol | 复用本机 Chrome，不引入 Playwright 的数百 MB 依赖 |

## 快速开始

**前置要求**：Python ≥ 3.11、Node ≥ 20。默认使用 SQLite，不需要额外装数据库。

```bash
git clone https://github.com/FurinaLuna/personal-blog.git
cd personal-blog
```

**1. 启动后端**

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --app-dir src --port 8000
```

**2. 启动前端**（另开一个终端）

```bash
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
|---|---|
| 前台 | <http://localhost:5173> |
| 后台 | <http://localhost:5173/admin> |
| API 文档 | <http://localhost:8000/docs> |
| 就绪检查 | <http://localhost:8000/ready> |

**默认管理员**：`admin` / `admin123456` —— **生产环境请立刻修改**。

首次启动会自动建表并写入 4 篇演示文章；不想要演示数据就把 `.env` 里的
`SEED_DEMO_DATA` 设为 `false`。

装了 `make` 的话，`make install` + `make dev` 可以一步到位。

## 常用命令

```bash
make help          # 列出全部命令
make install       # 安装前后端依赖
make dev           # 同时启动前后端
make check         # 提交前跑这个：ruff + 格式 + 后端测试 + 前端单测
make test-all      # 前后端测试
make build         # 前端类型检查 + 生产构建
make smoke         # 真实浏览器冒烟（34 项，需先 make dev）
make interaction   # 真实点击的交互验证（16 项，需先 make dev）
make migrate       # 应用数据库迁移
make migration m="add xxx field"   # 生成迁移
make docker-up     # 容器化启动
```

Windows 上没有 `make` 时，每个目标的原始命令都写在 `Makefile` 里。

## 目录结构

```
personal-blog/
├── backend/                        # FastAPI 后端
│   ├── src/app/
│   │   ├── main.py                 # 应用工厂：中间件 / 异常处理 / 探针
│   │   ├── config.py               # 配置（全部来自环境变量）
│   │   ├── api/                    # 路由、依赖注入、限流、请求上下文中间件
│   │   │   └── v1/                 # 按资源分组的端点
│   │   ├── models/                 # SQLAlchemy 模型（8 张表）
│   │   ├── schemas/                # Pydantic 请求/响应模型
│   │   ├── services/               # 业务规则唯一所在地
│   │   ├── repositories/           # 数据访问（只 flush，不 commit）
│   │   ├── db/                     # 会话 / 基类 / 自定义类型 / 种子数据
│   │   └── utils/                  # 安全 / 存储 / 日志 / 限流 / 异常
│   ├── tests/                      # 183 个 pytest 用例
│   ├── alembic/                    # 数据库迁移
│   ├── README.md                   # 后端说明（分层职责 / 迁移 / 运维端点）
│   └── storage/                    # 上传的图片与附件（内容不入库）
├── frontend/                       # Vue 3 前端
│   ├── src/
│   │   ├── api/                    # HTTP 客户端 + 各模块接口
│   │   ├── components/             # 可复用组件
│   │   ├── composables/            # useAsyncData / useAction / useHead / useDraftAutosave …
│   │   ├── layouts/                # Default / Admin / Blank 三套布局
│   │   ├── router/                 # 路由表 + 守卫
│   │   ├── stores/                 # Pinia：auth / site / theme
│   │   ├── utils/                  # Markdown 渲染 / 格式化 / 预取
│   │   └── views/                  # 前台 9 页 + 后台 8 页
│   └── src/**/*.spec.ts            # 48 个 Vitest 用例
├── deploy/                         # Dockerfile × 2 + nginx.conf
├── docs/
│   ├── DESIGN.md                   # ★ 完整设计与实现方案（五大模块）
│   ├── ASSESSMENT.md               # 全面评估（风险 / 升级 / 功能 / 性能）
│   ├── ROADMAP.md                  # 迭代路线图与进度
│   ├── devlog/                     # 逐日开发日志（决策 / 验证 / 踩坑）
│   └── screenshots/                # 界面截图
├── tools/
│   ├── smoke-check.mjs             # CDP 冒烟：逐页渲染与关键交互
│   └── interaction-check.mjs       # CDP 交互：写操作与失败路径
├── Makefile                        # 常用命令入口
├── docker-compose.yml
├── CONTRIBUTING.md                 # 贡献指南
├── SECURITY.md                     # 安全策略与部署注意事项
├── CHANGELOG.md                    # 更新日志
└── LICENSE                         # MIT
```

## 配置项

全部通过环境变量注入，模板见 [`backend/.env.example`](backend/.env.example)。
**开发环境大部分可保持默认**，生产必须改的项已在表中标出。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | ⚠️ 生产设 `production`（会自动关闭 `/docs` 与 `/redoc`） |
| `DEBUG` | `true` | ⚠️ 生产设 `false` |
| `DATABASE_URL` | SQLite 文件 | 生产推荐 `postgresql+asyncpg://…` |
| `DB_AUTO_CREATE` | `true` | ⚠️ 生产设 `false`，改由 `alembic upgrade head` 管表结构 |
| `JWT_SECRET_KEY` | 开发占位值 | ⚠️ 生产必须换：`openssl rand -hex 32` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `120` | access token 有效期 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | refresh token 有效期 |
| `CORS_ORIGINS` | localhost | ⚠️ 生产只填真实前端域名，逗号分隔 |
| `SITE_BASE_URL` | `http://localhost:5173` | RSS / sitemap 里的绝对链接依赖它 |
| `STORAGE_DIR` | `./storage` | 上传文件根目录 |
| `MAX_UPLOAD_SIZE` | `10485760` | 10 MB，需与 nginx `client_max_body_size` 一致 |
| `LOG_JSON` | `true` | 访问日志以单行 JSON 输出，便于日志收集器按字段过滤 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `TRUST_PROXY_HEADERS` | `false` | ⚠️ 只有部署在可信反代之后才开。`X-Forwarded-For` 可被伪造 |
| `RATE_LIMIT_ENABLED` | `true` | 登录 / 评论 / 点赞限流开关 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin123456` | ⚠️ 首次启动后立刻改密码 |
| `SEED_DEMO_DATA` | `true` | ⚠️ 生产设 `false` |

## API 概览

完整交互式文档在 <http://localhost:8000/docs>。主要端点：

| 分组 | 端点 | 说明 |
|---|---|---|
| 认证 | `POST /api/v1/auth/login` | 用户名或邮箱 + 密码换双 Token（限流 5/分） |
| | `POST /api/v1/auth/refresh` | 用 refresh token 续期 |
| | `GET /api/v1/auth/me` | 当前用户 |
| | `GET/POST/PATCH/DELETE /api/v1/auth/users` | 用户管理（站长） |
| 文章 | `GET /api/v1/articles` | 前台列表，支持分页 / 排序 / 筛选 / 搜索 |
| | `GET /api/v1/articles/{slug或id}` | 详情（草稿对无权用户返回 404） |
| | `POST/PATCH/DELETE /api/v1/articles` | 增改删（作者以上） |
| | `GET /api/v1/articles/archive` | 按月归档 |
| | `GET /api/v1/articles/{id}/related` | 相关文章 |
| | `POST /api/v1/articles/{id}/like` | 点赞（限流 20/分） |
| 分类标签 | `GET/POST/PATCH/DELETE /api/v1/categories`、`/api/v1/tags` | 分类与标签；删除只解关联不删文章 |
| 评论 | `GET /api/v1/comments/article/{id}` | 两级评论树 |
| | `POST /api/v1/comments/article/{id}` | 发表评论（支持匿名，限流 5/分） |
| | `PATCH/DELETE /api/v1/comments/{id}` | 审核与删除（作者以上） |
| 媒体 | `POST /api/v1/attachments` | 上传（真实解码判型 + 流式限流读） |
| | `GET/PATCH/DELETE /api/v1/attachments` | 媒体库管理 |
| 站点 | `GET /api/v1/site/profile`、`/stats` | 站点档案与统计 |
| | `PUT /api/v1/site/profile` | 更新站点设置（站长） |
| 订阅 | `GET /feed.xml`、`/sitemap.xml` | RSS 2.0 与站点地图（后端直出） |
| 运维 | `GET /health`、`/ready` | 存活 / 就绪探针 |

所有响应都带 `X-Request-ID` 头；错误响应体包含 `request_id`，
用户报障时提供它即可在服务端日志里精确定位那一次请求。

## 测试与验证

```bash
make check          # 后端 lint + 格式 + 测试 + 前端单测
make build          # 前端类型检查 + 生产构建
make smoke          # 真实浏览器冒烟（需先 make dev）
make interaction    # 真实点击的交互与失败路径验证（需先 make dev）
```

当前基线：

| 检查 | 结果 |
|---|---|
| `ruff check` / `ruff format --check` | 全部通过 |
| `pytest` | **183 passed** |
| `vue-tsc --noEmit` | 0 报错 |
| `vitest run` | **48 passed** |
| `vite build` | 成功（vendor 分包 gzip ~43 KB、markdown 分包 gzip ~31 KB、主包 gzip ~30 KB） |
| `alembic upgrade head` / `downgrade base` | 8 张表，干净回滚 |
| `tools/smoke-check.mjs` | **34/34**（真实 Chrome，页面错误 0） |
| `tools/interaction-check.mjs` | **22/22**（登录失败路径 / 评论审核 / 状态切换 / 设置保存 / 窄屏布局 / 草稿恢复 / 评论链路与空值拦截） |

**为什么要两个浏览器脚本**：单测与类型检查都是绿的情况下，
项目里仍然出现过「登录后被弹回登录页」和「后台侧边栏渲染两遍、子页面完全不显示」
这两个只有真实浏览器才暴露的问题。这两个脚本就是为此留下的回归网。

## 部署

```bash
cp backend/.env.example backend/.env
vim backend/.env          # 至少改 APP_ENV / DEBUG / DB_AUTO_CREATE /
                          # JWT_SECRET_KEY / ADMIN_PASSWORD / SEED_DEMO_DATA /
                          # CORS_ORIGINS / SITE_BASE_URL
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

前端 <http://localhost:8080>，后端 <http://localhost:8000>。

生产上线前的完整检查清单、备份方案与回滚条件见
[`docs/DESIGN.md`](docs/DESIGN.md) 第 5 章，部署注意事项见 [SECURITY.md](SECURITY.md)。

## 贡献指南

见 [CONTRIBUTING.md](CONTRIBUTING.md)。简单说：

1. 从 `main` 切分支：`git checkout -b feat/your-feature`
2. 提交前确保 `make check`、`make build` 全绿；动到路由 / 布局 / 样式层 / 上传链路时
   还要跑 `make smoke` 与 `make interaction`
3. 提交信息说明**改了什么 + 怎么验证的**
4. 开 PR 并填写模板中的检查清单

安全漏洞请**不要**开公开 issue，见 [SECURITY.md](SECURITY.md)。

迭代计划与进度见 [`docs/ROADMAP.md`](docs/ROADMAP.md)，
逐日的决策与踩坑记录见 [`docs/devlog/`](docs/devlog/)。

## 许可证

[MIT License](LICENSE) © 2026 FurinaLuna

你可以自由使用、修改、分发本项目（包括商用），只需保留版权声明。
