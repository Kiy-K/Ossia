# ADR-0014: Standalone deployment — architecture and operations

**Status:** accepted.
**Date:** 2026-06-28.
**Supersedes:** the ad-hoc docker-compose.yml usage (now documented as the primary production path).

## Context

The LangSmith standalone server deployment guide prescribes a deployment model using:

- A Docker image built via `langgraph build` (LangGraph Platform server)
- Redis for pub-sub
- Postgres for metadata/background tasks
- LangSmith API key + LangGraph Cloud license key
- Health check at `/ok` returning `{"ok": true}`

Ossia is **not** a LangGraph Platform deployment. It runs a custom FastAPI server (`core.api:app`) with custom `/v1/*` routes. The `langgraph.json` file defines 4 sub-graphs for the LangGraph Platform deployment model (used only for async subagent execution in cloud deployments), but the primary runtime is the FastAPI server.

This ADR documents the standalone deployment architecture based on the existing docker-compose stack, adapted to address the production readiness concerns from the LangSmith guide while keeping the custom API surface.

## Decision

Deploy using the existing **docker-compose stack** as the production path, with the following adaptations:

### 1. Service architecture

```mermaid
flowchart LR
    subgraph External["🌐 External"]
        INTERNET["Internet / Client"]
    end

    subgraph Proxy["🚀 Reverse Proxy"]
        CADDY["Caddy<br/><i>TLS termination, routing,<br/>auto HTTPS via Let's Encrypt</i>"]
    end

    subgraph App["⚙️ Ossia Application"]
        OSSIA["ossia FastAPI Server<br/><i>port 8000</i>"]
        API["/v1/chat, /v1/chat/stream<br/>/v1/tools, /v1/audit<br/>/v1/eval, /v1/threads/*<br/>/ok, /health"]
        OSSIA --- API
    end

    subgraph Data["💾 Persistence"]
        PG["Postgres 16<br/><i>checkpointing, memory store,<br/>HITL state persistence</i>"]
        INMEMORY["In-Memory Store<br/><i>event buffer, rate limiter,<br/>circuit breaker state</i>"]
    end

    subgraph Observability["📊 Observability (optional)"]
        PROM["Prometheus<br/><i>metrics collection</i>"]
        LOKI["Loki<br/><i>log aggregation</i>"]
        GRAFANA["Grafana<br/><i>dashboards & visualization</i>"]
        PROM --> GRAFANA
        LOKI --> GRAFANA
    end

    subgraph Tracing["🔍 Tracing (optional)"]
        LS["LangSmith<br/><i>trace recording & debugging</i>"]
    end

    INTERNET -->|"HTTPS :443"| CADDY
    CADDY -->|"HTTP :8000"| OSSIA
    OSSIA -->|"persistence"| PG
    OSSIA -.->|"metrics"| PROM
    OSSIA -.->|"logs"| LOKI
    OSSIA -.->|"traces"| LS

    style INTERNET fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style CADDY fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style OSSIA fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style API fill:#0d0d1a,stroke:#e94560,stroke-width:1px,color:#888
    style PG fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style INMEMORY fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style PROM fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style LOKI fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style GRAFANA fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style LS fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#888

    style External fill:#0d0d1a,stroke:#e94560,stroke-width:1px,color:#888
    style Proxy fill:#0d0d1a,stroke:#0f3460,stroke-width:1px,color:#888
    style App fill:#0d0d1a,stroke:#e94560,stroke-width:1px,color:#888
    style Data fill:#0d0d1a,stroke:#533483,stroke-width:1px,color:#888
    style Observability fill:#0d0d1a,stroke:#0f3460,stroke-width:1px,color:#888
    style Tracing fill:#0d0d1a,stroke:#16213e,stroke-width:1px,color:#888
```

- **Caddy** terminates TLS with automatic Let's Encrypt certificates. Configured via `DOMAIN` env var.
- **ossia** serves the custom `/v1/*` API. No LangGraph Platform routes. No Redis required.
- **Postgres** provides persistent checkpointing and memory store. Required for HITL, optional for basic operation.
- **Monitoring** is opt-in via `--profile monitoring`. Includes Prometheus (metrics), Loki (logs), and Grafana (dashboards).

