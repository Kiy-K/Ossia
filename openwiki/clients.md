# Clients

Ossia has two first-party user interfaces: a terminal UI and a browser UI. The TUI is a thin SSE client over the backend's `/v1/*` REST API. The Web UI is a CopilotKit frontend that talks to the backend over the AG-UI protocol at `/agui`. The two UIs consume different backend surfaces, so backend changes can affect them differently.

> Note: `src/webui/README.md` and the root `README.md` were written against a Vite-based Web UI and are now stale — the Web UI was rewritten as a Next.js + CopilotKit app. Trust `src/webui/src/` and `src/core/api.py` over those READMEs until they are updated.

## Terminal UI

The TUI lives in `src/tui/` and is documented in `src/tui/README.md`.

Important points:

- It is built with OpenTUI and React 19.
- It connects to `POST /v1/chat/stream` over SSE.
- It expects the backend at `OSSIA_API_URL` and the API key in `OSSIA_API_KEY`.
- The TUI test suite includes unit, component, stream-parser, and integration tests.

The repository CLI can launch it directly, and `ossia` defaults to running backend + TUI together.

## Web UI

The Web UI lives in `src/webui/` and is a **Next.js 16 + CopilotKit** app (React 19, Tailwind v4). It is the CopilotKit <> LangGraph starter template, adapted to connect to the Ossia backend.

Important points:

- It is **not** a direct SSE client. Instead it runs a CopilotKit runtime (`@copilotkit/runtime`) whose `LangGraphHttpAgent` points at the backend's AG-UI endpoint:
  - `src/webui/src/app/api/copilotkit/[[...slug]]/route.ts` sets `LangGraphHttpAgent({ url: process.env.AGENT_URL || "http://localhost:8000/agui" })`.
- The backend serves that endpoint from `src/core/api.py` (`add_langgraph_fastapi_endpoint(..., path="/agui")`), mounting it at app lifespan.
- The UI demonstrates CopilotKit generative UI (shared agent-driven state, A2UI renderers) and is structured as a showcase/template (`src/webui/src/app/`, `src/webui/src/components/`, `src/webui/src/hooks/`).
- Optional `COPILOTKIT_LICENSE_TOKEN` enables CopilotKit Threads and `copilotkit:intelligence`; without it the runtime uses an in-memory agent runner. `next.config.ts` bakes a `NEXT_PUBLIC_COPILOTKIT_THREADS_ENABLED` flag at build time derived from the token.
- The Web UI has Playwright/E2E coverage expectations but its current tests/ directory is being rebuilt alongside the rewrite.

## Shared backend assumptions

The two clients rely on different backend surfaces:

- TUI: request authentication via `X-API-Key`, streaming through `/v1/chat/stream`, thread replay/history endpoints, consistent event shapes.
- Web UI: AG-UI protocol at `/agui`, CopilotKit runtime auth/identity (`identifyUser`), and the same underlying agent/checkpointer/store as `/v1/*`.

Because the Web UI consumes `/agui` (not the SSE contract), backend changes to event shape or thread metadata may require client changes only on the TUI side, while changes to the agent graph/middleware affect both.

## Change guidance

Start with the client source in the relevant directory, then inspect the runtime code that feeds it:

- `src/tui/src/` for terminal rendering and SSE event handling
- `src/webui/src/` (esp. `app/api/copilotkit` and `app/`) for the CopilotKit/AG-UI integration
- `src/core/api.py` for both the `/v1/*` REST routes and the `/agui` AG-UI endpoint
