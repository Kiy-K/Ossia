# ADR-0007: Agent-scoped memory (semantic) + episodic recall (per-thread)

**Status:** accepted.
**Date:** 2026-06-22.
**Supersedes:** ADR-0002 §"Cross-session memory" only for the long-term
file side. ADR-0002's Postgres requirement for HITL still stands.

## Context

Ossia needs two flavors of memory per the Deep Agents memory docs:

1. **Long-term / semantic memory** — facts and preferences stored as
   files in the LangGraph store. The agent reads them on startup and
   can rewrite them via `edit_file`. Survives across threads and
   restarts (with a real store).
2. **Episodic memory** — records of past experiences: what happened,
   in what order, and what the outcome was. Preserves the full
   conversational context so the agent can recall *how* a problem
   was solved, not just *what* it learned.

Prior state: the `CompositeBackend(default=StateBackend(),
routes={"/memories/": StoreBackend()})` was wired in `_make_backend`
but the `StoreBackend` had no `store=` injected and no `namespace=`,
so writes would have been ambiguous (default per-thread namespace).
`get_store` returned a Postgres store but the agent never actually
used it. There was no episodic surface — the agent had no way to
recall past conversations.

## Decision

**Long-term memory** (semantic) is wired as agent-scoped per the
docs: a single namespace `("ossia",)` shared across every
conversation. The filesystem under `/memories/` is backed by a
`StoreBackend(store=..., namespace=lambda rt: ("ossia",))` and the
agent is built with `memory=["/memories/AGENTS.md"]` so the seed
loads into the system prompt at startup. The seed is a sensible
"identity" file (Ossia's self-description, response style, and a
"Things I've learned" section the agent can update with
`edit_file`). It is seeded once on first boot and is never
overwritten by re-seeds.

**Episodic memory** (per-thread recall) is wrapped as a LangChain
tool, `recall_thread_turns(thread_id, limit)`, that returns the
most recent messages of a specific thread from the checkpointer.
Per-thread is the only stable contract on a bare
`BaseCheckpointSaver.alist({...})`; cross-thread enumeration requires
the LangGraph SDK's `client.threads.search(metadata=...)` and is
out of scope for v1.

**No per-user scoping** in this pass. The user explicitly excluded
user-scoped memory: all conversations share the same `("ossia",)`
namespace. The FastAPI layer still prefixes `thread_id` with the
caller hash so the *checkpointer* isolates threads per-caller, but
the *memory file* is shared.

## Consequences

- **Pro:** the agent has a stable, persistent identity from first
  boot. The seed is readable to humans (good for debugging).
- **Pro:** the agent can recall previous turns of a thread via the
  episodic tool without the model having to remember anything
  itself.
- **Pro:** in tests and ephemeral setups with no Postgres, the
  `InMemoryStore` and `InMemorySaver` give a working end-to-end loop.
- **Con:** agent-scoped memory means every user shares the same
  `AGENTS.md`. If a user runs `edit_file` maliciously, the change is
  visible to the next user. Per the docs, this is a known caveat of
  agent-scoped memory; mitigation is to switch to user-scoped
  (`namespace=lambda rt: (rt.server_info.user.identity,)`) when
  per-user isolation is needed.
- **Con:** the episodic tool is per-thread only. Cross-thread
  "what did I tell user X last week?" requires the LangGraph SDK or
  a custom metadata-aware checkpointer index; out of scope for v1.
- **Con:** the `InMemorySaver` test path needs a real graph run to
  populate the blob store; synthetic `aput` of `HumanMessage` objects
  bypasses the blob serialization and returns an empty `messages`
  channel on read.

## Alternatives considered

1. **Per-user memory** (the docs' other pattern). The user asked for
   agent-scoped only; revisit if a per-user requirement lands.
2. **User-scoped episodic search** (the docs' `search_past_conversations`
   example with `user_id`). Same reason: out of scope.
3. **Build a custom cross-thread checkpointer index on the store**
   (mirror each turn to a per-thread key in the store). Possible,
   but the checkpointer already does this — exposing the SDK's
   `client.threads.search` is a smaller change.
4. **Skip episodic memory entirely and rely on file-based memory.**
   Defeats the "recall *how* I solved this" use case the docs call
   out as the value of episodic.
