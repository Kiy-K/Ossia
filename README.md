# Ossia — Portable AI Support Agent

<p align="center">
  <a href="https://github.com/Kiy-K/Ossia/actions/workflows/release.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Kiy-K/Ossia/release.yml?label=CI&logo=github" alt="CI">
  </a>
  <img src="https://img.shields.io/badge/coverage-84%25-green" alt="coverage">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="license">
  <a href="https://codebuff.com"><img src="https://img.shields.io/badge/built%20with-Codebuff-8B5CF6" alt="built with Codebuff"></a>
</p>

<p align="center">
  <strong>One-command install:</strong>
  <br>
  <code>curl -fsSL https://raw.githubusercontent.com/Kiy-K/Ossia/master/install.sh | bash</code>
</p>

**Ossia** is a portable, model-agnostic AI support agent built on [LangChain Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview). It bridges the gap between a raw LLM and a production-ready assistant — providing structured subagent delegation, human-in-the-loop approval, multimodal understanding, programmatic pipelines, and a real-time streaming event protocol.

Think of Ossia as a **digital teammate**: it can research your codebase, diagnose bugs, propose fixes, run tests, audit architecture, and execute multi-step workflows — all through a single unified HTTP API.

## Why Ossia?

| Problem | Ossia's approach |
|---|---|
| Agent frameworks tied to one provider | **Model-agnostic** — OpenRouter, OpenAI, Anthropic, Google, Nebius, or any OpenAI-compatible endpoint |
| Streaming feels like a black box | **Normalized event protocol** — every message, tool call, subagent spawn, and pipeline step is a typed, ordered, replayable event |
| Subagents are hard to observe | **Concurrent real-time normalization** — coordinator and subagent events stream together in a single ordered feed |
| Hard to debug agent runs | **Thread replay buffer** — `GET /v1/threads/{id}/events` replays the full event stream for any thread |
| Hand-written integration glue | **Spec-driven OpenAPI contract** — `specs/openapi.checked.json` is the pinned source of truth; `test_openapi_drift.py` catches drift |
| One-off scripts instead of API | **Unified `/v1/*` HTTP API** — scripts, notebooks, TUIs all talk to the same FastAPI server |
| Scattered top-level AI skill dirs | **Clean repo root** — all AI tool state and runtime artifacts are gitignored under `.kilocode/` |

## Architecture

```mermaid
flowchart TB
    Client["🌐 Client<br/><i>TUI / Web UI / curl / app</i>"]
    Proxy["🚀 Reverse Proxy<br/><i>Caddy / Nginx</i>"]
    API["⚙️ FastAPI Server<br/><i>POST /v1/chat<br/>POST /v1/chat/stream<br/>GET /v1/tools<br/>GET /v1/threads/*<br/>POST /v1/resume<br/>GET /v1/audit<br/>GET /metrics</i>"]
    MW["🔒 Middleware Stack<br/><i>10 layers: PII → Model Retry →<br/>Circuit Breaker → Tool Retry →<br/>Revision Cap → Tool Limit →<br/>Code Interpreter → Subagents →<br/>Caller Context</i>"]
    LLM["🤖 LLM Provider<br/><i>OpenRouter / OpenAI /<br/>Anthropic / Google</i>"]
    COORD["🎯 Coordinator Agent"]
    SUB["🔧 Subagents<br/><i>code-researcher<br/>bug-diagnostician<br/>fix-proposer<br/>test-runner</i>"]
    ASYNC["⏳ Async Subagents<br/><i>researcher / tester / auditor</i>"]
    PIPE["🏗️ Pipelines<br/><i>bugfix / audit / refactor</i>"]
    TOOLS["🛠️ Tools<br/><i>search_codebase<br/>internet_search<br/>run_tests<br/>grade_response<br/>send_response</i>"]
    NORM["🔄 EventNormalizer<br/><i>4 concurrent relays →<br/>asyncio.Queue → OssiaEvent</i>"]
    SSE["📤 SSE Stream"]
    BUF["💾 Thread Event Buffer<br/><i>GET /threads/{id}/events</i>"]
    POSTGRES["🗄️ Postgres<br/><i>checkpointing / memory</i>"]

    Client -->|HTTPS :443| Proxy
    Proxy -->|HTTP :8000| API
    API --> MW
    MW --> LLM
    LLM -->|response| COORD
    COORD -->|delegate| SUB
    COORD -->|delegate| ASYNC
    COORD -->|orchestrate| PIPE
    COORD -->|call| TOOLS
    TOOLS -.->|persist| POSTGRES
    COORD -->|v3 stream| NORM
    SUB -.->|v3 stream| NORM
    NORM --> SSE
    NORM --> BUF

    style Client fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style Proxy fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style API fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style MW fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style LLM fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style COORD fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style SUB fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style ASYNC fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style PIPE fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style TOOLS fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style NORM fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style SSE fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style BUF fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style POSTGRES fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
```

