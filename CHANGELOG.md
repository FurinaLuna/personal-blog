# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的组织方式。

> 说明：1.0.0 之前的提交是项目从零建成的连续过程，这里按主题归并，
> 逐条的决策与踩坑记录见 [`docs/devlog/`](docs/devlog/)。

## [Unreleased]

### 计划中

- 图片多尺寸与 AVIF 转换（上传时生成，老数据需回填脚本）
- 文章「系列 / 合集」导航
- 站点统计时间序列与后台趋势图
- 评论邮件通知（含退订）
- 服务层拆分（`ArticleService` 读写分离）与全站 API 限流扩容方案

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
