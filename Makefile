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

.PHONY: dev dev-live dev-all dev-web dev-all-web format lint typecheck check

dev: ## Start the dev server with hot reload (requires .env with OSSIA_API_KEY + provider key)
	@if grep -qE '^POSTGRES_URL=.*@postgres:' .env 2>/dev/null; then \
		bash -c 'set -e; \
			was_running=$$(docker compose ps --status running --services postgres 2>/dev/null || true); \
			cleanup() { \
				if [ -z "$$was_running" ]; then \
					echo ""; \
					echo "==> Stopping postgres container (was auto-started by \`make dev\`)..."; \
					docker compose stop postgres >/dev/null 2>&1 || true; \
				else \
					echo ""; \
					echo "==> Leaving postgres container running (it was already up before \`make dev\`)."; \
				fi; \
			}; \
			trap cleanup EXIT INT TERM; \
			echo "==> Starting postgres container (will be stopped on Ctrl+C unless it was already running)..."; \
			docker compose up -d postgres; \
			echo "==> Waiting for postgres to be ready..."; \
			for i in $$(seq 1 30); do \
				docker compose exec -T postgres pg_isready -U ossia -d ossia >/dev/null 2>&1 && break; \
				sleep 1; \
			done; \
			echo "==> Starting uvicorn (POSTGRES_URL=127.0.0.1)..."; \
			POSTGRES_URL=postgresql://ossia:ossia@127.0.0.1:5432/ossia $(PYTHON) -m uvicorn core.api:app --host 127.0.0.1 --port 8000 --reload'; \
	else \
		echo "==> POSTGRES_URL does not point at the Docker host; running uvicorn as-is."; \
		$(PYTHON) -m uvicorn core.api:app --host 127.0.0.1 --port 8000 --reload; \
	fi

dev-live: ## Start the dev server without hot reload
	$(PYTHON) -m uvicorn core.api:app --host 0.0.0.0 --port 8000

dev-all: ## Start backend (background) + TUI (foreground); Ctrl+C tears down both
	@bash -c 'set -e; \
		set -m; \
		$(MAKE) dev > /tmp/ossia-backend.log 2>&1 & \
		BACKEND_PID=$$!; \
		BACKEND_PGID=$$(ps -o pgid= -p $$BACKEND_PID 2>/dev/null | tr -d " "); \
		cleanup() { \
			echo ""; \
			echo "==> Cleaning up (backend pgid $$BACKEND_PGID)..."; \
			if [ -n "$$BACKEND_PGID" ]; then \
				kill -TERM -$$BACKEND_PGID 2>/dev/null || true; \
				for i in $$(seq 1 5); do \
					kill -0 $$BACKEND_PID 2>/dev/null || break; \
					sleep 1; \
				done; \
				kill -KILL -$$BACKEND_PGID 2>/dev/null || true; \
			fi; \
			wait $$BACKEND_PID 2>/dev/null || true; \
		}; \
		trap "cleanup; exit 130" INT TERM; \
		trap cleanup EXIT; \
		echo "==> Waiting for backend health (http://127.0.0.1:8000/health)..."; \
		for i in $$(seq 1 60); do \
			curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 && { echo "    ready."; break; }; \
			sleep 1; \
		done; \
		if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then \
			echo "==> Backend did not become ready in 60s. Tail of /tmp/ossia-backend.log:"; \
			tail -20 /tmp/ossia-backend.log; \
			exit 1; \
		fi; \
		echo "==> Starting TUI..."; \
		$(MAKE) tui; \
		echo "==> TUI exited."'

dev-web: ## Start the Web UI dev server only (requires backend running separately)
	cd src/webui && npm run dev

