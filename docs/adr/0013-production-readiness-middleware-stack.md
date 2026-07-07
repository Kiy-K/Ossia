# ADR-0013: Production readiness — middleware stack, observability, and multi-tenancy

**Status:** accepted.
**Date:** 2026-06-28.
**Supersedes:** ADR-0007 §"No per-user scoping" (user-scoped memory was added in this pass).
**Supersedes:** the implicit "hardcoded middleware params" pattern (all middleware parameters are now env-configurable).

## Context

The Deep Agents production guide (`going-to-production`) prescribes a set of production readiness features: PII redaction, retry with backoff, circuit breakers, tool-call limits, model retry and fallback, structured logging, rate limiting, multi-tenancy, and observability. Prior to this ADR, Ossia had:

- A working `RetryToolMiddleware` with hardcoded parameters.
- A `RevisionLoopCapMiddleware` with hardcoded `max_loops`.
- No PII redaction middleware.
- No circuit breaker middleware.
- No tool-call limit middleware.
- No model-level retry or fallback middleware.
- No Prometheus metrics or Grafana dashboards.
- No structured logging with request/caller context.
- A single-tenant memory namespace `("ossia",)` shared by all callers.
- All subagents receiving the full tool set (including `create_pr`, `run_bugfix_pipeline`, etc.).
- Hardcoded middleware parameters that required code changes to tune.

The guide's checklist is comprehensive. This ADR documents the decisions made to close the gap, the order in which middleware compose, and the remaining gaps that are deferred to future passes.

## Decision

### 1. Middleware stack — composition order

Middleware compose as a stack: the first in the list is the outermost layer (runs first on the way in, last on the way out). The order is chosen so that each layer handles a specific failure domain before the next layer wastes resources:

```mermaid
flowchart TB
    subgraph Security["🔒 Security & Privacy"]
        direction LR
        PII["PIIRedactionMiddleware<br/><i>strip secrets from tool inputs</i>"]
    end

    subgraph Model["🤖 Model Reliability"]
        direction LR
        MR["ModelRetryMiddleware<br/><i>retry transient LLM failures</i>"]
        MF["ModelFallbackMiddleware<br/><i>switch provider on outage</i>"]
        MR --> MF
    end

    subgraph Resilience["🛡️ Service Resilience"]
        direction LR
        CB["CircuitBreakerMiddleware<br/><i>fail-fast on overloaded services</i>"]
        RT["RetryToolMiddleware<br/><i>retry tool calls with backoff</i>"]
        CB --> RT
    end

    subgraph Governance["⚖️ Governance & Limits"]
        direction LR
        RC["RevisionLoopCapMiddleware<br/><i>cap response revision loops</i>"]
        TCL["ToolCallLimitMiddleware<br/><i>cap total tool calls per run</i>"]
        RC --> TCL
    end

    subgraph Runtime["⚡ Runtime & Context"]
        direction LR
        CI["CodeInterpreterMiddleware<br/><i>sandboxed QuickJS eval</i>"]
        AS["AsyncSubAgentMiddleware<br/><i>long-running background tasks</i>"]
        CC["make_caller_context_middleware<br/><i>inject caller identity into prompts</i>"]
        CI --> AS --> CC
    end

    PII -->|outermost → innermost| Model --> Resilience --> Governance --> Runtime

    style PII fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style MR fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style MF fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style CB fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style RT fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style RC fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style TCL fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style CI fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style AS fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style CC fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff

    style Security fill:#0d0d1a,stroke:#e94560,stroke-width:1px,color:#888
    style Model fill:#0d0d1a,stroke:#0f3460,stroke-width:1px,color:#888
    style Resilience fill:#0d0d1a,stroke:#16213e,stroke-width:1px,color:#888
    style Governance fill:#0d0d1a,stroke:#533483,stroke-width:1px,color:#888
    style Runtime fill:#0d0d1a,stroke:#0f3460,stroke-width:1px,color:#888
```

**Layer-by-layer order (outermost to innermost):**

