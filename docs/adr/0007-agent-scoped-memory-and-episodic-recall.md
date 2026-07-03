# ADR-0007: Memory surfaces (semantic file store, episodic recall, semantic search)

**Status:** accepted.
**Date:** 2026-06-22. Updated 2026-07-02 (per-caller default, semantic_search
tool, hybrid ``/scratch/`` route).
**Supersedes:** ADR-0002 §"Cross-session memory" only for the long-term
file side. ADR-0002's Postgres requirement for HITL still stands.

## Context

Ossia needs three memory surfaces per the Deep Agents memory docs:

1. **Long-term / semantic memory** — facts and preferences stored as
   files in the LangGraph store. The agent reads them on startup and
   can rewrite them via `edit_file`. Survives across threads and
   restarts (with a real store).
2. **Episodic memory** — records of past experiences: what happened,
   in what order, and what the outcome was. Preserves the full
   conversational context so the agent can recall *how* a problem
   was solved, not just *what* it learned.
3. **Semantic search** (v0.10+) — vector similarity over the memory
   store so the agent can find "what I learned about X" without
   knowing the thread_id. Requires the store to be a Redis instance
   with a RediSearch index (see `Settings.enable_vector_index`).

Prior state: the `CompositeBackend(default=StateBackend(),
routes={"/memories/": StoreBackend()})` was wired in `_make_backend`
but the `StoreBackend` had no `store=` injected and no `namespace=`,
so writes would have been ambiguous (default per-thread namespace).
`get_store` returned a Postgres store but the agent never actually
used it. There was no episodic surface — the agent had no way to
recall past conversations.

## Decision

### Long-term memory — per-caller by default, agent-scoped opt-in

`/memories/` is backed by a `StoreBackend` namespaced via
`_make_memory_namespace(base)`. The default behavior is **per-caller**
isolation: the caller's Argon2id hash is appended to the base
namespace, so two authenticated users see disjoint memory. The base
namespace is `AGENT_NAMESPACE = ("ossia", "default")` — used as a
fallback when no caller context is available (tests, one-off
scripts).

Set `Settings.memory_scope = "agent"` to switch to **agent-scoped**
memory: a single namespace shared across every caller, matching the
DeepAgents "agent-scoped memory" pattern. Use this when the agent
should accumulate shared knowledge (identity, conventions, "things
I've learned") visible to all users. The trade-off is documented in
the *Consequences* section below.

The agent is built with `memory=["/memories/AGENTS.md"]` so the seed
loads into the system prompt at startup. The seed is a sensible
"identity" file (Ossia's self-description, response style, and a
"Things I've learned" section the agent can update with
`edit_file`). `seed_memory` writes it once on first boot and is
idempotent — agent-written updates are never overwritten by re-seeds.
A `redis_lock("seed_memory", ...)` collapses the concurrent first-boot
race in the rare case two processes boot at the same time.

### Read-only policies — `/policies/` route

Application code seeds policy files (compliance, runbook) into
`POLICY_NAMESPACE = ("ossia", "policies")` at startup via
`seed_policy`. The `/policies/` route is mounted on the same store
but protected by `_POLICY_DENY_WRITE = FilesystemPermission(
operations=["write"], paths=["/policies/"], mode="deny")` so the
agent can `read_file` but cannot `edit_file` or `write_file` a
policy. Only `seed_policy` (app code) populates them.

### Working memory — `/scratch/` route (hybrid)

The third filesystem surface is for *transient* working state: the
agent's last tool output, in-flight search results, anything that
should be readable across the next few turns but does not belong in
the durable `/memories/AGENTS.md`. Mounted on
`SCRATCH_NAMESPACE = ("ossia", "scratch")` with per-caller
namespacing via `_make_scratch_namespace` (always per-caller; the
`memory_scope=agent` opt-in does NOT apply to scratch).

The route is the *hybrid Redis-for-hot/Postgres-for-cold* leg of the
architecture: when `REDIS_URL` is set, the same Redis store backs
both `/memories/` and `/scratch/` (sub-millisecond reads, TTL-able,
the natural fit for ephemeral working state). When only
`POSTGRES_URL` is set, the `/scratch/` route is not mounted; the
agent's working state falls through to `StateBackend` (in-thread,
truly ephemeral). To get the hybrid explicitly, set both URLs;
Redis wins for scratch on the dual-store setup.

