# Feature: Deterministic Session ID System

- Status: implemented
- ADR: docs/adr/0004-unified-http-api-v1.md
- Scope: route | infrastructure

## What it does

Adds a reproducible session/thread ID system using UUID v5 deterministic hashing so that the same caller, project context, and session topic always map to the same thread ID. This is analogous to ChatGPT or Gemini's session sidebar — a user can rejoin a previous conversation just by specifying the same topic slug, without remembering a random UUID.

The feature also supports "New Chat" flows (fresh UUID v4 sessions), automatic project context detection from git remote or working directory, client-side session caching via `.kilocode/active_session.json`, and HTTP header injection (`X-Session-Topic`, `X-Project-Context`) for clients that prefer headers over payload fields.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Session ID derivation | UUID v5 from ``(caller_id, project_context, topic)`` composite key using a fixed ``OSSIA_NAMESPACE``. Same triplet → same UUID. | Session migration, versioned namespaces, or cross-caller session sharing. |
| Multi-session support | Distinct topic slugs (e.g. ``"bugfix-auth"``, ``"refactor-api"``) produce distinct session IDs within the same project. Topic ``"default"`` when unspecified. | Named session listing, session rename, or session deletion API. |
| New Chat flow | ``new_session=true`` generates a random UUID v4 and returns metadata for client-side caching. | Server-side session registry or lifecycle management. |
| Project context | Auto-detected from ``OSSIA_PROJECT_CONTEXT`` env var → git remote origin → CWD basename → ``"unknown"``. | Multi-repo workspace detection, branch-scoped sessions, or remote git remotes (only ``origin`` is checked). |
| Client cache | Server provides ``read_active_session`` / ``write_active_session`` / ``clear_active_session`` helpers for reading/writing ``.kilocode/active_session.json`` at the repo root. | Server-initiated cache writes, encrypted cache, or multi-file cache (single-file only). |
| Header injection | ``X-Session-Topic`` and ``X-Project-Context`` HTTP headers accepted as fallbacks when payload fields are absent. Payload always wins. | ``X-Session-ID`` header for direct thread ID bypass. |
| Backward compat | ``thread_id`` field in ``ChatRequest`` still accepted and used directly (caller-scoped). Old clients continue to work unchanged. | Deprecation of the ``thread_id`` field. |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| `POST` | `/v1/chat` | Modified — ``ChatRequest`` gains ``session_topic``, ``new_session``, ``project_context`` fields. HTTP headers ``X-Session-Topic`` and ``X-Project-Context`` accepted as fallbacks. Handler uses ``resolve_thread_id`` instead of ``_thread_id_for``. |
| `POST` | `/v1/chat/stream` | Modified — same schema and header changes as ``/v1/chat``. Handler uses ``resolve_thread_id`` instead of ``_thread_id_for``. |

## Safety/Permissions

- **Caller scoping**: The session ID includes the caller hash in the composite key fed to UUID v5, so two different API keys with the same topic get different session IDs. The deterministic ID is also prefixed with ``{caller_id}:`` to match the existing thread route format.
- **Project context isolation**: The project context string is hashed into the UUID, so the same topic in different projects produces different session IDs.
- **No new interrupt points**: Session resolution is purely an ID-derivation step before the agent runs. It does not add any HITL interrupt points, tool calls, or agent-visible state changes.
- **Environment detection is best-effort**: ``detect_project_context`` falls back to CWD basename or ``"unknown"`` when no git remote is available. Production deployments should set ``OSSIA_PROJECT_CONTEXT`` explicitly for deterministic behavior across machines.
- **Header injection is opt-in**: Headers are only read when present; the server never requires them. Payload fields always take precedence over headers, so both mechanisms coexist without ambiguity.

## NFRs

