# 贡献指南

感谢你有兴趣改进这个项目。这是一套前后端分离的个人博客系统，
下面是你需要知道的全部约定 —— 目标只有一个：**让改动容易被验证**。

## 目录速览

| 目录 | 说明 |
|---|---|
| `backend/` | FastAPI 后端（分层：api / services / repositories / models / schemas） |
| `frontend/` | Vue 3 + TypeScript 前端 |
| `deploy/` | Dockerfile 与 nginx 配置 |
| `docs/` | 设计文档、路线图、开发日志 |
| `tools/` | 真实浏览器端到端验证脚本 |

## 搭建开发环境

需要 **Python ≥ 3.11**、**Node ≥ 20**。默认使用 SQLite，无需额外安装数据库。

```bash
git clone https://github.com/FurinaLuna/personal-blog.git
cd personal-blog

# 后端
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -e ".[dev]"
cp .env.example .env

# 前端
cd ../frontend
npm install
```

装了 `make` 的话，`make install` 可以一步装完两端。

## 提交改动前必须通过

```bash
make check          # ruff + 格式检查 + 前端单测 + 后端测试
make build          # 前端类型检查 + 生产构建
make smoke          # 真实浏览器冒烟（需先 make dev 起好前后端）
```

分别等价于：

| 检查 | 命令 | 当前基线 |
|---|---|---|
| 后端 lint / 格式 | `ruff check .` / `ruff format --check .` | 全通过 |
| 后端测试 | `pytest` | 171 passed |
| 前端类型 | `npm run type-check` | 0 报错 |
| 前端单测 | `npm run test` | 40 passed |
| 前端构建 | `npm run build` | 成功 |
| 端到端冒烟 | `node tools/smoke-check.mjs` | 34/34 |
| 交互验证 | `node tools/interaction-check.mjs` | 16/16 |

> **动到路由、布局、样式层或上传链路时，`make smoke` 与
> `node tools/interaction-check.mjs` 是必跑的** —— 这两个脚本抓出过单元测试
> 和类型检查都发现不了的问题（后台布局嵌套导致子页面不渲染、登录后被弹回登录页）。

## 代码约定

**后端**

- 分层职责不可越界：路由只解析参数，业务规则写在 `services/`，数据访问写在 `repositories/`
- 仓储层只 `flush` 不 `commit`，事务边界交给服务层/请求层
- 服务层不 import FastAPI —— 它必须能脱离 Web 框架被脚本复用
- 异常用 `app/utils/exceptions.py` 里的领域异常，由统一 handler 翻译成 HTTP 响应
- 所有函数带类型注解与 docstring（Args / Returns / Raises）

**前端**

- 组合式 API + `<script setup lang="ts">`，不写 Options API
- 状态放 URL query（分页 / 排序 / 筛选），不藏在组件内部
- 写操作走 `useAction()`，不要各写一套 try/catch/toast
- 列表请求走 `useAsyncData()`（内置竞态保护）
- 注释解释「为什么」而不是「做了什么」

**通用**

- 提交信息用 Conventional Commits：`feat:` / `fix:` / `refactor:` / `docs:` / `chore:`
- 提交信息正文写清「改了什么 + 怎么验证的」
- 新增逻辑必须带测试；修 bug 要先有能复现的用例
- 不要提交 `.env`、数据库文件、媒体文件、构建产物（`.gitignore` 已覆盖）

## CI 失败时先看什么

流水线红了不要急着改代码 —— 先分清是**代码问题**还是**环境问题**：

| 现象 | 大概率原因 | 怎么办 |
|---|---|---|
| job 在 2 秒内结束、**没有任何步骤记录**、注解里出现 `billing` / `locked` | 账号级账单锁（与代码无关） | 去 **Settings → Billing** 处理，别改代码 |
| 注解出现 `Invalid workflow file` | workflow YAML 语法错误 | 看注解指出的行号 |
| `npm ci` 失败并提示 lock 与 package.json 不同步 | 改了 `package.json` 但没跑 `npm install` | 本地跑 `npm install` 并提交更新后的 lock |
| `pip install -e ".[dev]"` 失败 | `backend/pyproject.toml` 的 `readme` 指向的文件不存在 | 检查 `backend/README.md` 是否在 |

> 这条清单来自一次真实排查：CI 连续 5 次全红，最后发现是账号账单锁——
> job 压根没启动。**先把「环境问题」排除掉，能省掉一轮无用的改代码。**

CI 不可用时的本地等价验证（缺一不可）：

```bash
make check          # ruff + 格式 + 后端测试 + 前端类型 + 前端单测
make build          # 前端生产构建
make smoke          # 真实浏览器冒烟（需先 make dev）
make interaction    # 真实点击的交互验证（需先 make dev）
```

## 提交 Pull Request

1. 从 `main` 切出分支：`git checkout -b feat/your-feature`
2. 完成改动，确保上面「提交改动前必须通过」全绿
3. 提交时说明**验证方式**（跑了哪些命令、看到什么结果）
4. 开 PR 并填写模板里的检查清单

PR 里请避免：

- 顺手做大范围重构（会让 review 无法聚焦）
- 引入新依赖却不说明理由与替代方案
- 只改测试断言来让流水线变绿（那等于删掉了检查）

## 报告问题

- Bug 请用 [Bug 反馈模板](.github/ISSUE_TEMPLATE/bug_report.md)，附复现步骤与环境
- 安全相关问题请不要开公开 issue，见 [SECURITY.md](SECURITY.md)
- 功能建议请说明**使用场景**，而不是只给方案

## 许可

贡献的代码按 [MIT License](LICENSE) 授权。
