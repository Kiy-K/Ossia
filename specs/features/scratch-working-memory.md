# Feature: Hybrid memory (per-caller /scratch/ working memory)

- Status: implemented
- ADR: docs/adr/0007-agent-scoped-memory-and-episodic-recall.md
- Scope: memory

## What it does

Adds a third filesystem surface, ``/scratch/``, for *transient*
working state. The agent uses it for tool outputs, search results,
and other artifacts it wants to keep around for a few turns
without polluting the durable ``/memories/AGENTS.md``. The
namespace is always per-caller — there is no agent-scoped opt-in
for scratch, by design.

The route is the *hybrid Redis-for-hot/Postgres-for-cold* leg of
the architecture. When ``REDIS_URL`` is set, the same Redis store
backs ``/memories/`` and ``/scratch/`` (sub-millisecond reads,
TTL-friendly, the natural fit for ephemeral state). When only
``POSTGRES_URL`` is set, ``/scratch/`` is not mounted and the
agent's working state falls through to ``StateBackend``
(in-thread, ephemeral). Setting both URLs gives the explicit
hybrid; Redis wins for scratch.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Mounting | ``/scratch/`` in ``CompositeBackend`` when a scratch store is wired | Per-mounting policy / quota |
| Namespace | Per-caller via ``_make_scratch_namespace``; falls back to ``("ossia", "scratch")`` | Agent-scoped opt-in (deliberate) |
| Store wiring | Reuse the same Redis connection as ``/memories/``; no second store on Postgres-only | A second Postgres connection dedicated to scratch |
| HTTP surface | None — ``/scratch/`` is internal to the agent | Debug endpoint to read scratch (intentional; scratch is disposable) |
| TTL | None — the user-facing Redis backend's own ``EXPIRE`` policy applies | Application-level eviction |

## Endpoint impact

None. ``/scratch/`` is an *agent-internal* filesystem path surfaced
via the DeepAgents filesystem tools (``ls``, ``read_file``,
``write_file``, ``edit_file``, ``glob``, ``grep``). There is no
HTTP route for it; the existing ``GET /v1/memories/{path}`` and
``GET /v1/policies/{path}`` debug routes remain the only memory
inspection surface.

## Safety/Permissions

- Per-caller namespace prevents one user from reading another's
  scratch state.
- No write-deny permission is needed; scratch is meant to be
  writable by the agent.
- Scratch is *not* in the ``memory=[...]`` list passed to
  ``create_deep_agent``; the agent is not preloaded with any
  scratch content on boot.

## NFRs

- **Latency:** ``/scratch/`` reads on Redis are sub-millisecond
  (per the LangGraph Redis store contract); falls through to
  ``StateBackend`` (in-memory) on Redis-less deployments.
- **Durability:** scratch inherits the durability of whatever
  store backs it. On Redis, scratch survives a process restart but
  not a Redis flush. On ``StateBackend``, scratch dies with the
  thread.
- **Cross-thread:** scratch is per-caller but not per-thread; the
  agent can use it to share working state across a user's
  multiple threads. The agent must clean up scratch itself; the
  store does not TTL it (Redis-only deployments can set
  ``EXPIRE`` via the LangGraph store's TTL knobs if needed).

## Affected modules

- ``src/core/memory.py`` — adds ``SCRATCH_NAMESPACE`` constant
- ``src/core/agent.py`` — adds ``_make_scratch_namespace``,
  updates ``_make_backend`` to accept ``scratch_store`` and
  mount ``/scratch/`` conditionally; ``build_agent_async`` wires
  ``scratch_store = store`` when Redis is configured
- ``tests/test_memory.py`` — three new tests: per-caller
  namespace, route mounts, route omitted when no scratch store

## Testing notes

- Behavioral tests in ``test_memory.py`` cover namespace
  resolution and route wiring with an ``InMemoryStore`` as the
  scratch store.
- A live Redis is not required to validate the contract; the
  shape of the route is what matters. End-to-end validation
  against a real Redis happens in deployment.
- The ``/scratch/`` route is not exercised by the existing agent
  smoke tests (the agent does not write to scratch by default);
  add an end-to-end test if/when the agent is taught to use it.