> 📊 **Architecture diagrams** — See [`docs/diagrams.md`](docs/diagrams.md) for detailed visualizations of every subsystem, including the middleware stack, subagent routing, request flow, event pipeline, and deployment topology.

### Subsystems at a glance

Ossia's architecture is composed of six interconnected subsystems, each documented in its own Architecture Decision Record (ADR) with detailed Mermaid diagrams:

| Subsystem | ADR | What it does | Key diagram |
|-----------|-----|-------------|-------------|
| **API Gateway** | [ADR-0014](docs/adr/0014-standalone-deployment.md) | FastAPI server, auth (Argon2), rate limiting, `/v1/*` routes | [Deployment topology](docs/diagrams.md#5-deployment-topology) |
| **Middleware Stack** | [ADR-0013](docs/adr/0013-production-readiness-middleware-stack.md) | 10-layer defense-in-depth: PII → model retry/fallback → circuit breaker → tool retry → caps → runtime | [Stack order](docs/diagrams.md#2-middleware-stack) + [Request flow](docs/diagrams.md#3-request-flow-sequence) |
| **Agent Runtime** | [ADR-0008](docs/adr/0008-subagent-design-and-routing.md) | Coordinator delegates to subagents with scoped tool permissions | [Subagent routing](docs/diagrams.md#1-subagent-routing) |
| **Event Streaming** | [ADR-0006](docs/adr/0006-streaming-v3-protocol.md) | v3 stream → normalizer (5 concurrent relays) → typed events → SSE | [Event pipeline](docs/diagrams.md#4-event-stream-pipeline) |
| **Memory & Persistence** | [ADR-0007](docs/adr/0007-agent-scoped-memory-and-episodic-recall.md) | Postgres + in-memory store, per-caller namespaces, thread replay buffer | [Deployment topology](docs/diagrams.md#5-deployment-topology) |
| **Orchestrator Pipelines** | [ADR-0008](docs/adr/0008-subagent-design-and-routing.md) | bugfix/audit/refactor pipelines via code interpreter with multi-step workflows | [Subagent routing](docs/diagrams.md#1-subagent-routing) |
| **Terminal UI** | `src/tui/` | OpenTUI/React 19 terminal client consuming `/v1/chat/stream` over SSE | [TUI README](src/tui/README.md) |
| **Web UI** | `src/webui/` | React 19 + Vite + Tailwind v4 web client with ChatGPT-style layout, session sidebar, SSE streaming | [Web UI README](src/webui/README.md) |

### Request lifecycle

A typical request flows through the stack as follows:

1. **Client** sends a request to `POST /v1/chat` via the reverse proxy (Caddy on port 443)
2. **FastAPI** authenticates via `X-API-Key` (Argon2 caller-id derivation), sets rate limits, injects `request_id` and `caller` context
3. **Middleware stack** processes the request through 10 layers — PII redaction strips secrets, model retry/fallback handles provider failures, circuit breaker blocks dead services, tool retry adds backoff, revision cap and tool-call limit prevent runaway agents
4. **Deep Agent runtime** invokes the coordinator, which may delegate to subagents or call tools
5. **EventNormalizer** converts the v3 stream into typed events in real-time via 5 concurrent relays
6. **Response** flows back through the middleware stack in reverse, serialized as SSE events or a JSON response
7. **Cleanup** clears context vars, emits Prometheus metrics, stores events in the thread buffer for replay

## Quick Start

### Using the Makefile (recommended)

```bash
# 1. Create .env from template
make env

# 2. Edit .env with your API keys
#    vim .env   (set OSSIA_API_KEY and OPENROUTER_API_KEY)

# 3. Install dependencies
make install

# 4. Start the dev server
make dev

# 5. Test it with curl, or open the Web UI:
curl -X POST http://localhost:8000/v1/chat \
  -H "X-API-Key: dev" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

# Or open the Web UI (needs a terminal for backend + one for UI):
make dev-web
```

### Using Docker

```bash
# 0. First, create .env (if you haven't already)
make env
# Edit .env with your API keys (OSSIA_API_KEY, OPENROUTER_API_KEY)

# 1. Build and start the full stack (ossia + postgres + caddy)
make docker-up

# 2. Verify it works
curl http://localhost/health                    # → {"status":"ok"}
curl http://localhost/metrics                   # → Prometheus metrics

# 3. Chat through the proxy
curl -X POST http://localhost/v1/chat \
  -H "X-API-Key: dev" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

# 4. Stream
curl -X POST http://localhost/v1/chat/stream \
  -H "X-API-Key: dev" \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain the architecture"}'
```

### Raw (no Docker)

```bash
# Install dependencies
uv pip install -e ".[dev,notebook]"

# Start the server
OSSIA_API_KEY=dev .venv/bin/python -m uvicorn core.api:app --host 127.0.0.1 --port 8000

# Chat
curl -X POST http://localhost:8000/v1/chat \
  -H "X-API-Key: dev" \
  -H "Content-Type: application/json" \
  -d '{"message": "What files are in the project?"}'
```

## Makefile

The project includes a `Makefile` with 40+ targets organized by category. Run `make help` to see all available commands.

| Category | Key targets |
|----------|-------------|
| **Setup** | `make install`, `make env` — install deps, create `.env` |
| **Development** | `make dev`, `make lint`, `make typecheck`, `make check` |
| **Testing** | `make test`, `make test-focused path=...`, `make test-coverage` |
| **Docker** | `make docker-up`, `make docker-down`, `make docker-logs`, `make docker-ps` |
| **Monitoring** | `make monitor-up`, `make monitor-down`, `make monitor-logs`, `make metrics` |
| **Quality** | `make audit`, `make eval`, `make openapi-drift` |
| **Spec** | `make spec-docs`, `make spec-coverage`, `make changelog` |
| **TUI** | `make tui`, `make tui-install` |
| **Web UI** | `make dev-web`, `make dev-all-web` |
| **Cleanup** | `make clean`, `make clean-all` |

## Docker Compose Stack

The `docker-compose.yml` orchestrates multiple services:

| Service | Role | Depends on |
|---------|------|-----------|
| `ossia` | The agent server (FastAPI) | postgres |
| `postgres` | State persistence, HITL checkpointing | — |
| `caddy` | Reverse proxy (auto HTTPS, security headers) | ossia |
| `prometheus` | Metrics collection (15s scrape interval) | ossia |
| `loki` | Log aggregation and storage | — |
| `grafana` | Dashboards (pre-loaded with Prometheus + Loki datasources) | prometheus, loki |

### Profiles

- **Default** (`docker compose up -d`): starts ossia + postgres + caddy
- **Monitoring** (`docker compose --profile monitoring up -d`): adds prometheus + loki + grafana

### Reverse Proxy

Ossia runs behind Caddy by default, which provides:
- Automatic Let's Encrypt HTTPS (when `DOMAIN` is set)
- Security headers (HSTS, XSS protection, etc.)
- Structured JSON access logs
- Traffic routing from port 80/443 to the internal ossia:8000

An Nginx config is also provided as an alternative (see `nginx.conf`).

## Monitoring Stack

Start with:
```bash
make monitor-up
```

| Component | Access | Purpose |
|-----------|--------|---------|
| **Prometheus** | `http://localhost:9090` | Scrapes `/metrics` from ossia every 15s. 30d retention. |
| **Loki** | `http://localhost:3100` | Aggregates Docker container logs |
| **Grafana** | `http://localhost:3000` (admin/ossia) | 11-panel pre-loaded dashboard |

The Grafana dashboard includes:
- Request rate and HTTP status code distribution
- Latency percentiles (p50, p95, p99)
- Error rate tracking
- Log explorer (Loki query interface)
- CPU and memory usage
- Service uptime

## Key Capabilities

### Model-Agnostic Runtime
Plug in any provider via a single env var: `OpenRouter`, `OpenAI`, `Anthropic`, `Google Gemini`, or any OpenAI-compatible endpoint. The agent framework, tools, subagents, and pipeline logic are entirely provider-independent.

### Real-Time Event Streaming
The EventNormalizer converts the raw DeepAgent v3 stream into a typed, ordered event protocol — coordinator messages, subagent lifecycle, tool calls, pipeline steps, async tasks, and multimodal artifacts all stream in a single ordered feed via SSE.

### Thread Replay Buffer
Every streamed run's normalized events are stored in an in-memory buffer. Clients can late-join or replay via `GET /v1/threads/{id}/events` — useful for session recovery, debugging, and audit.

### Subagent Delegation
7 synchronous subagents (`code-researcher`, `bug-diagnostician`, `fix-proposer`, `test-runner`, `ui-debugger`, `diagram-analyzer`, `visual-regression-reviewer`) and 3 async subagents (`researcher`, `tester`, `auditor`) handle specialized work without filling the coordinator's context.

### Programmatic Pipelines
Three orchestrator pipelines (`run_bugfix_pipeline`, `run_audit_pipeline`, `run_refactor_pipeline`) automate multi-step workflows via the code interpreter — diagnose → propose → test, or research → report, or research → plan → rewrite → validate.

### Multimodal Understanding
Accepts images, documents, audio, and video via `ChatRequest.artifacts`. Specialized subagents (`ui-debugger`, `diagram-analyzer`, `visual-regression-reviewer`) analyze visual content.

### HITL Approval
Human-in-the-loop interrupts on sensitive actions (`send_response`). Reviewers can approve, edit, reject, or respond via `POST /v1/threads/{id}/resume`.

### Spec-Driven Contract
The OpenAPI spec at `specs/openapi.checked.json` is the pinned source of truth. `pytest -k openapi_drift` catches any drift between the code and the contract. Breaking changes bump the URL prefix.

### Security Hardening
All code scanning alerts are resolved (0 open). Caller authentication uses
**Argon2id** for key hashing (memory-hard, GPU-resistant). The eval endpoint
uses a hardcoded dataset path to prevent path traversal. Web search fallback
uses the modern `ddgs` package. See `specs/changelog.md` for details.

### Clean Repo Root
All AI tool state, runtime artifacts, and skill directories are scoped to a
single `.kilocode/` directory at the repo root — no more scattered `.claude/`,
`.windsurf/`, `.openhands/`, or `.firecrawl/` directories littering the tree.
The `.gitignore` keeps everything but `kilocode.json` out of version control.

## Finishing Touches

### Terminal UI

Ossia ships with a full-featured **Terminal UI** built with OpenTUI and React 19.
It connects to the backend via SSE and provides multi-pane visualizations:

| Panel | What it shows |
|-------|--------------|
| **Timeline** | Chronological log of all events (messages, tool calls, subagents, pipeline steps) |
| **ReAct** | Agent reasoning loops — thoughts, actions, and observations in real time |
| **Subagents** | Lifecycle of each active subagent (spawned → running → completed/error) |
| **Tools** | Active and completed tool calls with inputs and outputs |
| **Background Tasks** | Long-running async subagent tasks (researcher, tester, auditor) |
| **Status Bar** | Thread ID, agent/tool counts, async task count, and run state |

```bash
cd src/tui && bun install && bun dev
```

See the [TUI README](src/tui/README.md) for full documentation.

### Web UI

Ossia also ships with a **Web UI** built with React 19, Vite, and Tailwind CSS v4.
It connects to the backend via SSE and provides a ChatGPT-style browser interface:

```bash
# Start both backend + Web UI together:
make dev-all-web

# Or start just the Web UI (backend must be running separately):
make dev-web

# Open http://localhost:5173 in your browser
```

The Web UI features:

| Feature | Description |
|---------|-------------|
| **ChatGPT-style layout** | Minimal header, centered empty state, sticky composer, high-contrast bubbles |
| **Session sidebar** | Thread list via `GET /v1/threads`, click to switch, New Chat, lazy-loaded titles |
| **Dark/light mode** | Sun/Moon toggle, persists to localStorage, no flash on load |
| **Theme-aware favicon** | Matches dark/light mode automatically |
| **Connection config** | Gear icon to set API URL and key, live connection indicator |
| **SSE streaming** | Real-time agent responses with inline tool call tracking |
| **ToolGroup** | Collapsible card for consecutive tool calls |
| **Markdown rendering** | Full syntax highlighting via Shiki |

```bash
# Run e2e tests (auto-starts Vite dev server)
cd src/webui && npm run test:e2e
```

See the [Web UI README](src/webui/README.md) for full documentation.

## Configuration

All settings are driven by environment variables parsed through Pydantic in `src/core/config.py`.

| Variable | Description | Default |
|---|---|---|
| `OSSIA_API_KEY` | API key for authenticating requests | — |
| `PROVIDER` | Model provider | `openrouter` |
| `MODEL` | Model identifier | `openai/gpt-4o-mini` |
| `OPENROUTER_API_KEY` | OpenRouter key | — |
| `OPENAI_API_KEY` | OpenAI key | — |
| `ANTHROPIC_API_KEY` | Anthropic key | — |
| `GOOGLE_API_KEY` | Google Gemini key | — |
| `POSTGRES_URL` | Postgres DSN for checkpointing | — |
| `ENABLE_HUMAN_REVIEW` | Pause before sending | `true` |
| `MAX_REVISION_LOOPS` | Revision cap | `3` |
| `TAVILY_API_KEY` | Web search (falls back to DuckDuckGo) | — |
| `GRAFANA_USER` | Grafana admin username | `admin` |
| `GRAFANA_PASSWORD` | Grafana admin password | `ossia` |
| `PROMETHEUS_RETENTION` | Prometheus data retention period | `30d` |
| `LOG_DRIVER` | Docker log driver | `json-file` |

## Project Structure

```
src/
  core/              # Core library: agent, api, tools, events, memory,
                     # middleware, config, schemas, graphs, orchestrators
  tui/               # Terminal UI (bun + OpenTUI/React)
  webui/             # Web UI (React + Vite + Tailwind v4)
tests/               # 100+ tests across all modules
scripts/             # Audit, eval, OpenAPI spec generation, coverage matrix
specs/               # OpenAPI contract, changelog, feature specs, coverage
monitoring/          # Prometheus, Loki, Grafana configs
docs/
  adr/               # Architecture Decision Records (0001..0014)
  agents/            # Agent context reference
  skills/            # Loadable skill files (web-search, code-review)
  diagrams.md        # 📊 Index of all architecture diagrams
```

## Deploy

Ossia ships as a single FastAPI app. Deploy anywhere you can run a Docker container or a `uvicorn` process:

```bash
# Using the Makefile (recommended)
make docker-build
make docker-up

# Manual Docker
docker build -t ossia .
docker run -p 8000:8000 -e OSSIA_API_KEY=... -e OPENROUTER_API_KEY=... ossia

# With full stack (postgres + caddy)
docker compose up -d --build

# With monitoring stack
docker compose --profile monitoring up -d

# Raw process
.venv/bin/python -m uvicorn core.api:app --host 0.0.0.0 --port 8000
```

## License

MIT
