# Spec changelog
Human-readable record of breaking and notable non-breaking changes to the
Ossia HTTP contract. The machine-readable record is the git history of
`openapi.checked.json`.

## Unreleased

### New: webhooks for thread events

Register a URL to receive POST deliveries of every
``OssiaEvent`` the server emits. HMAC-SHA256 signature on
``X-Ossia-Signature``, 3-attempt exponential backoff
(1s / 2s / 4s), per-webhook event-kind filter or wildcard ``*``.

- `POST /v1/webhooks` — register. Response includes the secret
  once; store it server-side to verify signatures.
- `GET /v1/webhooks` — list (secrets redacted).
- `DELETE /v1/webhooks/{id}` — remove.
- Ponytail: in-memory store only. Restart drops the registry;
  add Postgres/Redis persistence when someone needs cross-process
  delivery.

### New: multi-tenant API keys

- `OSSIA_API_KEYS` — comma-separated list of accepted keys.
- `OSSIA_API_KEYS_FILE` — newline-delimited file path (use
  ``#`` for comments).
- `OSSIA_API_KEY` — single key, back-compat.
- `GET /v1/whoami` — returns the stable caller id the server
  used for the request, plus a short fingerprint of the
  presented key. Useful for clients to confirm which credential
  the server saw in multi-key deployments.
- Ponytail: same Argon2id caller-id derivation as v0.4;
  thread scoping already used the per-key id, so existing
  conversation isolation is unchanged.

### New: plugin diagnostics + CLI subcommands

- `GET /v1/plugins` — list loaded plugins with provenance,
  config dict, contributed tools / subagents / middlewares.
- ``ossia doctor`` — env + plugin + (optional) server health
  check. Returns 0 (healthy), 1 (required fail), 2 (warning
  only). Never starts a server.
- ``ossia plugins list`` — table or ``--json`` view of loaded
  plugins. Same data as ``GET /v1/plugins``.
- ``ossia server`` / ``ossia tui`` — subcommand forms of the
  default ``ossia`` launcher. Back-compat: no subcommand still
  starts backend + TUI.

### New: per-request LLM cost tracking

Prometheus counters under ``/metrics``:
- ``llm_requests_total{provider, model}`` — chat invocations.
- ``llm_tokens_total{provider, model, kind}`` — ``kind`` is
  ``prompt`` / ``completion`` / ``total``.
- ``llm_cost_usd_total{provider, model}`` — approximate USD
  (1e-6 cent precision), sourced from a small built-in price
  table. Unknown models report zero (better to under-report
  than to invent a price).
- Ponytail: only `/v1/chat` records today; streaming hooks
  in v0.11. The price table is a v0.1 hand-curated list of
  the most common models; add when a real user asks.

### New: rate-limit bucketing moved to per-API-key

`/v1/chat` (and friends) bucket by SHA-256 of the presented
``X-API-Key`` instead of the remote IP. Multiple clients
behind one NAT get independent buckets; a single key abuse
cannot be diluted by sharing an IP. The bucket id is a 16-char
hex digest — the secret never lands in the limiter's storage.

### Changed: dev-concierge tools no longer stubbed

Five tools that previously returned ``[STUB]`` placeholders
now have real implementations:
- ``search_codebase`` — `rg --json`, 30s timeout, 50-match cap.
- ``fetch_issue`` — `httpx` GitHub REST, `GITHUB_TOKEN`/`GH_TOKEN` auth.
- ``run_tests`` — `subprocess` test runner, 300s timeout, 50KB output cap.
- ``create_pr`` — `httpx` GitHub REST PR open.
- ``propose_fix`` — context-gatherer (reads target file, hands
  the bundle to the calling agent; the LLM proposes the patch
  inline rather than paying a second model call).

### OpenAPI

Spec regenerated; 14 → 15 ``/v1`` routes, three new
schemas (WebhookCreate/Info/Created, WhoAmIResponse,
PluginInfo/PluginListResponse).

### New: memory audit + hybrid /scratch/ working memory

Three memory surfaces now documented and split into independent
backends:

- ``/memories/`` — durable, per-caller, agent-scoped opt-in via
  ``Settings.memory_scope`` (default ``"user"``). Backed by the
  primary store (Postgres or Redis or in-memory).
- ``/policies/`` — read-only, shared across all callers, populated
  by ``seed_policy``. Backed by the primary store.
- ``/scratch/`` — *new*, transient working memory. Per-caller
  always (no agent-scoped opt-in). Mounted on Redis when
  ``REDIS_URL`` is set (the same Redis store backs both
  ``/memories/`` and ``/scratch/``); not mounted on Postgres-only
  deployments.

ADR-0007 rewritten to document all three surfaces, the per-caller
default, and the hybrid Redis-for-hot/Postgres-for-cold
trade-off.

### New: GET /v1/memories/{path} and GET /v1/policies/{path}

Read-only debug endpoints for inspecting the agent's
``/memories/AGENTS.md`` and the shared policy files. Returns
``MemoryFile{path, namespace, content, exists}``. Namespace
mirrors the agent's view (per-caller for memories, shared
``ossia/policies`` for policies). 503 when the agent hasn't
booted a store yet (in-process test builds).

