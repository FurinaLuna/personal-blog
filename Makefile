# 个人博客 · 常用命令
#
# Windows 上如果没有 make，可以直接看每一节下面的注释里对应的原始命令。

SHELL := /bin/bash

BACKEND  := backend
FRONTEND := frontend

# Windows 用 Scripts/python.exe，类 Unix 用 bin/python
ifeq ($(OS),Windows_NT)
  VENV_PY := $(BACKEND)/.venv/Scripts/python.exe
else
  VENV_PY := $(BACKEND)/.venv/bin/python
endif

# 分层契约走 backend/scripts/lint_imports.py 包装，而不是裸 lint-imports：
# .importlinter 里的契约名与注释是中文，import-linter 用系统默认编码读它，
# 在中文 Windows（GBK）上会直接抛 UnicodeDecodeError 并以 1 退出——
# 看起来像"契约被破坏"，实际只是文件读不出来。包装脚本会在子进程里
# 以 UTF-8 模式重新执行，任何平台、任何 locale 下行为一致。
LINT_IMPORTS := $(abspath $(VENV_PY)) scripts/lint_imports.py

.DEFAULT_GOAL := help
.PHONY: help install install-backend install-frontend dev dev-backend dev-frontend \
        test test-frontend test-all test-cov lint lint-frontend fmt fmt-check check typecheck \
        smoke interaction full-check e2e-live \
        migrate migration migrate-down seed backup backup-list build build-preview \
        docker-config docker-up docker-down docker-logs docker-migrate clean

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- 安装

install: install-backend install-frontend ## 安装前后端全部依赖

install-backend: ## 安装后端依赖（创建 venv 并安装开发依赖）
	cd $(BACKEND) && python -m venv .venv
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m pip install --upgrade pip
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m pip install -e ".[dev]"

install-frontend: ## 安装前端依赖
	cd $(FRONTEND) && npm install

# ---------------------------------------------------------------- 开发

dev: ## 同时启动前后端（Ctrl+C 结束）
	@echo "后端 http://127.0.0.1:8000/docs  前端 http://127.0.0.1:5173"
	@$(MAKE) -j2 dev-backend dev-frontend

dev-backend: ## 只启动后端（热重载）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m uvicorn app.main:app --reload --app-dir src --port 8000

dev-frontend: ## 只启动前端（Vite 开发服务器）
	cd $(FRONTEND) && npm run dev

# ---------------------------------------------------------------- 质量

test: ## 运行后端测试
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m pytest

test-frontend: ## 运行前端单元测试（Vitest）
	cd $(FRONTEND) && npm run test

test-all: test test-frontend ## 前后端测试一起跑

test-cov: ## 运行测试并输出覆盖率报告
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m pytest --cov=app --cov-report=term-missing

lint: ## 后端静态检查（ruff check + 分层契约 import-linter）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff check .
	cd $(BACKEND) && $(LINT_IMPORTS)

fmt: ## 后端格式化（ruff format + 自动修复）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff format .
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff check . --fix

fmt-check: ## 检查格式是否符合规范（CI 用）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff format --check .
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff check .

typecheck: ## 前端类型检查
	cd $(FRONTEND) && npm run type-check

lint-frontend: ## 前端静态检查（ESLint：bug 与无障碍，不管排版风格）
	cd $(FRONTEND) && npm run lint

check: lint fmt-check typecheck lint-frontend test-frontend test ## 一次跑完所有检查（提交前跑这个）

interaction: ## 真实交互验证（点击驱动的失败路径；需先 make dev 起好前后端）
	@curl -fsS http://127.0.0.1:8000/health >/dev/null || (echo "后端未运行，请先 make dev" && exit 1)
	@curl -fsS http://127.0.0.1:5173/ >/dev/null || (echo "前端未运行，请先 make dev" && exit 1)
	node tools/interaction-check.mjs ./shots/interaction

