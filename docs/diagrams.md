# Architecture Diagrams

This page indexes all Mermaid architecture diagrams embedded in the Architecture Decision Records (ADRs). Each entry links to the source ADR for the full context.

---

## Diagram Index

| # | Diagram | ADR | Type | Description |
|---|---------|-----|------|-------------|
| 1 | [Subagent Routing](#1-subagent-routing) | [ADR-0008](adr/0008-subagent-design-and-routing.md) | Flowchart | Coordinator routes tasks to 4 custom subagents + fallback, with permission tiers |
| 2 | [Middleware Stack](#2-middleware-stack) | [ADR-0013](adr/0013-production-readiness-middleware-stack.md) | Flowchart | 10-layer middleware stack grouped into 5 logical concerns |
| 3 | [Request Flow Sequence](#3-request-flow-sequence) | [ADR-0013](adr/0013-production-readiness-middleware-stack.md) | Sequence | Full request lifecycle: auth → middleware → model → tools → response |
| 4 | [Event Stream Pipeline](#4-event-stream-pipeline) | [ADR-0006](adr/0006-streaming-v3-protocol.md) | Flowchart | DeepAgent v3 stream → normalizer → OssiaEvent → SSE/buffer/JSON |
| 5 | [Deployment Topology](#5-deployment-topology) | [ADR-0014](adr/0014-standalone-deployment.md) | Flowchart | Service topology: Caddy → ossia → Postgres + optional monitoring |

---

### 1. Subagent Routing

**Source:** [ADR-0008 § Context](adr/0008-subagent-design-and-routing.md)

Shows how the main coordinator agent delegates tasks to specialized subagents based on intent:

- **Coordinator Agent** — routes via intent-matching descriptions
- **4 Custom Subagents** — `code-researcher`, `bug-diagnostician`, `fix-proposer`, `test-runner`
- **Default Fallback** — `general-purpose` auto-added by Deep Agents
- **Permission Tiers** — read-only tools for most subagents, test tools for `test-runner`, full tool set for `general-purpose`

```
flowchart TB
    Coordinator -->|"context quarantine"| code-researcher
    Coordinator -->|"bug diagnosis"| bug-diagnostician
    Coordinator -->|"fix proposal"| fix-proposer
    Coordinator -->|"run tests"| test-runner
    Coordinator -->|"unmatched intent"| general-purpose

    code-researcher -.-> Read-Only Tools
    bug-diagnostician -.-> Read-Only Tools
    fix-proposer -.-> Read-Only Tools
    test-runner -.-> Test Tools
    general-purpose -.-> Full Tool Set
```

**Key design decisions:**
- Subagents inherit the main agent's model (no per-subagent model override yet)
- Skill isolation: only `general-purpose` inherits main-agent skills
- Per-subagent `interrupt_on` is not wired — all subagents inherit the main config

---

### 2. Middleware Stack

**Source:** [ADR-0013 § 1. Middleware stack — composition order](adr/0013-production-readiness-middleware-stack.md)

Shows the 10-layer middleware stack ordered outermost to innermost, grouped into 5 logical concerns:

| Layer | Middleware | Purpose |
|-------|-----------|---------|
| 🔒 Security & Privacy | `PIIRedactionMiddleware` | Strip secrets from tool inputs |
| 🤖 Model Reliability | `ModelRetryMiddleware` | Retry transient LLM failures |
| | `ModelFallbackMiddleware` | Switch provider on outage |
| 🛡️ Service Resilience | `CircuitBreakerMiddleware` | Fail-fast on overloaded services |
| | `RetryToolMiddleware` | Retry tool calls with backoff |
| ⚖️ Governance & Limits | `RevisionLoopCapMiddleware` | Cap response revision loops |
| | `ToolCallLimitMiddleware` | Cap total tool calls per run |
| ⚡ Runtime & Context | `CodeInterpreterMiddleware` | Sandboxed QuickJS eval |
| | `AsyncSubAgentMiddleware` | Long-running background tasks |
| | `make_caller_context_middleware` | Inject caller identity into prompts |

**Ordering rationale:**
- PII first — strip secrets before any downstream middleware can observe them
- Model retry/fallback before tool middleware — handle provider failures without consuming tool-call budget
- Circuit breaker before retry — fail fast on dead services instead of exhausting retries
- Retry before caps — exclude `grade_response`/`send_response` from tool-call counting
- Revision cap before tool-call limit — compose sequentially without double-counting
- Caller context last — closest to the model call via `@dynamic_prompt`

---

### 3. Request Flow Sequence

**Source:** [ADR-0013 § 1. Middleware stack — composition order](adr/0013-production-readiness-middleware-stack.md)

Traces a complete HTTP request through the entire stack:

**Phases:**

1. **Auth & Setup** — FastAPI verifies `X-API-Key` (Argon2), sets `request_id` + `caller` context, checks rate limits
2. **Inbound (outermost → innermost)** — Request descends through PII → Model Layer → LLM
3. **Tool Call Path** (conditional) — If the LLM returns `tool_calls`, the request continues deeper through Resilience → Governance → Runtime → External Tools, then results bubble back up
4. **Grade Response** — Model calls the LLM again with tool results for the final answer
5. **Outbound (innermost → outermost)** — Final response bubbles back up through all layers
6. **Cleanup** — Context vars cleared, Prometheus metrics emitted

**Stack depth** is visible through activation nesting: each middleware layer activates the next, and the LLM call sits at the center of the inbound path. The deepest nesting occurs when tool calls trigger the full resilience → governance → runtime chain.

```
sequenceDiagram
    Client->>FastAPI: POST /v1/chat
    FastAPI->>PII: Forward (after auth)
    PII->>Model Layer: Forward (PII stripped)
    Model Layer->>LLM: ainvoke(messages)
    LLM-->>Model Layer: tool_calls
    Model Layer->>Resilience: Forward tool calls
    Resilience->>Governance: Forward (circuit OK)
    Governance->>Runtime: Forward (caps OK)
    Runtime->>External Tools: Execute
    External Tools-->>Runtime: Results
    Runtime-->>Governance: Results
    Governance-->>Resilience: Results
    Resilience-->>Model Layer: Results
    Model Layer->>LLM: grade_response
    LLM-->>Model Layer: Final response
    Model Layer-->>PII: Response
    PII-->>FastAPI: Response
    FastAPI-->>Client: StreamingResponse
```

---

### 4. Event Stream Pipeline

**Source:** [ADR-0006 § Context](adr/0006-streaming-v3-protocol.md)

Shows the end-to-end event stream pipeline from the raw DeepAgent v3 stream to serialized output:

- **DeepAgent v3 Stream** — 4 async projections: `stream.messages`, `stream.tool_calls`, `stream.subagents`, `stream.values`
- **EventNormalizer** — 5 concurrent relays (`_relay_messages`, `_relay_tool_calls`, `_relay_subagents`, `_relay_values`, `_relay_artifacts`) each putting normalized events into a shared `asyncio.Queue`
- **Main loop** (`normalize()`) — consumes from the queue, yields `OssiaEvent` objects, emits final `interrupt` + `complete` events
- **Output** — 3 serialization/storage paths: SSE (`text/event-stream`), ThreadEventBuffer (in-memory replay), and standalone JSON

**Event categories emitted:**
- `message_*` — coordinator text tokens (started, delta, completed)
- `tool_*` — tool call lifecycle (started, progress, completed, failed)
- `subagent_*` — subagent lifecycle (spawned, completed, failed, interrupted)
- `pipeline_*` — pipeline orchestration events (auto-detected from pipeline tool names)
- `async_task_*` — background async subagent task events
- `artifact_*` — multimodal artifact lifecycle (received, processed, analysis)
- `interrupt / complete / error` — system-level events

**Key design:** The normalizer detects pipeline orchestrator tools (`run_bugfix_pipeline`, etc.) and automatically annotates their subagent steps with `pipeline_step_started/completed/failed` events — no explicit pipeline configuration needed.

```
flowchart TB
    DeepAgent v3 Stream --> EventNormalizer
    EventNormalizer -->|asyncio.Queue| normalize()
    normalize() --> OssiaEvent stream
    OssiaEvent stream --> serialize_sse() --> SSE
    OssiaEvent stream --> ThreadEventBuffer --> GET /events
    OssiaEvent stream --> serialize_json() --> JSON
```

---

### 5. Deployment Topology

**Source:** [ADR-0014 § 1. Service architecture](adr/0014-standalone-deployment.md)

Shows the production service topology and data flow:

| Component | Role | Required |
|-----------|------|----------|
| **Caddy** | TLS termination, reverse proxy, auto HTTPS via Let's Encrypt | Optional |
| **ossia** | FastAPI server serving `/v1/*` routes | Required |
| **Postgres** | Checkpointing, memory store, HITL state persistence | Optional |
| **Prometheus** | Metrics collection and storage | Optional (monitoring profile) |
| **Loki** | Log aggregation | Optional (monitoring profile) |
| **Grafana** | Dashboards and visualization | Optional (monitoring profile) |
| **LangSmith** | Trace recording and debugging | Optional |

**Data flow:**

```
Internet/Client --HTTPS :443--> Caddy --HTTP :8000--> ossia --persistence--> Postgres
                                                       ossia -.-> Prometheus (metrics)
                                                       ossia -.-> Loki (logs)
                                                       ossia -.-> LangSmith (traces)
                                                       Prometheus & Loki --> Grafana
```

**Key differences from LangSmith standalone server guide:**
- Custom FastAPI server (`core.api:app`) instead of `langgraph build` image
- Custom `/v1/*` API surface instead of generic `/runs` API
- No Redis required — no pub-sub dependency
- No LangGraph Cloud license needed
- Custom auth via `OSSIA_API_KEY` (Argon2 caller-id derivation)
- Postgres optional — agent works with in-memory store

---

## Quick Reference

| Want to understand... | Look at |
|-----------------------|---------|
| How the agent delegates to subagents | [Diagram 1](#1-subagent-routing) |
| What each middleware does and why they're ordered this way | [Diagram 2](#2-middleware-stack) |
| How a request flows through every layer | [Diagram 3](#3-request-flow-sequence) |
| How services connect in production | [Diagram 4](#4-deployment-topology) |
| How to deploy the stack | [ADR-0014 § 5](adr/0014-standalone-deployment.md#5-production-deployment-commands) |
| How to tune middleware parameters | [ADR-0013 § 12](adr/0013-production-readiness-middleware-stack.md#12-env-configurable-middleware-parameters) |
| Audit coverage | [ADR-0013 § 13](adr/0013-production-readiness-middleware-stack.md#13-audit-harness-expansion) |