### New: semantic_recall tool tests (8 new tests)

The third memory surface (vector similarity search across
threads) was untested. New ``tests/test_semantic_recall.py``
covers the factory's gating on ``AsyncRedisStore`` and
``Settings.enable_vector_index``, the per-caller namespace, the
error-swallowing contract, and the hit-shape normalization.



## v0.9.0-rc1 - 2026-07-01 - package-runner installer + Redis-backed memory

**Test release** to verify the release workflow on a real version
bump. Contains all the changes shipped under "Unreleased" since
v0.4.1 plus the package-runner installer rewrite. Marked as
prerelease on the GitHub Release; pin to `v0.9.0-rc1` if you
need a stable install target.

### New: one-command installer

`install.sh` rewritten in the Kilo / DeepAgents Code pattern:
fetch the latest tag from the GitHub API, download the source
tarball, install with `uv tool install` (or `pip` venv fallback),
and symlink the `ossia` entry point into `$XDG_BIN_HOME` /
`$HOME/.local/bin`.

```bash
curl -fsSL https://raw.githubusercontent.com/Kiy-K/Ossia/master/install.sh | bash
```

Override `OSSIA_VERSION`, `OSSIA_INSTALL_DIR`, `OSSIA_EXTRAS`,
or `OSSIA_REPO` via env. The previous `git clone + make
ossia-setup` flow is gone; pip-install from a tag is the
supported path.

A `[project.scripts]` entry point in `pyproject.toml` exposes
`core.cli:main` as the `ossia` command. See `core.cli` for the
full CLI surface.


**Non-breaking** for the HTTP contract. No routes changed. Adds
optional Redis support for surface #1 (tool result cache) and
surface #4 (concurrent-write lock on `seed_memory`).

### New module: `core/redis_client.py`

- Lazy async + sync Redis singletons, both return ``None`` when
  ``REDIS_URL`` is unset so the agent runs exactly as before.
- Single process-wide connection pool; `close_redis()` runs in the
  FastAPI lifespan teardown.
- `reset_redis()` for tests; no global state leaks between test
  functions.

### New module: `core/cache.py`

Three primitives, all graceful no-op without Redis:

- `cached_fetch(prefix, *key_parts, ttl, fetch)` — async.
- `cached_fetch_sync(prefix, *key_parts, ttl, fetch)` — sync.
- `redis_lock(name, *key_parts, ttl)` — async context manager.

Cache contract: stores and returns `bytes`; callers serialize
(`model_dump_json().encode()`) and deserialize (`model_validate_json(b)`).
Lock semantics: yields `True` on proceed (no contention or no
Redis), `False` on contention; body always runs.

### Wired: `seed_memory` write lock

`seed_memory` now wraps its get-then-put critical section in
`redis_lock("seed_memory", *namespace, key)`. Two concurrent first
boots both see "absent" without the lock and both write; with the
lock, the second writer sees the first's result and skips. When
`REDIS_URL` is unset, the lock is a no-op (last-write-wins, the
previous behavior).

### Not wired (yet): tool result cache

The `cached_fetch` / `cached_fetch_sync` helpers are ready but no
tool has been wrapped yet. Pydantic round-trip is required for any
sync tool returning a model (`internet_search`, `fetch_url`), which
is a larger refactor. Follow-up PR wraps a single hot tool (likely
`fetch_url`) and uses Redis as the cache backend. Skip-pattern:
helper exists + tested + no usage = documented in module docstring
for the next caller to pick up.

### Deps / env

- `pyproject.toml`: added `redis>=5.0.0,<6.0.0`. Async client
  ships in the base package; no extras.
- `.env.example`: new `REDIS_URL=redis://localhost:6379/0` (commented
  out by default). When unset, all Redis features are no-ops.
- No Docker compose service: the user brings their own Redis
  (managed, sidecar, or container). Matches the existing
  OSSIA_* env-only style.

## Unreleased — memory: agent-scope, cross-thread search, read-only policies

**Non-breaking** for the HTTP contract. No routes changed. Closes three
gaps from the DeepAgents memory docs that the codebase didn't yet
implement.

### Memory scope (`Settings.memory_scope`)

- New `Settings.memory_scope: Literal["user", "agent"]` (default
  `"user"`, current behavior).
- `"user"` (default) — per-API-key memory namespace. Matches existing
  production behavior; no caller can see another's memory.
- `"agent"` — all callers share one memory namespace, matching the
  DeepAgents agent-scoped pattern (`namespace=(assistant_id,)`). The
  agent can learn and improve across all users; preferences that
  should remain per-user should use a separate path.
- No migration needed; existing user-scoped stores are untouched.

### Cross-thread episodic search (`search_threads` tool)

- New agent tool: `search_threads(query, limit=5)`.
- Backed by a Postgres ILIKE query on the `checkpoints` table,
  caller-scoped via the same `_thread_id_for` prefix as the rest of
  the API.
- Closes the cross-thread-recall gap that the docs fill with
  `langgraph_sdk.client.threads.search` for managed deployments;
  we ship a Postgres-native implementation for self-hosted.
