# 后端 · personal-blog-backend

FastAPI + SQLAlchemy 2.0（全异步）+ Pydantic v2 + JWT 的博客后端。
仓库总览见 [上级 README](../README.md)，完整设计见 [`docs/DESIGN.md`](../docs/DESIGN.md)。

> 本文件同时是 `pyproject.toml` 里 `readme` 字段的指向。
> 如果删掉它，`pip install -e ".[dev]"` 会因为缺少打包元数据而失败。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -e ".[dev]"           # 运行依赖 + 开发依赖（pytest / ruff 等）
cp .env.example .env

uvicorn app.main:app --reload --app-dir src --port 8000
```

需要 **Python ≥ 3.11**。默认 SQLite，无需额外数据库；PostgreSQL 用
`pip install -e ".[postgres]"` 安装 `asyncpg` 后改 `DATABASE_URL` 即可。

## 分层职责（改动前必读）

```
api/          路由：解析参数、鉴权、调用 service、组装响应   ← 不写业务规则
services/     业务规则唯一所在地                            ← 不 import FastAPI
repositories/ 数据访问：只 flush，不 commit                 ← 不管业务规则
models/       SQLAlchemy 模型（8 张表）
schemas/      Pydantic 请求/响应模型（字段级错误回填表单）
db/           会话、基类、自定义类型、种子数据
utils/        安全 / 存储 / 文本 / 日志 / 限流 / 领域异常
```

几条硬约束：

- **服务层不依赖 Web 框架** —— 它要能脱离 FastAPI 被脚本或定时任务复用
- **仓储层不 commit** —— 事务边界交给请求层，一次请求一个事务
- **领域异常由统一 handler 翻译成 HTTP 响应** —— 路由里不写 `HTTPException`
  （唯一例外是限流依赖，它直接用 `RateLimitedError`，同样走统一 handler）

## 测试与检查

```bash
pytest                            # 183 个用例
pytest --cov=app --cov-report=term-missing
ruff check . && ruff format --check .
```

测试用独立 SQLite 文件，每个用例重建表结构；限流计数在用例间自动重置
（见 `tests/conftest.py` 里的 `_reset_rate_limiter`）。

## 数据库迁移

```bash
alembic revision --autogenerate -m "描述"   # 生成
alembic upgrade head                        # 应用
alembic downgrade -1                        # 回滚一步
```

生产必须把 `DB_AUTO_CREATE` 设为 `false`，改由迁移管理表结构 ——
否则一次误改模型就会在生产悄悄改表。

## 运维端点

| 端点 | 用途 |
|---|---|
| `GET /health` | 存活探针：进程在且配置加载成功 |
| `GET /ready` | 就绪探针：真实查询数据库，未就绪返回 503，编排系统据此摘流量 |
| `GET /docs` | OpenAPI 交互文档（`APP_ENV=production` 时自动关闭） |

所有响应带 `X-Request-ID`；错误响应体含 `request_id`，可直接在结构化日志里定位。
日志格式由 `LOG_JSON` 控制（默认单行 JSON，便于日志收集器按字段过滤）。