1. `PIIRedactionMiddleware` — strip secrets from tool inputs first
2. `ModelRetryMiddleware` — retry transient LLM provider failures
3. `ModelFallbackMiddleware` — switch to secondary model on provider failure
4. `CircuitBreakerMiddleware` — fail fast when a service is repeatedly down
5. `RetryToolMiddleware` — retry transient tool-call failures with backoff
6. `RevisionLoopCapMiddleware` — cap response revision loops
7. `ToolCallLimitMiddleware` — cap total tool calls per run
8. `CodeInterpreterMiddleware` — sandboxed QuickJS eval
9. `AsyncSubAgentMiddleware` — long-running background tasks
10. `make_caller_context_middleware` — inject caller identity into system prompt

**Rationale for the ordering:**

- **PII first.** Sensitive data should never reach any downstream middleware or the tool handler. Placing PII redaction first ensures secrets are stripped before logging, retry, or circuit-breaker logic can observe them.
- **Model retry/fallback before tool middleware.** These intercept the model call (`awrap_model_call`), which happens before any tool is invoked. Placing them early means provider failures are handled without consuming tool-call budget.
- **Circuit breaker before retry.** If a service is down, the circuit breaker blocks immediately instead of letting the retry middleware exhaust its attempts on a definitely-dead backend.
- **Retry before caps.** Retries count as tool calls; exhausting retries on a permanently failed service would consume the tool-call budget. The circuit breaker (placed before) prevents this for services it covers; internal middleware calls (`grade_response`, `send_response`) are excluded from the tool-call limit.
- **Revision cap before tool-call limit.** `grade_response` is excluded from the tool-call count but handled by the revision cap, so the two caps compose sequentially without double-counting.
- **Caller context last.** It runs closest to the model call via `@dynamic_prompt`, ensuring the caller identity is visible in every LLM turn.

**Request flow sequence:**

The following sequence diagram traces a complete request through the entire stack, showing how the middleware layers compose as nested handlers. Each middleware wraps the next, so the request enters outermost-first and returns innermost-last:

```mermaid
sequenceDiagram
    autonumber
    participant Client as Client / TUI
    participant FastAPI as FastAPI Gateway
    participant PII as PII Redaction
    participant ModelL as Model Layer
    participant Resilience as Resilience Layer
    participant Governance as Governance Layer
    participant Runtime as Runtime Layer
    participant LLM as LLM Provider
    participant Tools as External Tools

    rect rgb(13, 13, 26)
        Note over Client,Tools: 🔐 AUTH & SETUP
        Client->>FastAPI: POST /v1/chat
        FastAPI->>FastAPI: Verify X-API-Key (Argon2)
        FastAPI->>FastAPI: Set request_id, caller context
        FastAPI->>FastAPI: Rate limit check
    end

    rect rgb(13, 13, 26)
        Note over PII,Tools: ⬇️ INBOUND — outermost → innermost
        FastAPI->>PII: Forward request
        activate PII
        PII->>PII: Redact PII from tool args
        PII->>ModelL: Forward sanitized
        activate ModelL

        ModelL->>LLM: ainvoke(messages)
        activate LLM
        LLM-->>ModelL: Response (text or tool_calls)
        deactivate LLM

        alt Tool calls present
            ModelL->>Resilience: Forward tool calls
            activate Resilience
            Resilience->>Resilience: Check circuit breaker (CLOSED?)
            Resilience->>Governance: Forward allowed calls
            activate Governance
            Governance->>Governance: Check revision cap
            Governance->>Governance: Increment tool call count
            Governance->>Runtime: Forward
            activate Runtime
            Runtime->>Tools: Execute tool
            activate Tools
            Tools-->>Runtime: Results
            deactivate Tools
            Runtime-->>Governance: Results
            deactivate Runtime
            Governance-->>Resilience: Results
            deactivate Governance
            Resilience-->>ModelL: Results
            deactivate Resilience

            ModelL->>LLM: Call model with results (grade_response)
            activate LLM
            LLM-->>ModelL: Revised response
            deactivate LLM
        end

        ModelL-->>PII: Final response
        deactivate ModelL
        PII-->>FastAPI: Response
        deactivate PII
    end

    rect rgb(13, 13, 26)
        Note over FastAPI,Client: ⬆️ OUTBOUND — innermost → outermost
        FastAPI->>FastAPI: Serialize SSE events
        FastAPI-->>Client: StreamingResponse / JSON
    end

    rect rgb(13, 13, 26)
        Note over FastAPI,Tools: 🧹 CLEANUP
        FastAPI->>FastAPI: Clear request context
        FastAPI->>FastAPI: Emit Prometheus metrics
    end
```