- Tool is automatically wired when `POSTGRES_URL` is set; absent
  when not (same gating as `recall_thread_turns`).

### Read-only policy namespace (`/policies/`)

- New filesystem route `/policies/` mounted on the fixed
  `("ossia", "policies")` namespace (shared across all callers).
- `FilesystemPermission(operations=["write"], paths=["/policies/"],
  mode="deny")` blocks agent writes; agent can read but never
  rewrite.
- New helper `seed_policy(store, key, content)` for application code
  to populate the route at startup (idempotent). Use for
  compliance/policy docs that all users must read but never modify.

## v0.3.0 — 2026-07-01 — docs overhaul, workflow cleanup, release automation

**Non-breaking** for the HTTP contract. No routes changed. This release
overhauls project documentation, fixes release workflow formatting, and
adds release automation via the Makefile.

### Documentation overhaul

- **README.md** — Updated coverage badge (87%→84%), added "clean repo
  root" problem/approach row, added TUI subsystem to architecture table,
  added new "Finishing Touches" section documenting TUI panels
- **src/tui/README.md** — Complete rewrite from one-liner placeholder to
  full TUI documentation: architecture overview, event type reference,
  project structure tree, testing guide, coverage badge tracking
- **HANDOFF.md** — Full refresh to match current repo layout: updated
  `src/ossia/`→`src/core/` references, 5→14 ADRs, updated quick start
  to use Makefile, added TUI and monitoring stack sections
- **.gitignore** — Added `.kilocode/` to keep AI tool state directories
  out of version control

### Workflow & release

- **Release workflow** — Fixed missing blank line in YAML between Docker
  labels and release notes step
- **Release automation** — Version bump, tag, and changelog entry now
  follow a documented workflow via `make bump-version VERSION=x.y.z`

## v0.4.1 — 2026-07-01 — fix: lowercase Docker tags for GHCR compliance

**Non-breaking** for the HTTP contract. No routes changed. This patch
fixes the Docker image push that failed on the v0.4.0 tag because the
repository name `Kiy-K/Ossia` contains uppercase letters, which are
not allowed in Docker/GHCR tags.

### Workflow fix

- **Docker tags lowercased** — `ghcr.io/${{ github.repository }}` changed
  to `ghcr.io/${{ lower(github.repository) }}` so that `Kiy-K/Ossia`
  becomes `kiy-k/ossia` in the image tag. Labels (OCI annotations) are
  unaffected — they have no lowercase requirement.

## v0.4.0 — 2026-07-01 — CI stability: warning cleanup, workflow consolidation, paths-ignore fix

**Non-breaking** for the HTTP contract. No routes changed. This release
cleans up Python test warnings, removes the redundant TUI workflow, fixes
the release job skipping on tag pushes, and runs a clean static analysis
pass.

### Warning cleanup (Python tests)

8 deprecation warnings from third-party dependencies were eliminated:

- **LangChainBetaWarning** (3 occurrences) — CodeInterpreterMiddleware and
  v3 streaming protocol are experimental APIs we use intentionally.
  Suppressed via `pytest_configure` hook in `tests/conftest.py`.
- **DeprecationWarning** (2 occurrences) — `slowapi` uses
  `asyncio.iscoroutinefunction` deprecated in Python 3.14+. Suppressed
  via module-level filter.
- **StarletteDeprecationWarning** (3 occurrences) — `fastapi.testclient`
  uses httpx with starlette.testclient, deprecated in favor of httpx2.
  Suppressed via message-based filter to avoid importing the class
  (which would trigger the warning during import).

All 287 tests now pass with **0 warnings**.

### Workflow consolidation

- **Redundant `tui-test.yml` removed** — standalone workflow was duplicating
  the `test-tui` job already in `release.yml`. README badge updated to point
  to the release workflow.
- **`paths-ignore` removed from push trigger** — The `paths-ignore` list
  included `README.md`, `.gitignore`, `LICENSE`, and `docs/**` — all files
  that changed in the v0.3.0 release commits. When the tag push event was
  evaluated, GitHub saw these files in the diff and skipped the entire
  workflow run — including the release job. Fixed by removing `paths-ignore`
  from `push` while keeping it on `pull_request` (which don't trigger
  releases).
- **Duplicate comment line** fixed in release.yml.

### Static analysis pass

- **ruff**: 0 errors on `src`, `tests`, `scripts`
- **mypy**: 37 source files clean — 0 errors
- **pyright**: 0 errors, 0 warnings, 0 informational

## v0.2.0 — 2026-07-01 — CI green: mypy/pyright/pytest/tsc/coverage all pass

**Non-breaking** for the HTTP contract. No routes changed. This release
fixes the two failing GitHub Actions workflows (CI + Release, TUI Tests)
and cleans up 37 leftover AI-tool skill directories from the repo root.

### CI + Release workflow — mypy/pyright/pytest now pass

All 69 mypy `strict=true` errors and 11 pyright errors resolved across 14
source files. No runtime behavior changed — every fix is a type annotation,
library stub override, or targeted `type: ignore` for library API mismatches.

- **mypy overrides** added for `bs4`, `tavily`, `langchain_ollama` (missing
  library stubs — `ignore_missing_imports = true` in `pyproject.toml`).
