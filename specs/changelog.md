# Spec changelog

Human-readable record of breaking and notable non-breaking changes to the
Ossia HTTP contract. The machine-readable record is the git history of
`openapi.checked.json`.

## v1.5.0 — 2026-06-22 — runtime context propagation (OssiaContext)

**Non-breaking** for the HTTP contract. No routes changed; the spec
schema and pinned `openapi.checked.json` are unchanged. The agent
runtime gains a per-invoke context dataclass that propagates to all
subagents and is readable from any tool via the deepagent
``ToolRuntime``.

- **New module** `src/ossia/context.py` exports
  :class:`OssiaContext`, a frozen dataclass with three fields:
  ``caller`` (X-API-Key hash, required), ``request_id`` (UUID for
  tracing, optional), ``provider`` (model provider, defaults to
  ``"openrouter"``).
- **Agent wiring**: ``create_deep_agent(..., context_schema=
  OssiaContext)`` so any tool that wants the caller's identity
  can read it from ``runtime.context.caller`` (per the Deep Agents
  "Context engineering" doc).
- **FastAPI plumbing**: the ``/v1/chat`` and ``/v1/chat/stream``
  handlers now construct an ``OssiaContext`` from the validated
  API key and the per-request id, then pass it as ``context=`` to
  ``agent.ainvoke`` / ``agent.astream_events``.
- **Demo**: ``grade_response`` reads
  ``runtime.context.caller`` and logs it. The function remains
  callable without a runtime (the parameter is typed as ``Any``
  and is not in the Pydantic schema) so existing tests and
  one-off scripts work unchanged.

See `docs/adr/0010-runtime-context-ossia-context.md` for the
full decision record, including the explicit deferral of a
dynamic-prompt middleware that would surface the caller in the
system prompt itself (future ADR; v1 only exposes the context to
tools).

## v1.4.0 — 2026-06-22 — Tavily-backed web tools + Nebius adapter removed

**Non-breaking** for the HTTP contract. No routes changed; the spec
schema and pinned `openapi.checked.json` are unchanged. The agent
runtime gains three new tools and drops the unused Nebius adapter.

- **New tools** in `ossia.tools`:
  - `internet_search(query, max_results, topic)` — Tavily-backed web
    search with structured results and a synthesized `answer`.
    Falls back to DuckDuckGo when `TAVILY_API_KEY` is unset
    (same path used by `search_knowledge_base`).
  - `fetch_url(url, question=None)` — Tavily-backed URL extraction;
    with `question` set, returns a grounded one-shot answer plus
    the page content (capped at 4000 chars). **Fallback when
    Tavily is missing**: a direct `httpx` + BeautifulSoup fetch
    (canonical pattern from the Deep Agents deep-research doc,
    adapted to use `bs4` instead of `markdownify` to avoid a new
    dependency). With `question` set, the fallback runs a DDG
    search and fetches the top hit. The `backend` field is
    `"duckduckgo"` in either case.
  - `qna_search(query, topic)` — Tavily-backed one-shot Q&A
    ("what is X?" pattern). **Fallback when Tavily is missing**:
    a DDG web search whose top snippets are synthesized into an
    answer. The `backend` field is `"duckduckgo"`.
- **Config**: `tavily_api_key: str | None` on `Settings`, with
  `validation_alias=AliasChoices("TAVILY_API_KEY",
  "OSSIA_TAVILY_API_KEY")`. The user's `.env` already has
  `TAVILY_API_KEY`; the alias `OSSIA_TAVILY_API_KEY` is for
  deployment-time configuration.
- **Nebius removed**: `ossia.adapters.nebius` was deleted;
  `create_chat_model(Provider.NEBIUS)` now raises
  `NotImplementedError` with a clear message directing callers
  to `Provider.OPENROUTER` (or another OpenAI-compatible
  provider) with a Nebius-routed model id. The `NEBIUS` enum
  value is kept for backward compatibility (no import-time
  crash from old `.env` values).
- **New dependency**: `tavily-python>=0.7.0` in `pyproject.toml`.

See `docs/adr/0009-tool-surface-and-tavily.md` for the full
decision record. The "fails loudly when Tavily is missing"
language in the v1.4.0 candidate was relaxed after a follow-up
review: per the Deep Agents deep-research doc, the canonical
fallback for URL fetch is a direct `httpx` + text extraction,
and the canonical fallback for one-shot Q&A is a web search
whose snippets are synthesized. Both fallbacks work without
Tavily, are clearly tagged `backend="duckduckgo"`, and the
`answer` field for `fetch_url`'s fallback is empty (DDG has no
Q&A primitive; the model can read the page content instead).

## v1.3.0 — 2026-06-22 — subagent descriptions and system prompts tightened