The stack's depth is visible in the activation/deactivation nesting: each middleware layer activates the next, and the LLM call sits at the center. On return, each layer deactivates in reverse order, unwinding the stack.

### 2. PII redaction (`PIIRedactionMiddleware`)

**Pattern-based regex redaction.** Strips emails, API keys, bearer tokens, phone numbers, SSNs, RFC 1918 IPs, and credential-bearing URLs from tool call inputs. Applied recursively to nested dicts/lists up to depth 20. The order of patterns is intentional: credential URLs are checked before email addresses to avoid the email regex consuming the ``@`` before the credential URL pattern sees it.

**Scope:** tool call `args` dicts only. The system prompt and LLM response are not redacted — the model is trusted to follow the "do not expose secrets" instruction. This is defense-in-depth for the tool-input path, which is the highest-risk surface.

### 3. Model retry (`ModelRetryMiddleware`)

**Transient exceptions only.** Retries on a curated set of exception types discovered at module init from the installed SDKs: `openai.RateLimitError`, `openai.APITimeoutError`, `openai.APIConnectionError`, `openai.InternalServerError`, `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RemoteProtocolError`. Non-transient errors (auth, bad request) propagate immediately, ensuring the fallback middleware (when configured) only activates on genuine provider failures.

**Defaults:** 2 attempts, 0.5s initial interval, 2x backoff. All env-configurable via `MODEL_RETRY_*` env vars.

### 4. Model fallback (`ModelFallbackMiddleware`)

**Conditionally wired.** Only instantiated when `FALLBACK_MODEL` is set (alongside `FALLBACK_PROVIDER`). On the first transient failure, `request.model` is replaced with a pre-configured secondary `BaseChatModel` instance and the handler is retried. If the fallback also fails, the original model is restored before re-raising so subsequent middleware in the chain see the original config.

**Design choice:** On fallback success, `request.model` is left pointing to the fallback model for the remainder of the turn. This means subsequent model calls also use the fallback — preferable for stability (stick with the working model) rather than switching back to the primary that just failed.

### 5. Circuit breaker (`CircuitBreakerMiddleware`)

**Per-tool, per-thread CLOSED/OPEN/HALF_OPEN state machine.** Tracks consecutive failures per tool per thread. After `failure_threshold` (default 3) consecutive failures on an external tool, the circuit opens and subsequent calls return a `ToolMessage` without executing the handler. After `recovery_timeout` (default 30s), a single probe call is allowed (HALF_OPEN). If the probe succeeds the circuit closes; if it fails the circuit stays open for another timeout window.

**Prometheus counters** — 4 counters with `tool` label: `circuit_breaker_opens_total`, `circuit_breaker_blocks_total`, `circuit_breaker_probes_total`, `circuit_breaker_probe_successes_total`. Wired to existing Prometheus/Grafana stack with 2 dashboard panels.

### 6. Retry tool (`RetryToolMiddleware`)

**Exponential backoff on external tool calls.** Retries `_EXTERNAL_TOOLS` (currently `search_knowledge_base`, `search_codebase`, `send_response`, `fetch_issue`) with configurable max_attempts, initial_interval, backoff_factor, and optional jitter. Non-external tools (including `grade_response`, `send_response` as wiring) pass through without retry. All parameters env-configurable via `RETRY_*`.

### 7. Tool call limit (`ToolCallLimitMiddleware`)

**Hard cap per agent run.** Counts every tool invocation (excluding `grade_response` and `send_response`) and returns a capped `ToolMessage` after `max_calls` (default 25, env-configurable via `TOOL_CALL_LIMIT`). Counts reset per-run via `abefore_agent`/`aafter_agent` lifecycle hooks. This prevents runaway agents that spin on external I/O.

### 8. Structured logging with request context

**`contextvars`-based.** `request_id_var` and `caller_var` (Python 3.11+ `ContextVar`) propagate through `asyncio` natively — no thread-local issues. The FastAPI middleware sets them at request entry and clears them in a `finally` block. A `RequestLoggingFilter` attached to the core handler injects both vars onto every `logging.LogRecord` automatically, so every `logger.info()` call anywhere in `core.*` includes `request_id` and `caller` without any `extra=` argument.

