# =============================================================================
# Ossia — Makefile
# =============================================================================
# Development workflow targets for the Ossia AI agent server.
#
# Quick reference:
#   make install        Install dependencies
#   make dev            Run the development server locally
#   make test           Run the test suite
#   make docker-up      Start the full Docker stack
#   make monitor-up     Start the monitoring stack (Prometheus + Loki + Grafana)
#   make build          Build the Docker image
#   make clean          Stop everything and remove volumes
# =============================================================================

# ─── Project settings ─────────────────────────────────────────────────────────
PYTHON    := .venv/bin/python
UV        := uv
DOCKER    := docker
COMPOSE   := docker compose
PYTEST    := $(PYTHON) -m pytest
RUFF      := $(PYTHON) -m ruff
MYPY      := $(PYTHON) -m mypy
PYRIGHT   := $(PYTHON) -m pyright
MONITORING_PROFILE := --profile monitoring

ifneq ("$(wildcard .env)","")
	HAS_ENV := true
else
	HAS_ENV := false
endif

# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: install setup env

install: ## Install project dependencies (dev + notebook extras). Creates .venv if missing.
	@if [ ! -d .venv ]; then \
		echo "Creating virtual environment..."; \
		$(UV) venv; \
	fi
	$(UV) pip install -e ".[dev,notebook]"

setup: install ## Alias for install

env: ## Create .env from .env.example if it doesn't exist
ifeq ($(HAS_ENV),true)
	@echo ".env already exists — skipping"
else
	cp .env.example .env
	@echo "Created .env from .env.example — edit it with your API keys"
endif

# ──────────────────────────────────────────────────────────────────────────────
# Development — local server (no Docker)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: dev dev-live format lint typecheck check

dev: ## Start the dev server with hot reload (requires .env with OSSIA_API_KEY + provider key)
	$(PYTHON) -m uvicorn core.api:app --host 127.0.0.1 --port 8000 --reload

dev-live: ## Start the dev server without hot reload
	$(PYTHON) -m uvicorn core.api:app --host 0.0.0.0 --port 8000

format: ## Format code with ruff
	$(RUFF) check --fix src tests scripts
	$(RUFF) format src tests scripts

lint: ## Lint with ruff
	$(RUFF) check src tests scripts

typecheck: ## Typecheck with mypy and pyright
	$(MYPY) src
	$(PYRIGHT) src

check: lint typecheck ## Run all static checks (lint + typecheck)

# ──────────────────────────────────────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: test test-focused test-all test-coverage

test: ## Run the full test suite (skips flaky HITL tests)
	$(PYTEST) tests/ -v

test-focused: ## Run a specific test, e.g. make test-focused path=tests/test_api.py::test_health
ifndef path
	$(error Usage: make test-focused path=tests/test_api.py::test_health)
endif
	$(PYTEST) $(path) -v

test-all: ## Run all tests (no exclusions)
	$(PYTEST) tests/ -v

test-coverage: ## Run tests with coverage report
	$(PYTEST) tests/ --cov=src/core --cov-report=term-missing --cov-report=html

# ──────────────────────────────────────────────────────────────────────────────
# Spec & validation
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: spec-docs spec-docs-force spec-coverage changelog

spec-docs: ## Update pinned OpenAPI spec (run after deliberate contract changes)
	$(PYTHON) scripts/update_openapi_spec.py

spec-docs-force: ## Regenerate OpenAPI spec (alias for spec-docs)
	$(PYTHON) scripts/update_openapi_spec.py

spec-coverage: ## Generate coverage matrix (route x feature table)
	$(PYTHON) scripts/coverage_matrix.py

changelog: ## Generate draft changelog entry from implemented feature specs
	$(PYTHON) scripts/generate_changelog_entry.py --dry-run

# ──────────────────────────────────────────────────────────────────────────────
# Docker
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: docker-build docker-rebuild docker-up docker-up-direct docker-up-minimal
.PHONY: docker-down docker-down-clean docker-logs docker-logs-svc docker-ps
.PHONY: docker-restart docker-langgraph-build

docker-build: ## Build the Docker image
	$(DOCKER) build -t ossia .

docker-rebuild: ## Build with no cache
	$(DOCKER) build --no-cache -t ossia .

docker-up: ## Start the full Docker stack (ossia + postgres + caddy)
	$(COMPOSE) up -d --build

docker-up-direct: ## Start stack with ossia exposed directly on port 8000 (dev mode, no Caddy)
	OSSIA_EXPOSE_DIRECT=true $(COMPOSE) up -d --build ossia postgres

docker-up-minimal: ## Start only essential services (ossia + postgres, no proxy)
	$(COMPOSE) up -d --build ossia postgres

docker-down: ## Stop the Docker stack
	$(COMPOSE) down

docker-down-clean: ## Stop and remove all volumes
	$(COMPOSE) down -v

docker-logs: ## Tail logs from all services
	$(COMPOSE) logs -f

