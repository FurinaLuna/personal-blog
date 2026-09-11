# 个人博客

一个前后端分离的个人博客系统。技术笔记、生活随笔、读书观影都往这里放。

**后端** FastAPI + SQLAlchemy 2.0（全异步）+ Pydantic v2 + JWT
**前端** Vue 3 + TypeScript + Vite + Pinia + Tailwind CSS

---

## 快速开始

需要 **Python ≥ 3.11** 与 **Node ≥ 20**。默认用 SQLite，不需要额外装数据库。

```bash
# 1. 后端
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
cp .env.example .env

uvicorn app.main:app --reload --app-dir src --port 8000
```

```bash
# 2. 前端（另开一个终端）
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173> 就能看到站点。

| 入口 | 地址 |
|---|---|
| 前台 | <http://localhost:5173> |
| 后台 | <http://localhost:5173/admin> |
| API 文档 | <http://localhost:8000/docs> |

**默认管理员**：`admin` / `admin123456` —— 生产环境请务必立刻改掉。

首次启动会自动建表并写入 4 篇演示文章。不想要演示数据就把 `.env` 里的
`SEED_DEMO_DATA` 设为 `false`。

安装好 `make` 的话，上面这些都可以用 `make install` + `make dev` 一步完成。

---

## 常用命令

```bash
make help          # 列出全部命令
make dev           # 同时起前后端
make test          # 跑后端测试（145 个用例）
make check         # lint + 格式检查 + 前端类型检查 + 测试
make migration m="add xxx field"   # 生成数据库迁移
make build         # 构建前端生产产物
make docker-up     # 容器化启动
```

Windows 上如果没有 `make`，每个命令的原始写法都写在 `Makefile` 对应注释里。

---

## 项目结构

```
personal-blog/
├── backend/
│   ├── src/app/
│   │   ├── main.py          # 应用工厂
│   │   ├── config.py        # 配置（全部来自环境变量）
│   │   ├── api/             # 路由 + 认证授权依赖 + 分页
│   │   ├── models/          # SQLAlchemy 模型
│   │   ├── schemas/         # Pydantic 请求/响应模型
│   │   ├── services/        # 业务规则（唯一所在地）
│   │   ├── repositories/    # 数据访问（只 flush，不 commit）
│   │   ├── db/              # 会话 / 基类 / 自定义类型 / 初始化数据
│   │   └── utils/           # 安全 / 存储 / 文本 / 异常
│   ├── tests/               # 145 个测试用例
│   └── alembic/             # 数据库迁移
├── frontend/
│   └── src/
│       ├── api/             # HTTP 客户端 + 各模块接口
│       ├── components/      # 可复用组件
│       ├── composables/     # useAsyncData / useToast
│       ├── layouts/         # Default / Admin / Blank
│       ├── router/          # 路由表 + 守卫
│       ├── stores/          # Pinia：auth / site / theme
│       ├── utils/           # Markdown 渲染 / 格式化
│       └── views/           # 前台 9 页 + 后台 8 页
├── deploy/                  # Dockerfile × 2 + nginx.conf
├── docs/DESIGN.md           # ★ 完整设计与实现方案
└── docker-compose.yml
```

---

## 功能一览

**内容**
- 文章支持草稿 / 已发布 / 已归档三种状态，置顶，可选封面图
- Markdown 写作，编辑器带工具栏、分栏预览、粘贴即上传图片
- 正文渲染走 DOMPurify 白名单消毒 + 代码高亮 + 自动生成目录
- 分类（一对一导航骨架）与标签（多对多关联网络）
- 按月归档，两级评论（先审后发），点赞，阅读量统计

**列表页**
- 服务端分页（`page_size` 上限 50），五种排序，关键词/分类/标签筛选
- **全部状态存在 URL query 里** —— 刷新、分享链接、前进后退都能还原

**上传**
- 靠 Pillow 真实解码判定类型，不信任客户端声明的 `Content-Type`
- 流式限流读，超限立刻中断；扩展名白名单，默认禁用 SVG / HTML
- 自动生成 WEBP 缩略图；文件名由服务端生成，根除路径穿越

**权限**
- JWT 双 Token：access 120 分钟 / refresh 7 天，401 静默续期（带单飞锁）
- 三级角色：访客 / 作者 / 站长
- 三层防护：依赖注入门禁 → 资源归属校验 → Schema 层防提权
- 草稿对无权用户返回 404 而非 403（不泄露"这里有一篇未发布文章"）

**其他**
- 亮 / 暗主题（首屏无闪烁）
- 完整 TypeScript，vue-tsc 零报错
- 响应式，移动端导航折叠

---

## 验证状态

```
后端  ruff check           → All checks passed!
后端  ruff format --check  → 65 files already formatted
后端  pytest               → 145 passed
前端  vue-tsc --noEmit     → 无报错
前端  vite build           → 构建成功（gzip 后主包 ~29KB）
数据库 alembic upgrade head → 8 张表；downgrade base → 干净回滚
```

---

## 部署

```bash
cp backend/.env.example backend/.env
vim backend/.env                 # 至少改 APP_ENV / DEBUG / DB_AUTO_CREATE /
                                 # JWT_SECRET_KEY / ADMIN_PASSWORD / SEED_DEMO_DATA
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

前端 <http://localhost:8080>，后端 <http://localhost:8000>。

生产上线前的完整检查清单、备份方案、回滚条件，见 [`docs/DESIGN.md`](docs/DESIGN.md) 第 5 章。

---

## 进一步阅读

[`docs/DESIGN.md`](docs/DESIGN.md) 是本项目完整的设计与实现方案，包含：

1. 整体页面结构（路由表 / 布局层级 / 导航设计）
2. 前端功能模块（分页排序 / Markdown 渲染流水线 / 上传存储展示）
3. 数据模型设计（字段定义 / 关联关系 / 实现层关键决策）
4. 认证方案（Token 设计 / 单飞续期 / 三层访问控制 / 权限矩阵）
5. 部署上线流程（环境变量 / 上线步骤 / PRR 检查清单 / 回滚）
6. 附录：目录结构 / 验证记录 / **开发期踩过的 6 个坑**

---

## 许可

MIT
