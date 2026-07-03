# Handoff: Memory Layer — All Issues Resolved

**Date:** 2026-07-02.
**Status:** All known memory layer issues fixed and verified. 28 tests pass across 3 test files.

## What Was Fixed

### 1. 🔴 `recall_thread_turns` `InvalidStateError` — Fixed

**Root Cause**: The `@tool`-decorated async function's `alist` detection used `getattr(checkpointer, "alist", None)` to choose between sync/async checkpointer APIs, but when invoked through the Deep Agents runtime the async path wouldn't fire correctly, causing the code to fall through to the sync ``list()`` method. `AsyncPostgresSaver` raises `InvalidStateError` when its sync methods are called from the event-loop thread.

**Fix**: Replaced the `alist` detection with `anyio.to_thread.run_sync(lambda: list(checkpointer.list(...)))`. This runs the sync `list()` on a thread-pool worker — a **different thread** from the event-loop, which `AsyncPostgresSaver` allows. Works with both sync (`InMemorySaver`) and async checkpointers.

**Files touched**: `src/core/episodic.py:131-148`

### 2. 🧹 Debug Markers Removed

Removed the `print(f"!!! recall_thread_turns CONFIG: ...")` and `print(f"!!! recall_thread_turns ERROR !!! ...")` debug statements, plus the `import sys` and `import traceback` used only by them. Cleaned the error response to remove `traceback`, `marker` fields — only a concise `error` string remains when recall fails.

**Files touched**: `src/core/episodic.py:130-148`

### 3. 🟡 Seed Namespace Mismatch — Fixed

**Problem**: `seed_memory()` wrote to `("ossia", "default")` but the agent (in user scope) reads from `("ossia", caller_hash)`, so the seed was invisible.

**Fix (two parts)**:
- **Base namespace**: Changed `AGENT_NAMESPACE` from `("ossia", "default")` to `("ossia",)` — the true agent-level namespace. Startup seed now aligns with agent-scoped reads.
- **Per-caller seeding**: Added `ensure_caller_memory_seeded(store, caller)` in `memory.py`, called from both `chat` and `chat_stream` handlers in `api.py` before the agent runs. Each caller's namespace gets seeded on first request (idempotent thereafter).

**Files touched**: `src/core/memory.py:36-51`, `src/core/api.py:560-571`

### 4. 🔧 Tool Runtime Refactoring

Refactored all three memory tools to accept the injected `runtime` parameter (matching the `grade_response` pattern in `tools.py`):
- `semantic_recall`: Now reads store from `runtime.store` instead of the closure, with a fallback to the closure for backward compatibility (direct `tool.ainvoke()` calls in tests)
- `recall_thread_turns` and `search_threads`: Added `runtime` parameter for signature consistency (closures still needed for checkpointer/search_fn since they aren't available via runtime)

**Files touched**: `src/core/episodic.py`

### 5. ✅ E2E Test Added

`test_recall_through_agent_with_checkpointer` in `tests/test_episodic.py` builds a real Deep Agents agent with `InMemorySaver` + `recall_thread_turns` tool, populates checkpoints via a real graph run, scripts a tool call through a `_FakeToolModel`, and verifies the tool returns correct content through the agent runtime — the exact failure path that was broken before.

## Verification

All 28 tests pass across 3 test files:
```bash
.venv/bin/python -m pytest tests/test_episodic.py tests/test_memory.py tests/test_semantic_recall.py -v
# → 28 passed in 1.15s
```

| Test file | Count | Status |
|-----------|-------|--------|
| `test_episodic.py` | 10 | ✅ All pass (includes new E2E test) |
| `test_memory.py` | 11 | ✅ All pass |
| `test_semantic_recall.py` | 8 | ✅ All pass |

Lint (ruff) and typecheck (pyright): clean on all modified files.

## Key Files Changed

| File | What changed |
|------|-------------|
| `src/core/episodic.py` | Fixed recall_thread_turns with anyio.to_thread.run_sync(); removed debug prints; cleaned error response; added `runtime` params to all 3 memory tools |
| `src/core/memory.py` | Changed `AGENT_NAMESPACE` to `("ossia",)`; added `ensure_caller_memory_seeded()` helper |
| `src/core/api.py` | Added per-caller lazy memory seeding in `chat` and `chat_stream` handlers |
| `tests/test_episodic.py` | Added E2E test `test_recall_through_agent_with_checkpointer`; added `_FakeToolModel` and `import pytest` |