### Episodic recall — per-thread tool

`recall_thread_turns(thread_id, limit)` is a LangChain tool that
returns the most recent messages of a specific thread from the
checkpointer. Per-thread is the only stable contract on a bare
`BaseCheckpointSaver.alist({...})`; cross-thread enumeration requires
the LangGraph SDK's `client.threads.search(metadata=...)` and is
out of scope for v1.

### Semantic search — caller-scoped vector lookup

`semantic_recall(query, top_k=5)` is a LangChain tool that uses the
store's RediSearch vector index (Ollama embedder, configured via
`Settings.embedding_model` / `embedding_dim`) to find similar items
the agent wrote to memory across threads. It searches
`("ossia", caller)` so one user's queries cannot surface another
user's content — defense in depth on top of the store's own
namespace isolation. Returns `None` from the factory (tool not
wired) for non-Redis stores or when `Settings.enable_vector_index
= False`.

## Consequences

- **Pro:** the agent has a stable, persistent identity from first
  boot. The seed is readable to humans (good for debugging).
- **Pro:** per-caller is the default — one user's `edit_file` to
  their `/memories/AGENTS.md` does not bleed into the next user's
  view. The caller hash is also used to derive the thread_id prefix,
  so a cross-caller `recall_thread_turns` returns empty.
- **Pro:** the agent can recall previous turns of a thread via the
  episodic tool without the model having to remember anything
  itself.
- **Pro:** in tests and ephemeral setups with no Postgres, the
  `InMemoryStore` and `InMemorySaver` give a working end-to-end loop.
- **Pro (with `memory_scope=agent`):** shared identity, shared
  "things I've learned" across the org.
- **Con (with `memory_scope=agent`):** every user shares the same
  `AGENTS.md`. If a user runs `edit_file` maliciously, the change is
  visible to the next user. Mitigation: leave the default at
  `user` scope, or front the API with a policy tool that scrubs
  `edit_file` requests against the `Things I've learned` section.
- **Con:** the episodic tool is per-thread only. Cross-thread
  "what did I tell user X last week?" requires the LangGraph SDK or
  a custom metadata-aware checkpointer index; out of scope for v1.
  Use the semantic_recall tool for cross-thread discovery.
- **Con:** the `InMemorySaver` test path needs a real graph run to
  populate the blob store; synthetic `aput` of `HumanMessage` objects
  bypasses the blob serialization and returns an empty `messages`
  channel on read.
- **Con:** semantic_recall is gated on Redis + RediSearch. Postgres
  and in-memory stores return `None` from the factory; the agent
  simply doesn't have the tool in that mode.
- **Con (hybrid tradeoff):** on a Redis-only deployment, scratch and
  memory share the store — a Redis outage takes down both. The
  hybrid is a single point of failure by construction. Mitigation:
  set `POSTGRES_URL` to persist memory across a Redis restart and
  treat Redis as best-effort.

## Alternatives considered

1. **Always agent-scoped memory** (the original v1 decision). Moved
   to opt-in via `Settings.memory_scope="agent"` once multi-tenant
   auth (ADR-0009) landed. Per-caller is the safer default for a
   multi-tenant HTTP API.
2. **User-scoped episodic search** (the docs' `search_past_conversations`
   example with `user_id`). Replaced by `semantic_recall` —
   cross-thread discovery by meaning, not by metadata filter.
3. **Build a custom cross-thread checkpointer index on the store**
   (mirror each turn to a per-thread key in the store). Possible,
   but the checkpointer already does this — exposing the SDK's
   `client.threads.search` is a smaller change. Not built in v1.
4. **Skip episodic memory entirely and rely on file-based memory.**
   Defeats the "recall *how* I solved this" use case the docs call
   out as the value of episodic.