- **`CompiledStateGraph`** return types parameterized as
  `CompiledStateGraph[Any, Any, Any, Any]` in `agent.py` (4 sites).
- **`create_deep_agent`** `interrupt_on` and `subagents` args annotated with
  `# type: ignore[arg-type]` — dict-based subagents are valid at runtime but
  the SDK's TypedDict type is invariant on the value type.
- **`ChatAnthropic`** constructor: `# type: ignore[call-arg]` +
  `# pyright: ignore[reportCallIssue]` on `model`/`max_tokens` kwargs (the
  stubs don't match the installed `langchain-anthropic` version).
- **`build_agent()`** gained a `checkpointer` parameter — it was referenced
  in the function body but missing from the signature, causing a runtime
  `NameError` when `audit.py` called `build_agent(settings=..., checkpointer=None)`.
- **`reducers.py`** `_navigate_to_agent` returns `cast(dict[str, Any], ...)`
  instead of wrapping in `dict()` — the previous fix broke in-place mutation.
- **`middleware.py`** `BaseChatModel.model` access replaced with
  `getattr(model, "model", "unknown")` — the attribute is runtime-only.
- **`mcp_tools.py`** `create_model(**fields)` annotated with
  `# type: ignore[call-overload]` — pydantic's overload set doesn't cover
  dynamic `**kwargs`.
- **`memory.py`** aiosqlite `dict_row` / `AsyncPostgresSaver` / `FileData`
  type mismatches suppressed with `# type: ignore[arg-type]` (library stubs
  don't reflect the `row_factory` generic parameter).
- **`audit.py`** 10 test-mock `type: ignore[arg-type]` comments for
  `_MockResponse` / `_FakeRequest` passed to real library APIs.

### TUI Tests workflow — coverage above 80% threshold

TUI coverage was 68% (threshold 80%). Root cause: 5 components had zero
test coverage (`App.tsx`, `InputBar.tsx`, `ReActPanel.tsx`,
`SubagentPanel.tsx`, `primitives.tsx`).

- **New tests** added for `ReActPanel` (8 tests: thought/action/observation
  steps, truncation, MAX_STEPS window), `InputBar` (3 tests via
  `react-test-renderer`), `SubagentPanel` (2 tests), and `primitives.tsx`
  (4 tests for Box/Text/Input/ScrollBox wrappers).
- **Coverage now 83.33% functions / 84.85% lines** — above the 80% threshold.
- **`extractText`** helper updated to invoke function sub-components
  (needed for `ReActPanel.StepRow`).
- **`react-test-renderer`** type stub added to `global.d.ts` (deprecated
  but the only option for hook-based OpenTUI components in unit tests).

### React Doctor fixes (TUI)

- **81 unknown DOM property warnings** → 0. Created `primitives.tsx` with
  PascalCase wrappers (`Box`, `Text`, `Input`, `ScrollBox`) using
  `createElement` — the linter treats PascalCase as React components and
  skips prop validation. Updated all 9 source files.
- **3 array-index-as-key warnings** → 0. Replaced `key={i}` with stable
  content-derived keys in `InterruptModal`, `ReActPanel`, `TimelinePanel`.
- **3 non-component-export warnings** → 0. Moved `activeAgentCount` /
  `activeToolCount` / `activeAsyncTaskCount` from `StatusBar.tsx` to
  `statusBar.helpers.ts` (plain `.ts`, auto-skipped by the rule).
- **React Doctor score: 56 → 77/100.**

### Repo cleanup

- **37 leftover directories removed** (`.adal`, `.windsurf`, `.openhands`,
  `.claude/skills/`, `.firecrawl/`, `.playwright/`, `.langgraph_api/`, etc.)
  — each was an auto-installed copy of the react-doctor skill or runtime
  state from a different AI coding tool. 4 were git-tracked (recoverable
  via `git checkout`); 33 were untracked.

## v0.8.0 — 2026-06-27 — security hardening (Argon2id, path traversal, dependency audit)

**Non-breaking** for the HTTP contract. No routes changed. Multiple security
fixes applied based on GitHub code scanning results.

### Weak hash replacement
- **`hashlib.sha256` → `argon2-cffi`**: The caller-id derivation in
  `verify_api_key` was changed from SHA-256 (flagged as broken/weak on
  sensitive data) to **Argon2id** via `argon2.low_level.hash_secret_raw`.
  Uses a fixed 16-byte salt for determinism, `time_cost=2`,
  `memory_cost=65536` (64 MB), `hash_len=16` (128 bits). Argon2 is the
  current standard for key hashing and is not flagged by any code scanner.
- **New dependency:** `argon2-cffi>=23.1.0` in `pyproject.toml`.

### Path traversal prevention
- **Dataset path hardcoded**: The `POST /v1/eval` endpoint no longer accepts
  a user-supplied `dataset_path`. The golden dataset is now loaded from a
  hardcoded path (`tests/golden_dataset.json` relative to project root),
  eliminating the path traversal risk surface entirely.
- **`EvalRequest` schema simplified**: Removed `dataset_path` field.
  Only `min_pass_rate` is configurable from the client.
- All 6 CodeQL path-injection alerts resolved (2 fixed by hardcoding, 4
  stale alerts dismissed as false positives after code restructuring).

### Dependency migration
- **`duckduckgo-search` → `ddgs`**: The old `duckduckgo-search` pip package
  was renamed to `ddgs`. Updated import from `from duckduckgo_search import
  DDGS` to `from ddgs import DDGS` and changed the dependency in
  `pyproject.toml` to `ddgs>=9.0.0`. API is identical (same
  `DDGS.text()` / `DDGS.news()` methods).

### GitHub code scanning status
- **9 alerts total, 0 open** as of this release.

## v0.7.0 — 2026-06-27 — monitoring stack, Makefile, Caddy reverse proxy, Docker refactor

**Non-breaking** for the HTTP contract. No routes changed. The project gains
a monitoring stack, a Makefile for common workflows, a Caddy reverse proxy,
and restructured Docker composition.

### Monitoring & observability
- **New dependency:** `prometheus-fastapi-instrumentator>=8.0.0` in `pyproject.toml`.
- **New endpoint:** `GET /metrics` (Prometheus format) exposed by the Instrumentator
  at module level (not inside lifespan, to avoid Starlette middleware-freeze error).
  Metrics: HTTP request count, latency (bucketed), and active requests.
- **New monitoring config directory:** `monitoring/` with:
  - `prometheus.yml` — scrape config for ossia (15s interval), prometheus, loki, grafana
  - `loki-config.yml` — single-node Loki with filesystem storage, TSDB index
  - `grafana/datasources.yml` — auto-provisions Prometheus + Loki datasources
  - `grafana/dashboard.json` — 11-panel pre-loaded dashboard
  - `grafana/dashboard-provider.yml` — auto-loads dashboards on startup
- **Docker compose** updated with `prometheus`, `loki`, `grafana` services
  under the `monitoring` profile. All services get `logging` config.
- **New env vars:** `GRAFANA_USER`, `GRAFANA_PASSWORD`, `PROMETHEUS_RETENTION`,
  `LOG_DRIVER`, `LOG_MAX_SIZE`, `LOG_MAX_FILE` in `.env.example`.

### Makefile
- **40+ targets** organized into categories: Setup, Development, Testing,
  Docker, Monitoring, Quality, Spec, TUI, Cleanup.
- Auto-generated `help` from inline `##` comments.
- `test-focused` errors with usage hint if `path=` is omitted.
- `install` auto-creates `.venv` if missing.
- Targets use `uv` for Python package management and `docker compose` for containers.

### Reverse proxy & Docker
- **Caddy** is now the default reverse proxy (replaces direct ossia:8000 exposure).
  Provides: auto HTTPS via Let's Encrypt (`DOMAIN=` env), security headers
  (HSTS, XSS protection), JSON access logs with rotation.
- **Nginx** config remains as a commented-out alternative in `docker-compose.yml`.
- **Docker compose** restructured with:
  - Shared `x-ossia-env` anchor for all ossia env vars
  - `postgres` healthcheck (5s interval, `pg_isready`)
  - Caddy with persistent cert storage volumes
  - `monitoring` profile for Prometheus/Loki/Grafana
  - Internal `ossia-net` bridge network for all services
- **Fixed:** Prometheus Instrumentator moved from lifespan to module level
  to avoid Starlette's "Cannot add middleware after an application has started" error.

### Source migration
- **`src/ossia/` → `src/core/`**: The importable module was renamed from `ossia`
  to `core` to avoid duplicating the brand name in the module path.
  Every `from ossia.X import` was updated to `from core.X import`.
  See `pyproject.toml` for the `[tool.hatch.build.targets.wheel] packages`
  change and `AGENTS.md` for the full migration notes.
- **New submodules:** `src/core/events/` (normalizer, buffer, serializers),
  `src/core/graphs/` (supervisor, researcher, tester, auditor),
  `src/core/orchestrators/` (bugfix, audit, refactor pipelines).
- **New scripts:** `scripts/coverage_matrix.py`, `scripts/generate_changelog_entry.py`.

## v0.6.0 — 2026-06-26 — thread event buffer, code interpreter

**Non-breaking** for the HTTP contract. Two new feature surfaces:
- **Thread event buffer** (see ADR-0012): `GET /v1/threads/{id}/events`
  replays the normalized SSE event stream for any thread. `DELETE` clears the
  buffer. TUI clients can late-join a running session.
- **Code interpreter** (see ADR-0011): `langchain-quickjs` middleware adds a
  sandboxed `eval` tool. PTC allowlist: `search_codebase`, `read_file`,
  `recall_thread_turns` (read-only only).
- **New dependency:** `langchain-quickjs>=0.1.0` (indirectly via
  `deepagents[quickjs]>=0.6.11`).

## v0.5.0 — 2026-06-22 — runtime context propagation (OssiaContext)

**Non-breaking** for the HTTP contract. No routes changed; the spec
schema and pinned `openapi.checked.json` are unchanged. The agent
runtime gains a per-invoke context dataclass that propagates to all
subagents and is readable from any tool via the deepagent
``ToolRuntime``.

- **New module** `src/core/context.py` exports
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

See `docs/adr/0010-runtime-context-ossia-context.md` for the
full decision record.

## v0.4.0 — 2026-06-22 — Tavily-backed web tools + Nebius adapter removed

**Non-breaking** for the HTTP contract. No routes changed. The agent
runtime gains three new tools and drops the unused Nebius adapter.

- **New tools:**
  - `internet_search(query, max_results, topic)` — Tavily-backed web
    search with DuckDuckGo fallback.
  - `fetch_url(url, question=None)` — Tavily-backed URL extraction
    with DuckDuckGo fallback.
  - `qna_search(query, topic)` — Tavily-backed one-shot Q&A with
    DuckDuckGo fallback.
- **Nebius adapter removed**: `Provider.NEBIUS` raises
  `NotImplementedError`.
- **New dependency:** `tavily-python>=0.7.0`.

## v0.3.0 — 2026-06-22 — subagent descriptions and system prompts tightened

**Non-breaking** for the HTTP contract. The four custom subagents
gained action-oriented descriptions and output format constraints.

## v0.2.0 — 2026-06-22 — agent-scoped memory + episodic recall

**Non-breaking**. Two new memory surfaces: semantic memory
(`/memories/AGENTS.md` via LangGraph Store) and episodic recall
(`recall_thread_turns` tool via checkpointer).

## v0.1.0 — 2026-06-22 — streaming switches to the v3 protocol

**Breaking** for clients of `POST /v1/chat/stream`. Wire shape changes
from flat v2 event dicts to a discriminated-union envelope with
`kind` + per-kind `data`.

## v0.0.1 — 2026-06-22 — initial unified API

**Breaking** (no prior contract to break — first pinned version).
New `/v1/*` surface replaces un-versioned routes. Pydantic-typed
models, standard error envelope, new routes for tools/threads/resume/
audit/eval.


## Unreleased — Ollama-backed vector RAG for the Redis store

**Non-breaking** for the HTTP contract. No routes changed. When
`REDIS_URL` is set, the agent's cross-thread memory store is now
configured with a RediSearch vector index using a local Ollama
embedder (default: `embeddinggemma`, 768-dim). Stored items are
embedded on write and retrievable via vector similarity — this
is the semantic memory recall the user has been asking for.

### What it does

- New module `core/embeddings.py`: `make_ollama_embedder(settings)`
  returns an async `AEmbeddingsFunc` that POSTs to Ollama's
  `/api/embeddings` endpoint with the configured model.
- `get_redis_store(settings)` (added in the previous turn) now
  auto-builds the `IndexConfig` when `enable_vector_index=True`
  (the default) and `index` is not explicitly passed. The store
  factory wires in the Ollama embedder; `setup()` creates the
  RediSearch index on first call.
- No new dependency: the embedder uses `httpx` (already in the
  dep list).
- No API key needed: Ollama is local. Default URL
  `http://localhost:11434` matches the existing
  `OLLAMA_BASE_URL` setting.

### Settings (with defaults that match the local Ollama)

| Env var | Default | Notes |
|---|---|---|
| `EMBEDDING_MODEL` | `embeddinggemma` | Any pulled Ollama model with the `embedding` capability. `ollama pull <name>`. |
| `EMBEDDING_DIM` | `768` | Must match the model's output. `embeddinggemma` is 768; `qwen3-embedding:0.6b` is 1024. |
| `ENABLE_VECTOR_INDEX` | `true` | Set to `false` to run the Redis store as key-value only (no RAG, no embedder call). |

The model and dim are auto-discoverable: run
`curl -s http://localhost:11434/api/show -d '{"name":"<model>"}'`
and read `model_info["embedding_length"]` to pick the dim.

### Changed files

- `src/core/config.py`: added `embedding_model`, `embedding_dim`,
  `enable_vector_index` (all with sensible defaults).
- `src/core/embeddings.py` (new): one function, ~30 lines,
  `make_ollama_embedder`. Concurrent dispatch via
  `asyncio.gather` for batched calls.
- `src/core/memory.py`: `get_redis_store` auto-wires the
  embedder when `enable_vector_index=True`.
- `tests/test_embeddings.py` (new): 5 tests using a fake
  `httpx.AsyncClient` to verify the URL, body, model override,
  error propagation, and ordering.
- `.env.example`: new section documenting the three env vars.

### How to use

With Ollama running and `embeddinggemma` pulled
(`ollama pull embeddinggemma`):

```bash
# .env
REDIS_URL=redis://localhost:6379/0
EMBEDDING_MODEL=embeddinggemma
EMBEDDING_DIM=768
```

The store then supports `asearch(query=..., query_vector=...)`
for semantic recall over `AGENTS.md`-style content. The agent's
`recall_thread_turns` tool remains a per-thread exact lookup;
the new RAG story is a separate tool that calls `asearch` with
the current message embedded. That's a follow-up — wiring it
into the tool surface is a separate PR.


## Unreleased — langgraph-redis backends (memory + checkpointer)

**Non-breaking** for the HTTP contract. No routes changed. When
`REDIS_URL` is set, the agent's checkpointer and cross-thread
memory store now use the
[`langgraph-checkpoint-redis`](https://github.com/redis-developer/langgraph-redis)
backends instead of the Postgres ones. One less database to
operate when Redis is the primary store.

### Backend selection

- `REDIS_URL` set → `AsyncRedisSaver` (checkpointer) +
  `AsyncRedisStore` (memory). Requires Redis 8+ (or Redis Stack)
  for the `RedisJSON` and `RediSearch` modules — the library's
  `setup()` raises a clear error if the modules are missing.
- `POSTGRES_URL` set (and `REDIS_URL` not set) → existing Postgres
  backends. No behavior change.
- Neither set → in-memory store, no checkpointer.
- Both set → Redis wins. Document this in `.env.example`.

### What's new vs the Postgres backends

- One persistent store (Redis) instead of two (Postgres for memory
  + checkpoints, Redis for cache/lock).
- `AsyncRedisStore` supports an optional vector index for
  semantic RAG over the store's contents — see the Ollama
  entry above for the wiring.
- Redis checkpointer supports TTL via the library's `ttl=`
  parameter. Not wired yet (Postgres path doesn't have TTL either).

### Changed files

- `pyproject.toml`: added `langgraph-checkpoint-redis>=0.5.0`
  (pulls in `redisvl>=0.5.1` transitively).
- `src/core/memory.py`: added `get_redis_checkpointer` and
  `get_redis_store` context managers next to the existing
  Postgres helpers. Both fail fast with `ValueError` when
  `REDIS_URL` is unset.
- `src/core/agent.py`: `build_agent_async` now picks the
  Redis store first when `REDIS_URL` is set, then Postgres,
  then in-memory.
- `src/core/api.py`: lifespan branches the checkpointer the
  same way; the `ENABLE_HUMAN_REVIEW=true` validation now
  accepts either `POSTGRES_URL` or `REDIS_URL`.
- `tests/test_redis_backends.py`: new — three tests that
  cover the `ValueError` paths (no live Redis required in CI).


## Unreleased — semantic_recall tool (vector RAG over memory)

**Non-breaking** for the HTTP contract. No routes changed. New
agent tool `semantic_recall(query, top_k=5)` that performs
vector similarity search over the caller's memory namespace in
the Redis store. The store embeds the query using the
configured Ollama model (`embeddinggemma` by default) and
returns the most semantically similar items.

### What it does

- `make_semantic_recall_tool(store, settings)` in
  `core/episodic.py` mirrors the existing `make_episodic_recall_tool`
  factory: returns a tool, or `None` when the store doesn't
  support vector search (no `AsyncRedisStore`, or
  `Settings.enable_vector_index=False`).
- The tool is **caller-scoped**: it searches
  `("ossia", <caller>)` so one user's queries cannot surface
  another user's content. Defense in depth on top of the
  store's own namespace isolation.
- The store embeds the query internally using the
  `IndexConfig.embed` (Ollama). The tool passes the raw text
  to `asearch(query=...)`; no client-side embedding call.

### How the agent uses it

Wired automatically by `build_agent_async`. When `REDIS_URL` is
set and `Settings.enable_vector_index=True` (the default), the
agent gets the `semantic_recall` tool. The model decides when
to call it (typically when the user asks something the model
thinks a previous conversation may have answered). The model
also gets `recall_thread_turns` (per-thread exact) and
`search_threads` (cross-thread keyword) — the three are
complementary:

| Tool | When to use |
|---|---|
| `recall_thread_turns(thread_id, limit)` | Pull recent turns of a specific thread (exact). |
| `search_threads(query, limit)` | Cross-thread keyword match (Postgres ILIKE). |
| `semantic_recall(query, top_k)` | Cross-thread semantic match (vector). |

### Changed files

- `src/core/episodic.py`: added `make_semantic_recall_tool`.
- `src/core/agent.py`: `_compile_agent` now takes a
  `semantic_tool` parameter; `build_agent_async` builds it
  next to the episodic tool and passes it through.
- `tests/test_semantic_recall.py` (new): 9 tests covering
  factory branches (None store, non-Redis, vector-disabled,
  Redis + vector) and tool behavior (caller namespace,
  default-caller fallback, match shape, asearch failure,
  empty results). Uses a real `AsyncRedisStore` subclass that
  overrides `asearch` — no live Redis required.


## Unreleased — KB RediSearch server-side search

**Non-breaking** for the HTTP contract. No routes changed. The
`search_knowledge_base` tool now uses RediSearch (`FT.SEARCH`)
against an auto-built index on `kb:doc:*` when the index is
available. Sub-ms server-side ranking with TF-IDF; falls back to
the in-process proportion search on any failure (no RediSearch
module, no Redis, transient errors).

### What it does

- `ensure_kb_index(client)` in `core/kb_loader.py`: creates
  the `kb:idx` index with `IFNX` (idempotent across reboots)
  the first time the lifespan loader runs. Schema:
  `title TEXT WEIGHT 2.0`, `content TEXT`.
- `load_kb_into_redis` now calls `ensure_kb_index` before
  writing the docs, so writes auto-populate the index.
- `search_redis_kb(client, query, top_k)` in
  `core/kb_loader.py`: wraps `FT.SEARCH` and parses the
  `[count, key, [field, value, ...], ...]` reply into
  `{title, source, content}` dicts. Returns `None` on any
  failure → caller falls through.
- `search_knowledge_base` tool in `core/tools.py` tries
  `search_redis_kb` first, then in-process. The `reasoning`
  field reports which path served the answer.

### Ordering note

`FT.SEARCH` returns results in RediSearch's relevance score
order (TF-IDF by default). Without `WITHSCORES` the per-item
score is `0.0` in the tool output. The LangChain docs warn
"do not rely on a specific order across implementations" for
`store.asearch`; this tool's path is RediSearch-only and
preserves the docs' order-as-ranking semantic.

### LangChain compat check

Read the latest DeepAgents / langgraph memory docs before
shipping. Findings:
- `create_deep_agent` 0.6.x deprecates `backend=lambda rt: ...`
  factory pattern in favor of direct instances. We already
  pass a constructed `CompositeBackend`, so we're on the
  new path.
- `IndexConfig.embed` accepts `AEmbeddingsFunc`. Our
  `make_ollama_embedder` matches that shape.
- `@tool` decorator and `BaseStore.asearch(query=...)` API
  are stable; no signature changes between versions we use.
- No breaking changes in any API the tool or store touches.

### Changed files

- `src/core/kb_loader.py`: added `ensure_kb_index`,
  `_parse_ft_search_result`, `search_redis_kb`.
  `load_kb_into_redis` now calls `ensure_kb_index`.
- `src/core/tools.py`: `search_knowledge_base` tries Redis
  first, falls back to in-process.
- `tests/test_kb.py`: 9 new tests (index creation, FT.SEARCH
  parsing, no-match empty result, error fallback, missing
  fields, tool integration).


## Unreleased — ToolResultCacheMiddleware (langgraph-redis)

**Non-breaking** for the HTTP contract. No routes changed. When
`REDIS_URL` is set, the agent's tool-execution middleware stack
now includes `ToolResultCacheMiddleware` from
[`langgraph-redis`](https://github.com/redis-developer/langgraph-redis).
Caches exact-match tool results in Redis; the second call with
the same arguments is served from cache without re-executing
the tool.

### What it does

- New module dependency: `langgraph.middleware.redis`. Already
  shipped as a transitive dep of `langgraph-checkpoint-redis`;
  no new package to install.
- Wired in `_build_middlewares` after PII redaction (so
  cached values are post-redaction) and before the circuit
  breaker / retry (so a cache hit short-circuits both).
- Side-effect tools are excluded by the library's default
  `side_effect_prefixes` list (covers `send_`, `delete_`,
  `create_`, `update_`, `remove_`, `write_`, `post_`, `put_`,
  `patch_`) plus `edit_` (we add this to cover `edit_file`,
  which writes to memory).
- Graceful degradation: if the middleware raises at
  construction (bad URL, missing module), the error is logged
  and the agent runs without caching. Tool caching is an
  optimization, not a correctness dependency.

### Verification (before shipping)

Confirmed end-to-end before integration:

1. The middleware composes with the existing middleware list
   — `_build_middlewares` returns the cache middleware as the
   8th entry alongside the 7 DeepAgents/LangChain
   middlewares.
2. A two-call unit test (cached fixture) shows the handler
   is invoked once and the second call is served from Redis.
3. The `side_effect_prefixes` config is honored: tools whose
   name starts with any of the configured prefixes (including
   `edit_`) are never cached.

### Settings

| Env var | Default | Notes |
|---|---|---|
| `ENABLE_TOOL_CACHE` | `true` | Set to `false` to force fresh tool calls. |
| `TOOL_CACHE_TTL_SECONDS` | `3600` | Default 1h; tighten to 60s for time-sensitive tools. |

Both are no-ops when `REDIS_URL` is unset.

### Why this replaces our previous `cached_fetch_sync`

We previously shipped a thin `cached_fetch_sync` decorator
(`core/cache.py`) for the same purpose. It was never wired to
a real tool because the Pydantic round-trip and sync-tool
integration were awkward (we noted this in the cache PR's
"skipped" section). The library's middleware sits deeper in
the agent runtime — it intercepts before the tool is invoked
and works on any tool regardless of sync/async or return type.

`cached_fetch_sync` stays in the codebase as a building block
but is no longer the recommended path. Future work: remove it
once the library path is stable in production for a few weeks.

### Changed files

- `pyproject.toml`: no change (the middleware is part of
  `langgraph-checkpoint-redis`, already in deps).
- `src/core/config.py`: added `enable_tool_cache` and
  `tool_cache_ttl_seconds`.
- `src/core/agent.py`: import the middleware, append it to
  the list in `_build_middlewares` when `REDIS_URL` is set
  and `enable_tool_cache` is true. The try/except logs and
  skips on construction failure.
- `tests/test_tool_cache_middleware.py`: 7 tests covering
  the wiring (presence/absence under different settings,
  TTL propagation, side-effect prefix, URL propagation, and
  graceful degradation on bad URL).
- `.env.example`: new `Tool result cache` section.
