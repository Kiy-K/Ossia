# ADR-0016: DeepAgents v0.6 Adoption — Orchestration Collapse

**Status:** accepted.
**Date:** 2026-07-08.
**Supersedes:** ADR-0008 §"async subagent dispatch" (custom async dispatch/join replaced by `AsyncSubAgentMiddleware`).

## Context

Ossia originally ran a hand-rolled LangGraph orchestration layer (10 subagents,
custom sync/async dispatch, custom HITL via `send_response`/`/resume`) sitting
directly on raw LangGraph primitives. DeepAgents v0.6 (`create_deep_agent`)
provides the same category of scaffolding — planning, virtual filesystem,
subagent spawning via a `task` tool, summarization, HITL via `interrupt_on` —
as a pre-assembled, maintained middleware stack.

The decision is to **collapse onto DeepAgents as the orchestration layer**
and stop hand-maintaining equivalents. This ADR documents the choices made
during that migration.

## Decision

### 1. Sync subagents → `subagents=` (declarative)

All 7 sync subagents (`code-researcher`, `bug-diagnostician`, `fix-proposer`,
`test-runner`, `ui-debugger`, `diagram-analyzer`, `visual-regression-reviewer`,
plus the optional `web-reviewer`) are declared via DeepAgents' `subagents=`
parameter with per-role `system_prompt`, `tools`, and `model`. The main agent
spawns them via the built-in `task` tool. Each subagent receives a scoped tool
set (`_SUBAGENT_TOOL_MAP`) rather than the full core tool surface.

The `web-reviewer` subagent is only wired when the browser-use package and its
API key are available (`_resolve_web_reviewer_tools` returns `None` otherwise).
This follows the existing graceful-degradation pattern from ADR-0003.

### 2. Async subagents — classification

| Subagent | Bucket | Justification |
|----------|--------|---------------|
| `researcher` | **1** — genuinely concurrent / fan-out | Codebase research can fan out to parallel file searches across directories/patterns; the main agent benefits from continuing the conversation while background research runs. Keep as `AsyncSubAgent`. |
| `tester` | **2** — async only because I/O-bound | Single long-running test command with no internal concurrent branches; test results feed directly into the immediate fix-propose cycle. Collapse to sync declarative subagent via `subagents=`. |
| `auditor` | **2** — async only because I/O-bound | Sequential audit/index operations with no internal fan-out; audit results feed directly into the conversation flow. Collapse to sync declarative subagent via `subagents=`. |

The `researcher` remains wired via `AsyncSubAgentMiddleware` (opt-in, gated by
`Settings.enable_async_subagents`). The `tester` and `auditor` already exist as
sync declarative subagents in the `_DEV_CONCIERGE_SUBAGENTS` catalogue
(`test-runner` fills the tester role; the agent uses `run_audit_pipeline` for
audit work directly). Their standalone graph modules (`src/core/graphs/tester.py`,
`src/core/graphs/auditor.py`) and the `langgraph.json` entries remain for
LangGraph Platform deployments but are no longer wired into the main agent's
`AsyncSubAgentMiddleware`.

### 3. HITL → `interrupt_on` + `HumanInTheLoopMiddleware`

The custom `send_response`/`/resume` flow is replaced by DeepAgents' built-in
`interrupt_on` mechanism. Configuration:

```python
interrupt_on = {"send_response": True}
```

The `/v1/threads/{id}/resume` endpoint is preserved (clients depend on it),
but it now delegates to `agent.ainvoke(Command(resume={"decisions": [...]}), ...)`
— the standard LangGraph HITL resume pattern. No custom interrupt handler code.

The `send_response` tool itself is unchanged: it is a `@tool`-decorated function
that the agent calls when ready to deliver a response. The `interrupt_on` config
triggers DeepAgents' `HumanInTheLoopMiddleware` to pause before executing it,
requiring human approval.

### 4. Backend: `CompositeBackend` (StateBackend + StoreBackend routes)

Self-hosted, Postgres/Redis-backed. The chosen shape:

| Path | Backend | Storage | Purpose |
|------|---------|---------|---------|
| `/` (default) | `StateBackend` | In-process (ephemeral) | Per-thread scratch state; rides the LangGraph checkpointer lifecycle |
| `/memories/` | `StoreBackend` | Postgres or Redis (`BaseStore`) | Persistent agent memory (`AGENTS.md`); per-caller namespace by default, agent-scoped when `Settings.memory_scope == "agent"` |
| `/policies/` | `StoreBackend` | Postgres or Redis (`BaseStore`) | Shared read-only compliance/policy files; write-deny enforced by `FilesystemPermission` |
| `/scratch/` | `StoreBackend` | Redis only (when `REDIS_URL` set) | Hybrid working-memory surface; Redis for hot path (sub-ms reads, TTL-friendly) |

**Not used:** `ContextHubBackend` (LangSmith Hub-tied), any sandbox backend
(`LangSmithSandbox`, `DaytonaSandbox`, `E2BSandbox`, `ModalSandbox`,
`RunloopSandbox`, `VercelSandbox`). All assume a hosted provider that Ossia
explicitly does not depend on.