**JSON output.** Lines are JSON-serialized for Loki/Grafana ingestion. Configurable via `LOG_FORMAT` env var (default `json`, can be `text` for local dev).

### 9. Prometheus metrics and Grafana monitoring

**Existing stack** (`prometheus_fastapi_instrumentator` for HTTP metrics, custom counters for circuit breaker events) exposed at `/metrics`. Grafana dashboard auto-provisioned with 13 panels including:

- HTTP request rate, latency p50/p95/p99, error rate
- Circuit breaker opens/blocks (per-tool timeseries)
- Circuit breaker probe success rate (stat with thresholds)
- System resource panels (CPU, memory, open FDs)

All panels use `{job='ossia'}` filter for consistency. The monitoring stack is started via `make monitor-up` (docker compose with monitoring profile).

### 10. Multi-tenant memory isolation

**Per-user namespaces via `contextvars`.** The `_make_memory_namespace()` function reads the authenticated `caller` hash from `caller_var` (a `ContextVar` set by the FastAPI auth middleware) and produces namespaces like `("ossia", "abc123def456")`. When no caller is available (tests, one-off scripts), falls back to `("ossia", "default")`. The `StoreBackend` namespace lambda calls `_make_memory_namespace()` at resolution time (not at agent-build time), ensuring each thread sees the correct caller's data.

**Supersedes ADR-0007** which documented the single-tenant `("ossia",)` namespace. The migration is backward-compatible: existing single-user deployments see `("ossia", "default")`, which behaves identically to the old `("ossia",)` namespace.

### 11. Subagent permission scoping

**Tool whitelists per subagent.** Seven subagents are split into two permission tiers:

- `_READ_ONLY_TOOLS`: `search_codebase`, `search_knowledge_base` — assigned to `code-researcher`, `bug-diagnostician`, `fix-proposer`, `ui-debugger`, `diagram-analyzer`, `visual-regression-reviewer`.
- `_TEST_TOOLS`: adds `run_tests` — assigned to `test-runner` only.

Tools like `create_pr`, `run_bugfix_pipeline`, `run_refactor_pipeline` are **never** exposed to any subagent. This prevents a compromised or hallucinating subagent from performing destructive operations. The `general-purpose` default subagent (auto-added by Deep Agents) still has access to the full tool set as a safety valve.

### 12. Env-configurable middleware parameters

All middleware parameters previously hardcoded are now wired through `Settings`. The complete set:

| Setting | Env var | Default | Range |
|---------|---------|---------|-------|
| `max_revision_loops` | `MAX_REVISION_LOOPS` | 3 | 1–10 |
| `tool_call_limit` | `TOOL_CALL_LIMIT` | 25 | 1–200 |
| `retry_max_attempts` | `RETRY_MAX_ATTEMPTS` | 3 | 1–10 |
| `retry_initial_interval` | `RETRY_INITIAL_INTERVAL` | 1.0 | 0.1–60 |
| `retry_backoff_factor` | `RETRY_BACKOFF_FACTOR` | 2.0 | 1.0–10.0 |
| `code_interpreter_timeout` | `CODE_INTERPRETER_TIMEOUT` | 5.0 | 0.5–60 |
| `code_interpreter_max_ptc_calls` | `CODE_INTERPRETER_MAX_PTC_CALLS` | 32 | 1–200 |
| `circuit_breaker_failure_threshold` | `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | 3 | 1–20 |
| `circuit_breaker_recovery_timeout` | `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | 30.0 | 1–300 |
| `model_retry_max_attempts` | `MODEL_RETRY_MAX_ATTEMPTS` | 2 | 1–10 |
| `model_retry_initial_interval` | `MODEL_RETRY_INITIAL_INTERVAL` | 0.5 | 0.1–30 |
| `model_retry_backoff_factor` | `MODEL_RETRY_BACKOFF_FACTOR` | 2.0 | 1.0–10.0 |
| `fallback_provider` | `FALLBACK_PROVIDER` | None | — |
| `fallback_model` | `FALLBACK_MODEL` | None | — |

Parameters use `pydantic.Field(ge=..., le=...)` bounds. Defaults match the previously hardcoded values. All documented in `.env.example`.