- **Streaming:** Unaffected. Session resolution happens before the streaming loop starts; the resolved ``thread_id`` is passed into the agent config identically to the previous path.
- **Checkpointing:** Unaffected. The checkpointer still uses the same scoped ``thread_id`` format (``{caller}:{uuid}``) — only the ID derivation changed.
- **HITL:** Unaffected. No new interrupt points are introduced.
- **Performance:** UUID v5 hashing is sub-microsecond. The only I/O is the optional ``subprocess.run(["git", "remote", "get-url", "origin"])`` for project context detection, which has a 5-second timeout and is cached implicitly per-process (env vars don't change mid-run). For latency-sensitive deployments, set ``OSSIA_PROJECT_CONTEXT`` to skip the git call entirely.
- **Determinism:** Same input triplet always produces the same UUID v5 output. This is a mathematical property of the UUID v5 algorithm (SHA-1 hashing of the namespace + name), not an implementation detail.

## Affected modules

- `src/core/utils/session.py` — New module. Contains `make_session_id`, `detect_project_context`, `new_random_session`, `resolve_thread_id`, and the `.kilocode/` cache helpers (`read_active_session`, `write_active_session`, `clear_active_session`). Also defines `SessionMetadata` dataclass and `OSSIA_NAMESPACE` constant.
- `src/core/utils/__init__.py` — New. Exports the `core.utils` package.
- `src/core/schemas.py` — Modified. `ChatRequest` gained `session_topic: str | None`, `new_session: bool`, `project_context: str | None` fields.
- `src/core/api.py` — Modified. Added `_SessionHeaders` carrier class and `session_header_params` FastAPI dependency. Both `chat()` and `chat_stream()` now call `resolve_thread_id()` instead of `_thread_id_for()`, merging payload fields with header fallbacks.
- `tests/test_session.py` — New. 41 tests covering determinism, uniqueness, project context detection, remote URL parsing, random sessions, `.kilocode/` cache operations, thread ID resolution, edge cases, and backward compatibility.

### Client-side changes (separate packages)

- `src/tui/src/session.ts` — New. Bun fs-based session cache for `.kilocode/active_session.json`.
- `src/tui/src/events/stream.ts` — Modified. `StreamOptions` includes `sessionTopic`, `newSession`, `projectContext`.
- `src/tui/src/App.tsx` — Modified. Reads cache on mount, persists thread ID after first event, handles New Chat.
- `src/tui/src/components/InputBar.tsx` — Modified. Shows `[topic]` badge in prompt line.
- `src/webui/src/stream.ts` — Modified. New `SendMessageOptions` type replaces positional args; supports session fields.
- `src/webui/src/constants.ts` — Modified. Added `SESSION_ID` and `SESSION_TOPIC` localStorage keys.
- `src/webui/src/reducer.ts` — Modified. Added `__reset` and `__restore` client-only event handlers.
- `src/webui/src/App.tsx` — Modified. Restores session from localStorage on mount, New Chat button, inline topic editing.
- `src/webui/src/components/ChatPanel.tsx` — Modified. Session topic bar with inline editing and `+ New Chat` link.

## Testing notes

- **Unit tests** in `tests/test_session.py` (41 tests):
  - Deterministic session IDs (caller/project/topic combinations, default topic, UUID version check).
  - Project context detection (CWD basename, git remote, env override, edge cases).
  - Remote URL parsing (SSH, HTTPS, Azure, dashes, missing .git suffix).
  - Random/new session generation (unique IDs, metadata, scoping).
  - `.kilocode/` cache operations (write, read, clear, nonexistent file, idempotent mkdir).
  - Thread ID resolution (deterministic, with topic, new session, explicit thread ID, backward compat, project context override).
  - Edge cases (empty caller ID, special characters in topic, Unicode in topic, deterministic across multiple calls).
- **Backward compat tests** in `tests/test_api.py` — existing thread-scoping tests continue to pass (the ``thread_id`` field still works as before).
- **OpenAPI drift test** in `tests/test_openapi_drift.py` — validates that the pinned spec matches the actual API surface after regeneration.
- **Manual smoke test:**
  1. Start the server: `make dev`
  2. Send a first message with `session_topic: "my-topic"` — note the returned `thread_id`.
  3. Send a second message with the same `session_topic: "my-topic"` — the returned `thread_id` should be identical (deterministic).
  4. Send a message with `session_topic: "other-topic"` — the returned `thread_id` should be different from step 2.
  5. Send a message with `new_session: true` — the returned `thread_id` should be a random UUID v4.
  6. Verify the OpenAPI spec matches: `scripts/update_openapi_spec.py` + `pytest -k openapi_drift`.
- **Known limitation**: Project context detection runs a subprocess (git remote) which could block in event-loop-thread context. For production, set `OSSIA_PROJECT_CONTEXT` to bypass.
