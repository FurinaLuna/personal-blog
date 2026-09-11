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

.DEFAULT_GOAL := help
.PHONY: help install install-backend install-frontend dev dev-backend dev-frontend \
        test test-cov lint fmt fmt-check check smoke migrate migration seed build \
        docker-up docker-down docker-logs docker-migrate clean

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

test-cov: ## 运行测试并输出覆盖率报告
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m pytest --cov=app --cov-report=term-missing

lint: ## 后端静态检查（ruff check）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff check .

fmt: ## 后端格式化（ruff format + 自动修复）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff format .
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff check . --fix

fmt-check: ## 检查格式是否符合规范（CI 用）
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff format --check .
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m ruff check .

typecheck: ## 前端类型检查
	cd $(FRONTEND) && npm run type-check

check: lint fmt-check typecheck test ## 一次跑完所有检查（提交前跑这个）

smoke: ## 端到端冒烟验证（真实浏览器；需先 make dev 起好前后端）
	@curl -fsS http://127.0.0.1:8000/health >/dev/null || (echo "后端未运行，请先 make dev" && exit 1)
	@curl -fsS http://127.0.0.1:5173/ >/dev/null || (echo "前端未运行，请先 make dev" && exit 1)
	node tools/smoke-check.mjs http://127.0.0.1:5173 ./smoke-shots

# ---------------------------------------------------------------- 数据库

migrate: ## 应用全部数据库迁移
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m alembic upgrade head

migration: ## 生成迁移，用法：make migration m="add xxx field"
	@test -n "$(m)" || (echo "用法: make migration m=\"描述\"' && exit 1)
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m alembic revision --autogenerate -m "$(m)"

migrate-down: ## 回滚一步迁移
	cd $(BACKEND) && $(abspath $(VENV_PY)) -m alembic downgrade -1

seed: ## 重新写入演示数据（清空数据库后执行）
	cd $(BACKEND) && rm -f blog.db && $(abspath $(VENV_PY)) -m uvicorn app.main:app --app-dir src --port 8000 &
	@sleep 4 && curl -fsS http://127.0.0.1:8000/health && echo " ← 演示数据已写入（可 Ctrl+C 停掉服务）"

# ---------------------------------------------------------------- 构建与部署

build: ## 构建前端生产产物到 frontend/dist
	cd $(FRONTEND) && npm run build

build-preview: build ## 构建并在本地预览生产产物
	cd $(FRONTEND) && npm run preview

docker-up: ## 启动全部容器（需先在 backend/.env 配好生产配置）
	docker compose up -d --build

docker-down: ## 停止并移除全部容器
	docker compose down

docker-logs: ## 跟随查看容器日志
	docker compose logs -f --tail=100

docker-migrate: ## 在容器内执行数据库迁移
	docker compose exec backend alembic upgrade head

# ---------------------------------------------------------------- 清理

clean: ## 清理构建产物与缓存（不删数据库和媒体文件）
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite $(FRONTEND)/.npm-cache
	find $(BACKEND) -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND) -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND) -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	rm -f $(BACKEND)/.coverage $(BACKEND)/alembic_boot.db
