# ADR-0003: MCP graceful degradation via worker-per-task

**Status:** accepted.
**Date:** 2026-06-20.
**Supersedes:** none.

## Context

`mcp.client.streamable_http.streamable_http_client` runs the consumer code inside an anyio task group whose cancel scope is task-affine: it must be entered and exited in the same task. On a connection failure, anyio cancels the scope. The cancel surfaces as `asyncio.CancelledError` — a `BaseException`, not a regular `Exception` — and the cancel scope refuses to exit if the parent task tries to handle it.

This breaks the "A single failing MCP server no longer aborts startup" contract. An `except Exception` in the parent does not catch `CancelledError`; an `except BaseException` does, but the parent then tries to exit the cancel scope from a different task and crashes with:

```
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

## Decision

Run each MCP server's connect/initialize/list-tools/park sequence in a dedicated worker task (`_ServerWorker`). The parent never enters the anyio cancel scope, so a transport-internal cancel can never cross the task boundary. The worker converts its own `CancelledError` into a regular `McpServerConnectionError` exception on the worker's `ready` future. The parent observes that as a normal per-server failure and moves on.

The shutdown path uses `asyncio.shield(worker.task)` plus `current_task().cancelling() > 0` to distinguish a genuine external cancel (propagated) from the worker's own cancel completing after our explicit teardown (swallowed).

## Consequences

- **Pro:** one unreachable MCP server is logged and skipped; the agent boots with the remaining/core tools.
- **Pro:** a real external cancel (e.g. the server shutting down) still propagates as `CancelledError`; the degradation path does not swallow shutdowns.
- **Con:** a per-server worker is one extra task and one extra anyio scope per configured server. With 1-5 servers this is negligible; with 50+ it would matter.
- **Con:** the per-server teardown has a 5s grace period; a stuck server can delay shutdown by that amount. Bounded above to keep total teardown time linear in the number of failed servers, not the number of all servers.

## Alternatives considered

1. **`asyncio.shield` the init coroutine and catch `BaseException`.** The shield doesn't fix the task-affine cancel-scope problem; the parent still tries to exit the scope from outside the worker's task.
2. **Use a third-party MCP client that doesn't use anyio.** Would have removed the constraint, but would have lost the `streamable_http` transport.
3. **Run MCP connect in a subprocess.** Cleaner isolation but ~1s per cold start and complicates tool-call dispatch. Not worth it for a one-off startup path.