### 13. Audit harness expansion

The audit (`GET /v1/audit`, `scripts/audit_ossia.py`) now runs 7 sections in parallel:

| Section | Coverage |
|---------|----------|
| memory | BaseStore CRUD, Postgres unset DSN handling |
| process | RevisionLoopCap, RetryTool (transient, exhaustion, non-external skip) |
| fix-verifications | MCP graceful degradation, agent compilation, counter cleanup |
| multi-tenancy | Namespace resolution (default, per-user, isolation), store isolation |
| model | ModelRetry (RateLimit, exhaustion, auth skip), ModelFallback (switch on transient) |
| runtime | End-to-end agent run, streaming events, MCP tools |
| langsmith | Trace recording verification |

## Consequences

### Positive

- **Defense in depth.** Every failure mode has a dedicated layer: PII (prevention), model retry (transient provider failures), model fallback (provider outage), circuit breaker (service overload), tool retry (transient tool failures), revision cap (runaway grade loops), tool-call limit (runaway tool consumption).
- **Observable.** Every layer logs (with `request_id`/`caller`), circuit breaker events are metered to Prometheus, and the full stack is verified by the audit harness.
- **Isolated by default.** Memory is scoped per-caller, subagents are scoped to read-only tools, and the middleware stack enforces per-thread state isolation.
- **Tunable without code changes.** Every middleware parameter is env-configurable with validated bounds.
- **Audit-gated.** The audit harness provides a structured compliance report that can be run on every deployment to verify the stack is intact.

### Negative

- **Complexity.** 10 middleware layers (9 active in default config) compose in a specific order. Changing the order requires understanding the dependency graph documented in §1.
- **The fallback middleware is untested without a real API key.** The audit skips the fallback test when `OPENROUTER_API_KEY` is unset, which is always the case in CI. A future pass should add a mock-backed fallback test.
- **Per-user memory isolation depends on the FastAPI middleware.** If a non-HTTP entry point (CLI script, notebook, test) runs the agent without setting `caller_var`, all sessions share the `("ossia", "default")` namespace. This is acceptable for development but should be documented for operators.
- **No model retry/fallback metrics.** Unlike the circuit breaker, model retry and fallback events are not yet exported to Prometheus. A follow-up pass should add counters for `model_retries_total`, `model_fallbacks_total`, and `model_fallback_successes_total`.
- **Shell-exported env vars silently override .env.** When deploying via docker compose, shell-exported environment variables take precedence over the `.env` file and docker-compose.yml defaults. This was discovered during containerized testing: an `export POSTGRES_URL=postgresql://ossia:ossia@localhost:5432/ossia` in the parent shell caused the container to attempt connecting to `localhost` (the container's own loopback) instead of the `postgres` service, even though the `.env` file had been corrected. The precedence chain is: shell env > `.env` file > docker-compose.yml default (via `${VAR:-default}` syntax). Operators should `unset POSTGRES_URL` (or any env var they've exported) before `docker compose up` if they intend the `.env` file or compose defaults to apply. This applies to all env vars that have a docker-compose default: `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `LOG_FORMAT`, etc.
- **LangSmith audit section always fails in local deployments.** The audit's LangSmith section queries `https://api.smith.langchain.com/sessions` to verify tracing. Without a `LANGSMITH_API_KEY` in the deployment, this returns a 403 Forbidden and the section reports FAIL. This is expected behavior for local/CI deployments where LangSmith tracing is not configured. Operators should not treat the audit FAIL in the LangSmith section as a blocking deployment issue; the 6 functional sections (memory, process, fix-verifications, multi-tenancy, model, runtime) are the relevant pass/fail gates.

### Neutral / Future

- **Model call limit middleware** (`ModelCallLimitMiddleware`) does not exist yet. The tool-level limit (`ToolCallLimitMiddleware`) indirectly limits model calls (fewer tool loops = fewer LLM turns), but a direct model-call cap would prevent runaway chains within a single tool execution. Deferred until the model-call-per-turn pattern is empirically expensive.
- **Sandbox lifecycle audit** (thread-scoped vs assistant-scoped QuickJS sandbox) is not yet covered by the audit. The code interpreter uses `mode="thread"` but the audit does not verify sandbox isolation or lifecycle cleanup.
- **Per-subagent interrupt configuration** (`interrupt_on` per subagent) is not wired. All subagents inherit the main agent's interrupt config (currently `send_response` only). Deferred until a subagent needs its own approval gate.
- **Per-subagent model override** is not wired. All subagents use the main agent's model. The architecture supports it (each subagent builds its own `BaseChatModel`); defer until a subagent empirically needs a different model.

