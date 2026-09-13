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
- 端到端脚本的截图与报告产物统一收敛到 `shots/`（gitignore），
  Makefile 三个验证目标同步指向新目录，根目录不再散落多个 `*-shots/`
- 测试基线更新：后端 205 例、前端 80 例（Vitest）

### 已修复

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

### 计划中

- 图片多尺寸与 AVIF 转换（上传时生成，老数据需回填脚本）
- 站点统计时间序列与后台趋势图
- 评论邮件通知（含退订）
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