**Non-breaking** for the HTTP contract. No routes changed; the spec
schema and pinned `openapi.checked.json` are unchanged. The agent
runtime's subagent routing is now more precise.

- The four custom subagents (`code-researcher`, `bug-diagnostician`,
  `fix-proposer`, `test-runner`) now have **action-oriented
  descriptions** that start with "Delegates here when ..." so the
  coordinator's routing is unambiguous.
- Each subagent's `system_prompt` pins a **role, expected workflow,
  output format, and a 200-250 word cap** on the response. The
  coordinator's context stays lean and the outputs are parseable.
- The default Deep Agents `general-purpose` subagent is kept
  (inherits main-agent skills and model). It serves as a fallback
  for any question the four custom subagents do not cover.

See `docs/adr/0008-subagent-design-and-routing.md` for the full
decision record and the rationale against per-subagent model
overrides / structured output / disabling the default.

## v1.2.0 — 2026-06-22 — agent-scoped memory + episodic recall

**Non-breaking** for the HTTP contract. No routes changed; the spec
schema and pinned `openapi.checked.json` are unchanged. The agent
runtime gains two new memory surfaces.

- **Long-term memory** (semantic) is now wired. The agent reads
  `/memories/AGENTS.md` on startup; the file is seeded from
  `ossia.memory.initial_agents_memory` on first boot. Stored in the
  `("ossia",)` namespace of the LangGraph store (Postgres in
  production, in-memory in dev/tests). The seed is idempotent and
  never overwrites agent-written updates.
- **Episodic memory** (per-thread recall) is now a tool the agent
  can call: `recall_thread_turns(thread_id, limit)`. Returns the
  most recent messages of a specific thread from the checkpointer.
  Per-thread is the supported primitive on a bare
  `BaseCheckpointSaver`; cross-thread search requires the LangGraph
  SDK and is not yet wired.
- **Namespace policy**: agent-scoped only (per the user's
  instruction). The `("ossia",)` namespace is shared across every
  caller; the FastAPI layer still scopes *checkpointer thread ids*
  per-caller, but the memory *file* is shared.

See `docs/adr/0007-agent-scoped-memory-and-episodic-recall.md` for
the full decision record.

## v1.1.0 — 2026-06-22 — streaming switches to the v3 protocol

**Breaking** for clients of `POST /v1/chat/stream`.

- The streaming endpoint is now built on
  `agent.astream_events(input, config, version="v3")` instead of v2.
  The wire shape changes from flat `{event, name, data}` v2 event
  dicts to a discriminated-union envelope: each SSE event's `event:`
  field is one of `message`, `tool_call`, `subagent`, `value`,
  `interrupt`, `complete`, `protocol`, and the `data:` payload is a
  per-kind typed object. The full schema is on `StreamEvent` in
  `src/ossia/schemas.py`.
- A final `kind="complete"` event is always sent. Its `data.interrupted`
  field is `true` when the run paused on a human-review interrupt;
  clients should call `POST /v1/threads/{id}/resume` to continue.
- The v2 `event: on_chat_model_stream` / `on_tool_start` / `on_tool_end`
  flat event names are gone. The v3 typed projections replace them.
- v3 is marked experimental by upstream langgraph (`@beta`). If
  upstream changes the projection shape, only the projection adapters
  in `src/ossia/api.py:chat_stream` need to update — the wire contract
  (`kind` + per-kind `data`) is the part we promise to clients.

**Migration:** the v2 client loop was
`for ev in events: handle(ev['event'], ev['data'])`. The v3 client loop
is `for ev in events: handle(ev.kind, ev.data)` where each `data` shape
is the per-kind object documented in `StreamEvent`.

## v1.0.0 — 2026-06-22 — initial unified API

**Breaking** (no prior contract to break — first pinned version).

- New `/v1/*` surface replaces the prior un-versioned `/chat` and
  `/chat/stream` routes. The old routes are removed; this repo does not
  maintain deprecated aliases.
- Pydantic-typed request and response models for every route. Untyped
  `dict[str, Any]` payloads are gone.
- Standard error envelope: `{"error": {"code", "message", "request_id"}}`.
  `X-Request-ID` is honored if the client supplies one.
- New routes:
  - `GET /v1/tools` — list loaded tools with provenance
  - `GET /v1/threads/{id}/state` and `/history`
  - `POST /v1/threads/{id}/resume` — maps to `Command(resume=...)`
  - `GET /v1/audit` — run the audit harness via HTTP
  - `POST /v1/eval` — run the golden-dataset eval via HTTP
- `scripts/audit_ossia.py` and `scripts/eval_ossia.py` are now thin
  HTTP clients (start uvicorn, hit the endpoint, print, tear down).
  The actual logic lives in `ossia.audit` and `ossia.eval`.
- OpenAPI is pinned at `specs/openapi.checked.json`. Drift is a test failure.