dev-all-web: ## Start backend (background) + Web UI (foreground); Ctrl+C tears down both
	@bash -c 'set -e; \
		set -m; \
		$(MAKE) dev > /tmp/ossia-backend.log 2>&1 & \
		BACKEND_PID=$$!; \
		BACKEND_PGID=$$(ps -o pgid= -p $$BACKEND_PID 2>/dev/null | tr -d " "); \
		cleanup() { \
			echo ""; \
			echo "==> Cleaning up (backend pgid $$BACKEND_PGID)..."; \
			if [ -n "$$BACKEND_PGID" ]; then \
				kill -TERM -$$BACKEND_PGID 2>/dev/null || true; \
				for i in $$(seq 1 5); do \
					kill -0 $$BACKEND_PID 2>/dev/null || break; \
					sleep 1; \
				done; \
				kill -KILL -$$BACKEND_PGID 2>/dev/null || true; \
			fi; \
			wait $$BACKEND_PID 2>/dev/null || true; \
		}; \
		trap "cleanup; exit 130" INT TERM; \
		trap cleanup EXIT; \
		echo "==> Waiting for backend health (http://127.0.0.1:8000/health)..."; \
		for i in $$(seq 1 60); do \
			curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 && { echo "    ready."; break; }; \
			sleep 1; \
		done; \
		if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then \
			echo "==> Backend did not become ready in 60s. Tail of /tmp/ossia-backend.log:"; \
			tail -20 /tmp/ossia-backend.log; \
			exit 1; \
		fi; \
		echo "==> Starting Web UI (http://127.0.0.1:5173)..."; \
		cd src/webui && npx vite --host 127.0.0.1 --port 5173; \
		echo "==> Web UI exited."'

format: ## Format code with ruff
	$(RUFF) check --fix src tests scripts
	$(RUFF) format src tests scripts

lint: ## Lint with ruff
	$(RUFF) check src tests scripts

typecheck: ## Typecheck with pyright
	# ponytail: mypy 2.1 cannot parse numpy 2.2+ PEP 695 stubs;
	# pyright handles them correctly. Re-add mypy when it supports
	# PEP 695 stub parsing.
	$(PYRIGHT) src

check: lint typecheck ## Run all static checks (lint + typecheck)

# ──────────────────────────────────────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: test test-focused test-all test-coverage webui-e2e

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

webui-e2e: ## Run Web UI Playwright e2e tests
	cd src/webui && npm run test:e2e

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

.PHONY: tui tui-install tui-dev tui-test tui-test-watch tui-test-coverage webui webui-e2e ossia ossia-setup

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

# ──────────────────────────────────────────────────────────────────────────────
# Web UI (React + Vite + Tailwind — separate npm project)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: webui webui-install webui-dev webui-build webui-typecheck

webui-install: ## Install Web UI dependencies
	cd src/webui && npm install

webui-dev: ## Start Web UI dev server
	cd src/webui && npm run dev

webui-build: ## TypeScript check + production build Web UI
	cd src/webui && npm run build

webui-typecheck: ## TypeScript type-check Web UI only
	cd src/webui && npm run typecheck

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
# Versioning & release
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: bump-version show-version

CURRENT_VERSION := $(shell $(PYTHON) -c "import re; print(re.search(r'''^version\s*=\s*\"([^\"]+)\"''', open('pyproject.toml').read(), re.M).group(1))" 2>/dev/null || echo "unknown")

show-version: ## Show the current version from pyproject.toml
	@echo "$(CURRENT_VERSION)"

bump-version: ## Bump version, commit, and tag. Usage: make bump-version VERSION=0.9.0 [MESSAGE='...']
ifndef VERSION
	$(error Usage: make bump-version VERSION=0.9.0)
endif
	@if echo "$(VERSION)" | grep -vqE '^[0-9]+\.[0-9]+\.[0-9]+$$'; then \
		echo "ERROR: VERSION must be in X.Y.Z format (e.g. 0.9.0)"; \
		exit 1; \
	fi
	@if ! git diff --quiet --cached; then \
		echo "ERROR: You have staged but uncommitted changes. Commit or stash them first."; \
		exit 1; \
	fi
	@if ! git diff --quiet; then \
		echo "ERROR: You have unstaged changes. Commit or stash them first."; \
		exit 1; \
	fi
	@echo "Bumping version from $(CURRENT_VERSION) to $(VERSION)..."
	$(PYTHON) -c "\
import re;\
content = open('pyproject.toml').read();\
new = re.sub(r'(?m)^version\s*=\s*\"[^\"]+\"', 'version = \"$(VERSION)\"', content);\
open('pyproject.toml', 'w').write(new)\
"
	@git add pyproject.toml
	MESSAGE="$(or $(MESSAGE),Release v$(VERSION))"
	git commit -m "$$MESSAGE"
	git tag -a "v$(VERSION)" -m "$$MESSAGE"
	@echo ""
	@echo "Created tag v$(VERSION). Push with:"
	@echo "  git push origin master --tags"

# ──────────────────────────────────────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-28s\033[0m %s\n", $$1, $$2}'