The `CompositeBackend` is constructed in `_make_backend()` with per-caller
namespace lambdas. The route map is centralized so a future route addition
is a one-line entry.

### 5. Memory: self-hosted Mem0 as tool layer

Mem0 integrates as a **tool layer**, not as a DeepAgents `Backend`. Rationale:

- DeepAgents' `Backend` protocol is a virtual filesystem (`get`/`put`/`list`/`delete` over paths).
- Mem0's API is semantic memory CRUD+search (`add`/`search`/`get_all`/`update`/`delete` over natural-language memories with vector embeddings).
- No official adapter makes one satisfy the other's interface, and building one would discard the semantic search that is the entire point of using Mem0.

Instead, wrap Mem0's `Memory`/`AsyncMemory` class in `@tool`-decorated functions
and pass them via `tools=` to `create_deep_agent`:

```python
from mem0 import AsyncMemory
from langchain_core.tools import tool

memory = AsyncMemory.from_config({
    "vector_store": {
        "provider": "pgvector",
        "config": {
            "host": os.environ["POSTGRES_HOST"],
            "port": 5432,
            "dbname": os.environ["POSTGRES_DB"],
            "user": os.environ["POSTGRES_USER"],
            "password": os.environ["POSTGRES_PASSWORD"],
        },
    },
    "llm": {"provider": "openrouter", "config": {"model": settings.model}},
    "embedder": {"provider": "ollama", "config": {"model": settings.embedding_model}},
    "graph_store": {"provider": "excluded"},  # no Neo4j
})

@tool
async def search_memory(query: str, user_id: str) -> str:
    """Search stored long-term memory for relevant facts about this user/thread."""
    results = await memory.search(query, user_id=user_id)
    return json.dumps(results)

@tool
async def add_memory(content: str, user_id: str) -> str:
    """Store a new fact in long-term memory."""
    await memory.add(content, user_id=user_id)
    return "Memory stored."
```

Storage configuration:

- **Vector store:** pgvector on the existing Postgres instance. Same server,
  dedicated `mem0` schema for migration/backup isolation. No separate vector
  database (Qdrant, Chroma, etc.).
- **Graph memory: excluded for v1.** No Neo4j. Mem0 works as a pure
  vector-memory system without graph extraction; revisit only if
  relationship-aware retrieval becomes necessary.
- **Redis stays out of Mem0's storage path.** Redis is the cache layer only
  (`RedisSemanticCache`, `ToolResultCacheMiddleware`) — it does not back Mem0's
  vector store or history.

| Store | Responsibility |
|-------|---------------|
| Postgres | LangGraph checkpointer, thread metadata, HITL interrupt state, Mem0 vector store (pgvector) |
| Redis | Semantic cache, tool-result cache — caching only, no durable memory |

- **LLM + embedder: not OpenAI defaults.** The LLM for fact extraction uses
  the same provider as the main agent (OpenRouter by default, configurable via
  `Settings.provider`). The embedder uses the local Ollama server already
  configured for the vector index (`Settings.embedding_model`, default
  `embeddinggemma`). Both point at infrastructure already in use — no new
  external dependencies.

## Consequences

- **Deleted code.** The hand-rolled subagent dispatch/join logic, the custom
  `send_response` interrupt handler, and any now-unused module-level singletons
  for subagent orchestration are removed. The `test-runner` sync subagent fills
  the tester role; `run_audit_pipeline` covers audit work.
- **`langgraph.json` retained.** The 4 graph modules (`supervisor`, `researcher`,
  `tester`, `auditor`) remain registered for LangGraph Platform deployments.
  Only `supervisor` and `researcher` are wired into the main agent's runtime;
  `tester` and `auditor` exist as deployment targets for future async subagent
  expansion.
- **No new infrastructure.** pgvector on the existing Postgres, no Neo4j, no
  new Redis responsibilities, no new cloud dependencies.
- **No HTTP contract changes.** All `/v1/*` routes are unchanged. The
  `/v1/threads/{id}/resume` endpoint retains the same schema. `test_openapi_drift.py`
  stays green.
- **Streaming/typed-projection overhaul is out of scope.** This migration does
  not touch the v3 streaming protocol or the assistant-ui integration. Those are
  separate workstreams tracked independently.

## Non-goals (explicitly excluded)

1. Managed Deep Agents / LangSmith Deployment integration — self-hosted only.
2. Streaming/typed-projection overhaul — separate workstream.
3. Model or prompt changes unrelated to orchestration mechanics.
4. Nebius Serverless Challenge RL pipeline work.
5. Mem0 behind `Backend`/`BaseStore` protocol — interfaces are incompatible.
6. Neo4j or separate vector database — pgvector on existing Postgres is sufficient for v1.
7. OpenAI defaults for Mem0's LLM/embedder — both point at self-hosted or already-configured infrastructure.