docker-logs-svc: ## Tail logs from a specific service, e.g. make docker-logs-svc svc=ossia
	$(COMPOSE) logs -f $(svc)

docker-ps: ## List running containers
	$(COMPOSE) ps

docker-restart: ## Restart a specific service, e.g. make docker-restart svc=caddy
	$(COMPOSE) restart $(svc)

docker-langgraph-build: ## Build with langgraph CLI (alternative packaging)
	langgraph build -t ossia-lg

# ──────────────────────────────────────────────────────────────────────────────
# Monitoring (requires --profile monitoring)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: monitor-up monitor-up-only monitor-down monitor-logs monitor-ps metrics

monitor-up: ## Start monitoring stack (Prometheus + Loki + Grafana) alongside ossia
	$(COMPOSE) $(MONITORING_PROFILE) up -d --build
	@echo "Grafana:   http://localhost:3000  (admin/ossia)"
	@echo "Prometheus: http://localhost:9090"
	@echo "Loki:      http://localhost:3100"

monitor-up-only: ## Start monitoring only (if the main stack is already running)
	$(COMPOSE) $(MONITORING_PROFILE) up -d prometheus loki grafana

monitor-down: ## Stop monitoring services
	$(COMPOSE) $(MONITORING_PROFILE) down

monitor-logs: ## Tail monitoring logs
	$(COMPOSE) $(MONITORING_PROFILE) logs -f

monitor-ps: ## Check monitoring service status
	$(COMPOSE) $(MONITORING_PROFILE) ps

metrics: ## Prometheus metrics query (requires monitoring stack running)
	@curl -s http://localhost:9090/api/v1/query?query=up | $(PYTHON) -m json.tool

# ──────────────────────────────────────────────────────────────────────────────
# Deployment — production operations
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: deploy-up deploy-down deploy-logs deploy-ps deploy-restart deploy-status

deploy-up: ## Start the production stack (ossia + postgres + caddy, no monitoring)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "Deployment started. Check status with:"
	@echo "  make deploy-ps"
	@echo "  curl http://localhost:80/ok"

deploy-down: ## Stop the production stack
	$(COMPOSE) down

deploy-logs: ## Tail production logs
	$(COMPOSE) logs -f

deploy-ps: ## List production containers and their health
	$(COMPOSE) ps

deploy-restart: ## Restart a specific service, e.g. make deploy-restart svc=ossia
	$(COMPOSE) restart $(svc)

deploy-status: ## Quick health check of all services
	@echo "=== OSSIA health check ==="
	@curl -sf http://localhost:80/ok && echo "  ossia: OK" || echo "  ossia: DOWN"
	@echo "=== Docker status ==="
	$(COMPOSE) ps --services --filter "status=running"

# ──────────────────────────────────────────────────────────────────────────────
# Quality & audit
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: audit eval openapi-drift

audit: ## Run the in-process audit harness (spins up server, hits /v1/audit, tears down)
	OSSIA_API_KEY=dev $(PYTHON) scripts/audit_ossia.py

eval: ## Run the golden-dataset eval
	OSSIA_API_KEY=dev $(PYTHON) scripts/eval_ossia.py

openapi-drift: ## Check for OpenAPI drift against the pinned spec
	$(PYTEST) tests/test_openapi_drift.py -v

# ──────────────────────────────────────────────────────────────────────────────
# TUI (terminal UI — separate Bun project)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: tui tui-install tui-dev tui-test tui-test-watch tui-test-coverage ossia ossia-setup

tui-install: ## Install TUI dependencies
	cd src/tui && bun install

tui-dev: ## Start the TUI in development mode
	cd src/tui && bun dev

tui: tui-dev ## Start the TUI (alias)

tui-test: ## Run TUI tests
	cd src/tui && bun test

tui-test-watch: ## Run TUI tests in watch mode
	cd src/tui && bun test --watch

tui-test-coverage: ## Run TUI tests with coverage report
	cd src/tui && bun test --coverage

ossia-setup: install tui-install env ## One-time setup: Python deps + TUI deps + .env
	@echo "Setup complete. Run 'make ossia' to start."

ossia: ## Start backend + TUI (unified) — use --server-only or --tui-only for single-mode
	$(PYTHON) -m core

# ──────────────────────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: clean-docker clean-python clean clean-all

clean-docker: ## Remove all Docker containers, volumes, and the built image
	$(COMPOSE) down -v
	-$(DOCKER) rmi ossia:latest 2>/dev/null || true

clean-python: ## Clean Python cache files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pyright" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true

clean: clean-docker clean-python ## Clean everything (Docker + Python caches)
	@echo "Cleanup complete."

clean-all: clean-docker ## Nuclear cleanup — removes .venv and .env too
	rm -rf .venv .langgraph_api/ .env
	@echo "Full cleanup complete. Run 'make setup' to reinitialize."

# ──────────────────────────────────────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-28s\033[0m %s\n", $$1, $$2}'