smoke: ## 端到端冒烟验证（真实浏览器；需先 make dev 起好前后端）
	@curl -fsS http://127.0.0.1:8000/health >/dev/null || (echo "后端未运行，请先 make dev" && exit 1)
	@curl -fsS http://127.0.0.1:5173/ >/dev/null || (echo "前端未运行，请先 make dev" && exit 1)
	node tools/smoke-check.mjs http://127.0.0.1:5173 ./shots/smoke

full-check: ## 全功能回归 + 数据基线核对（会写数据但自清理；需先 make dev 起好前后端）
	@curl -fsS http://127.0.0.1:8000/health >/dev/null || (echo "后端未运行，请先 make dev" && exit 1)
	@curl -fsS http://127.0.0.1:5173/ >/dev/null || (echo "前端未运行，请先 make dev" && exit 1)
	node tools/full-check.mjs http://127.0.0.1:5173 ./shots/full

e2e-live: ## 端到端真实链路（自起进程与临时库，不需要先 make dev；CI 跑的就是它）
	$(abspath $(VENV_PY)) tools/e2e_live/e2e_run.py

# ---------------------------------------------------------------- 数据库

migrate: ## 应用全部数据库迁移
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m alembic upgrade head

migration: ## 生成迁移，用法：make migration m="add xxx field"
	@test -n "$(m)" || (echo "用法: make migration m=\"描述\"' && exit 1)
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m alembic revision --autogenerate -m "$(m)"

migrate-down: ## 回滚一步迁移
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m alembic downgrade -1

seed: ## 重新写入演示数据（会清空 backend/blog.db，先自动备份一份）
	@if [ -f $(BACKEND)/blog.db ]; then \
		echo "⚠️  即将删除 $(BACKEND)/blog.db 并重建演示数据"; \
		$(MAKE) --no-print-directory backup >/dev/null && echo "   （已先备份到 $(BACKEND)/backups/）"; \
	fi
	cd $(BACKEND) && rm -f blog.db && $(abspath $(VENV_PY)) -m uvicorn app.main:app --app-dir src --port 8000 &
	@sleep 4 && curl -fsS http://127.0.0.1:8000/health && echo " ← 演示数据已写入（可 Ctrl+C 停掉服务）"

backup: ## 备份数据库到 backend/backups/（SQLite 用 VACUUM INTO；PG 用 pg_dump -Fc，保留最近 14 份）
	cd $(BACKEND) && $(abspath $(VENV_PY)) scripts/backup_db.py --keep 14
	@echo "   恢复方式见 README「备份与恢复」"

backup-list: ## 列出已有的备份
	@ls -lht $(BACKEND)/backups/ 2>/dev/null || echo "还没有备份（先跑 make backup）"

# ---------------------------------------------------------------- 构建与部署

build: ## 构建前端生产产物到 frontend/dist
	cd $(FRONTEND) && npm run build

build-preview: build ## 构建并在本地预览生产产物
	cd $(FRONTEND) && npm run preview

docker-config: ## 校验 compose 配置（缺必填变量/写错上下文会在这里就报出来）
	docker compose config > /dev/null && echo "docker compose 配置有效"

docker-up: ## 启动全部容器（需先在**项目根目录**的 .env 配好生产配置，见 .env.example）
	docker compose up -d --build

docker-down: ## 停止并移除全部容器
	docker compose down

docker-logs: ## 跟随查看容器日志
	docker compose logs -f --tail=100

docker-migrate: ## 手动补跑数据库迁移（正常由容器入口自动执行，这里是应急/重跑用）
	docker compose exec backend alembic upgrade head

# ---------------------------------------------------------------- 清理

clean: ## 清理构建产物与缓存（不删数据库和媒体文件）
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite $(FRONTEND)/.npm-cache
	find $(BACKEND) -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND) -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND) -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND) -type d -name .import_linter_cache -prune -exec rm -rf {} + 2>/dev/null || true
	# 在仓库根目录跑 pytest/ruff/lint-imports 时也会在根上生成一份同名缓存，一并清掉
	rm -rf .pytest_cache .ruff_cache .import_linter_cache 2>/dev/null || true
	rm -f $(BACKEND)/.coverage $(BACKEND)/alembic_boot.db