### 2. Required vs optional infrastructure

| Component | Required | Purpose |
|-----------|----------|---------|
| `OSSIA_API_KEY` | Required | Auth for all API routes |
| Provider API key | Required | LLM inference (OPENROUTER_API_KEY, OPENAI_API_KEY, etc.) |
| Postgres | Optional | Checkpointing, memory persistence, HITL |
| Caddy | Optional | TLS termination, reverse proxy |
| Redis | Not needed | No pub-sub dependency in current architecture |
| LangSmith API key | Optional | Tracing (LANGSMITH_TRACING=true) |
| LangGraph Cloud license | Not needed | Not running LangGraph Platform server |

### 3. Health check compatibility

The LangSmith guide expects a `/ok` endpoint returning `{"ok": true}`. The existing `/health` endpoint returns `{"status": "ok"}`. Both are now served:

- `GET /health` — returns `{"status": "ok"}` with rate limiting, auth-optional (deprecated, kept for backward compat)
- `GET /ok` — returns `{"ok": true}` without rate limiting, no auth. Suitable for load balancer probes, Docker HEALTHCHECK, and k8s liveness probes.

### 4. Env configuration for production

Required in `.env` or passed as environment variables:

```
OSSIA_API_KEY=<strong-random-secret>
PROVIDER=openrouter
MODEL=openai/gpt-4o-mini
OPENROUTER_API_KEY=<your-key>

# Postgres (required for HITL, recommended for production)
POSTGRES_URL=postgresql://ossia:ossia@postgres:5432/ossia

# Optional: monitoring
LOG_FORMAT=json
LOG_LEVEL=INFO
```

Optional tuning (see `.env.example` for defaults):

```
# Middleware tuning
RETRY_MAX_ATTEMPTS=3
CIRCUIT_BREAKER_FAILURE_THRESHOLD=3
MODEL_RETRY_MAX_ATTEMPTS=2
TOOL_CALL_LIMIT=25

# Model fallback (optional — set both to enable)
FALLBACK_PROVIDER=openai
FALLBACK_MODEL=gpt-4o-mini
```

### 5. Production deployment commands

**⚠️ Important:** Before running `docker compose up`, ensure no shell-exported env vars shadow the `.env` file. Docker Compose reads environment variables with this precedence (highest to lowest):

1. Shell-exported variables (`export POSTGRES_URL=...` in `.bashrc`, `.zshrc`, or active terminal)
2. `.env` file (auto-read by docker compose from the project root)
3. Defaults in `docker-compose.yml` (via `${VAR:-default}` syntax)

During containerized testing, an `export POSTGRES_URL=postgresql://ossia:ossia@localhost:5432/ossia` in the parent shell caused the container to connect to `localhost` (its own loopback) instead of the `postgres` service. The fix is to unset the var:

```bash
unset POSTGRES_URL
```

Or launch compose in a clean subshell:

```bash
env -u POSTGRES_URL docker compose up -d
```

This affects any env var that has a docker-compose default, including `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `LOG_FORMAT`, etc. When in doubt, check which vars are exported:

```bash
env | grep -E '^(POSTGRES|OPENROUTER|OPENAI|ANTHROPIC|TAVILY|LANGSMITH)'
```

---

```bash
# First deployment
cp .env.example .env
# Edit .env with your keys

# Build and start
make deploy-up

# Verify
curl http://localhost:80/ok
# → {"ok":true}

# With custom domain (auto HTTPS)
DOMAIN=api.ossia.dev make deploy-up

# Production health check
make deploy-status

# View logs
make deploy-logs

# Restart a service
make deploy-restart svc=ossia