## Alternatives considered

1. **Two middleware stacks (tool + model) in separate files.** Kept them in `middleware.py` for discoverability. The file is now ~650 lines; if it grows beyond 800, split into `tool_middleware.py` and `model_middleware.py`.
2. **Middleware-level fallback (switch model inside `ModelFallbackMiddleware`) vs graph-level fallback (two `CompiledStateGraph`s).** The middleware approach is simpler (one graph, one checkpointer, one store) and matches the Deep Agents production guide pattern for `ModelFallbackMiddleware`. A graph-level fallback would duplicate checkpointer/store state and complicate lifecycle management. Deferred only if a full-provider outage (not just a transient failure) becomes a common pattern.
3. **Persistent event buffer (Postgres) vs in-memory (existing).** The `ThreadEventBuffer` is in-memory (ADR-0012). Keeping it in-memory for v1 avoids adding a Postgres dependency for the replay feature. If persistent replay becomes critical, the buffer can be swapped to a Postgres-backed append log.
4. **Global circuit breaker vs per-tool.** Per-tool isolation is more precise: one failing service doesn't degrade all other tools. The overhead of one `_BreakerEntry` per tool per thread is negligible (~100 bytes).

## Status

Accepted. Implemented across v1.8.0–v1.9.0 (consecutive production-readiness passes).

## References

- Feature specs: (gaps remain — see `specs/coverage.md`)
- `src/core/middleware.py` — middleware implementations
- `src/core/agent.py` — middleware wiring and namespace factory
- `src/core/config.py` — env-configurable settings
- `src/core/metrics.py` — Prometheus counters
- `src/core/request_context.py` — `contextvars`-based request context
- `src/core/logging_config.py` — JSON logging setup
- `src/core/audit.py` — audit harness
- `monitoring/grafana/dashboard.json` — dashboard panels
- `docs/adr/0007-agent-scoped-memory-and-episodic-recall.md` — superseded re multi-tenancy
- `docs/adr/0010-runtime-context-ossia-context.md` — runtime context foundation
- Deep Agents production guide: https://docs.langchain.com/oss/python/deepagents/going-to-production

## Amendment A (2026-07-07): Community middleware layers (10 → 13 layers)

Three community middleware layers are inserted into the stack after
the original 10, bringing the total to 13:

| # | Middleware | Purpose | Gating | Source |
|---|-----------|---------|--------|--------|
| 9 | Eager-tools | Dispatch tool calls concurrently as streaming blocks seal | `enable_eager_tools=true` (default) | `eager-tools` + `eager-tools-langgraph` |
| 12 | Compact | Context window compaction | `enable_compact=true` (piloted) | `compact-middleware` |
| 13 | Advisor | Proactive fast/slow model routing | `enable_advisor=true` (piloted) | `advisor-middleware` / `langchain-router` |

Additionally, NoPII vault-based tokenization is available at position 1
(replacing regex-based PII redaction when `enable_nopii=true`).

### Implementation

- All community middlewares wired in ``_compile_agent`` in
  ``core/agent.py``. Settings in ``core/config.py``.
- ``core/middleware_adapters.py`` (new) provides ``EagerToolAdapter``
  and ``_EAGER_DENY`` (side-effect tool deny list).
- Eager-tools placed after core middlewares so that retries and
  circuit breakers wrap the eager dispatch path.
- Compact and advisor placed close to the model (before caller context).

### Rationale

- **Eager-tools**: Overlaps tool execution with LLM generation for
  20-50% latency improvement on multi-tool turns. Zero risk for
  read-only tools (deny list excludes side-effect tools).
- **Compact**: Prevents context window overflow in long-running
  sessions with multi-subagent pipelines. Piloted because deep
  agent workloads rarely exhaust context at current model capacities.
- **Advisor**: Cost optimization for deployments with both cheap
  and expensive model access. Piloted because the fast/slow
  routing heuristics need calibration per workload.