# Stop
make deploy-down
```

### 6. Kubernetes consideration

For k8s deployments, the docker-compose.yml is the reference architecture but not directly portable. Key differences:

- **Config**: k8s Secrets instead of `.env` file. The `langgraph.json` env pattern does not apply.
- **Probes**: Use `/ok` for liveness and readiness probes.
- **Postgres**: Use a managed Postgres instance or operator (e.g., CloudNativePG).
- **Caddy**: Use an Ingress controller (nginx-ingress, traefik, or Caddy ingress).
- **Monitoring**: Export metrics from `/metrics` to Prometheus Operator's `ServiceMonitor`.

A full k8s helm chart is out of scope for v1. If k8s becomes the primary deployment target, the `docker compose` config serves as the specification for service topology, env vars, and health checks.

### 7. Differences from the LangSmith standalone server guide

| LangSmith guide | Ossia implementation | Rationale |
|-----------------|---------------------|-----------|
| `langgraph build` Docker image | Standard `Dockerfile` (mutli-stage) | Custom FastAPI server needs custom build |
| `/runs` API | `/v1/chat`, `/v1/chat/stream`, etc. | Custom API surface for dev-concierge agent |
| Redis pub-sub | Not used | No cross-worker pub-sub needed in single-process |
| LangGraph Cloud license | Not needed | Not running LangGraph Platform |
| `LANGSMITH_API_KEY` for server auth | `OSSIA_API_KEY` for custom auth | Custom auth with Argon2 caller-id derivation |
| `/ok` health check | `/ok` (new) + `/health` (existing) | Added `/ok` for compatibility |
| Postgres + Redis required | Postgres optional | Agent works without persistence (in-memory store) |

## Consequences

### Positive

- **No new dependencies.** The existing docker-compose stack, Makefile, and Dockerfile are adapted, not replaced.
- **Minimal migration.** Existing `.env.example` and docker-compose.yml are updated, not rewritten.
- **Custom API surface preserved.** All `/v1/*` routes (chat, stream, threads, tools, audit, eval) work identically.
- **Reduced infrastructure.** No Redis, no LangGraph Cloud license, no LangSmith server auth needed.
- **Monitoring stack is optional but documented.** Prometheus/Loki/Grafana can be added on demand without config changes.

### Negative

- **Single-process limitation.** The current architecture runs one uvicorn worker (`--workers 1`). Multi-worker would need Redis-based state sharing for the in-memory event buffer and rate limiter. Postgres-backed checkpointing is already thread-safe.
- **No built-in horizontal scaling.** To scale, deploy multiple instances behind Caddy with sticky sessions (for in-memory event buffer) or switch to a Postgres- or Redis-backed event buffer.
- **Caddy dependency for TLS.** The stack defaults to Caddy; switching to nginx (commented out in compose) requires manual SSL cert management.
- **No k8s manifests.** A Helm chart or k8s resource set is future work if k8s becomes the primary target.
- **HEAD requests return 405.** The `/ok` and `/health` endpoints do not support the HEAD HTTP method. Some load balancers and monitoring tools (e.g., k8s liveness probes with `httpGet`) use HEAD requests to reduce response size. If using k8s, configure probes with `scheme: HTTP` and `method: GET` instead. This was confirmed during containerized testing — a `curl -sI http://localhost:80/ok` returned `HTTP/1.1 405 Method Not Allowed` with `Allow: GET`.

### Neutral / Future

- **Air-gapped deployment.** The standalone server works without egress to `beacon.langchain.com` (no LangGraph Cloud license check). The only egress needed is to the LLM provider API. If the LLM is also local (Ollama), the entire stack can run air-gapped.
- **Postgres connection pooling.** For high concurrency, add PgBouncer as a sidecar. The current `AsyncConnection.connect()` pattern establishes a new connection per request; pooling would reduce latency.
- **Multi-region.** Not addressed in v1. The Postgres checkpointer and in-memory store are single-region by design. Cross-region replication requires a different architecture.

## Status

Accepted. Implemented alongside ADR-0013 (production readiness middleware stack).

## References

- LangSmith standalone server guide: https://docs.langchain.com/langsmith/deploy-standalone-server
- `Dockerfile` — multi-stage build
- `docker-compose.yml` — service topology
- `Makefile` — deploy-* targets
- `Caddyfile` — TLS termination and reverse proxy config
- `src/core/api.py` — `/ok` and `/health` endpoints
- `docs/adr/0013-production-readiness-middleware-stack.md` — middleware decisions
