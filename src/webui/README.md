# Ossia Web UI

<p align="center">
  <strong>React 19 + Vite + Tailwind CSS v4</strong> — ChatGPT-inspired chat interface for the Ossia agent.
</p>

<p align="center">
  <code>make dev-web</code> or <code>make dev-all-web</code>
</p>

## Overview

The Ossia Web UI is a browser-based chat interface that connects to the Ossia backend via [Server-Sent Events (SSE)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events). It provides a ChatGPT-like experience:

- **ChatGPT-style layout** — minimal header, centered empty state, sticky composer, high-contrast user bubbles
- **Session sidebar** — thread list fetched from `GET /v1/threads`, click-to-switch sessions, "New Chat" button
- **Streaming responses** — real-time agent responses via SSE, incremental tool call tracking
- **Dark/light mode** — persists to localStorage, no flash on load with inline blocking script
- **Connection config** — API URL + key via gear icon, live connection indicator (green/red dot)
- **Markdown rendering** — assistant messages rendered with `@assistant-ui/react-markdown` + Shiki syntax highlighting
- **Tool calls** — inline tool call cards with collapsible `ToolGroup` wrapper for consecutive calls
- **All lucide-react icons** — no other icon dependency

## Architecture

```
src/webui/
├── index.html              # Entry point with blocking dark-mode script
├── vite.config.ts           # Vite + Tailwind v4 + React plugin
├── playwright.config.ts     # E2E test config
├── package.json
├── tsconfig.json
├── tsconfig.app.json
├── src/
│   ├── main.tsx             # React DOM entry
│   ├── App.tsx              # Root component: layout, header, sidebar, config, chat
│   ├── index.css            # Tailwind v4 + ChatGPT-inspired color palette
│   ├── stream.ts            # SSE parser (parseSSEStream) + health check
│   ├── types.ts             # Shared types (OssiaEvent, Config, etc.)
│   ├── constants.ts         # localStorage key constants
│   ├── components/
│   │   ├── MyRuntimeProvider.tsx    # assistant-ui runtime provider
│   │   ├── SessionSidebar.tsx       # Thread list sidebar (session management)
│   │   ├── TooltipIconButton.tsx    # Reusable icon button with CSS tooltip
│   │   ├── ToolGroup.tsx            # Collapsible group for consecutive tool calls
│   │   ├── ToolFallback.tsx         # Fallback UI for unregistered tool types
│   │   └── MarkdownText.tsx         # Markdown renderer with Shiki code highlighting
│   ├── runtimes/
│   │   └── ossia-external-store.ts  # ExternalStoreAdapter for SSE streaming
│   ├── stores/
│   │   └── sideChannel.ts           # Pub/sub store for side-channel events
│   └── tools/
│       ├── ossia-toolkit.tsx        # Barrel file — assembles all tool UIs
│       ├── common.tsx               # Shared styles + withSafeStatus HOC
│       ├── search.tsx               # SearchKBUI, InternetSearchUI, QnaSearchUI
│       ├── code.tsx                 # SearchCodebaseUI, RunTestsUI, ProposeFixUI, FetchUrlUI
│       ├── pr-tools.tsx             # CreatePrUI, FetchIssueUI
│       └── response.tsx             # SendResponseUI, GradeResponseUI
└── tests/
    ├── syntax-highlighting.spec.ts   # Playwright e2e: Shiki code-block rendering
    └── tool-ui.spec.ts               # Playwright e2e: tool UI card rendering
```

## Quick Start

### Prerequisites

- Node.js 20+ with npm
- The Ossia backend running on `http://localhost:8000`

### Start the Web UI

```bash
# Start both backend + Web UI together:
make dev-all-web

# Or start just the Web UI (backend must be running separately):
cd src/webui && npm install && npm run dev

# Open http://localhost:5173
```

### Configure connection

Click the gear icon (⚙️) in the header to set:
- **API URL** — default `http://localhost:8000`
- **API Key** — default `dev`

Both values persist to localStorage.

## Features

### ChatGPT-Style Chat Interface

The UI mirrors the modern chatgpt.com layout:

| Element | Description |
|---------|-------------|
| **Minimal header** | Sidebar toggle + brand + connection dot + dark mode + settings |
| **Empty state** | "Where should we begin?" heading + centered composer + suggestion buttons |
| **Chat composer** | `rounded-[28px]` with tooltipped add-attachment, four-state primary action (Cancel / StopDictation / Send / Dictate+voice) |
| **User bubble** | Right-aligned, `max-w-[70%]`, high-contrast (`#0d0d0d` light / `#ececec` dark), Copy + Edit actions on hover |
| **Assistant message** | Markdown text, tool calls (grouped), "Thinking…" indicator, full action bar |
| **Action bar** | Copy, thumbs up/down, read aloud, share, regenerate, more |
| **Sticky footer** | Composer + disclaimer at the bottom of the chat view |

### Session Sidebar

Toggle with the `PanelLeftOpen` button in the header. The sidebar:

- Fetches `GET /v1/threads` to list all sessions for the authenticated caller
- Displays each thread's **title** (lazy-loaded from the first user message via `GET /v1/threads/{id}/history`) with a relative timestamp
- Shows a "New Chat" button to start a fresh session
- Highlights the active session
- Click a thread to switch — the runtime clears messages, aborts in-flight requests, and loads the new thread's history
- Responsive: overlay on mobile (`<md`), fixed sidebar on desktop

### Session Switching Architecture

Thread switching is exposed via the `useOssiaControls()` context hook:

1. User clicks a thread in the sidebar → `SessionSidebar` calls `onSwitchThread(threadId)`
2. `App` invokes `switchThread(threadId)` from the runtime controls
3. The runtime aborts any in-flight request, clears messages, updates `thread_id` in the side channel, and loads the new thread's history from `GET /v1/threads/{id}/history`
4. For "New Chat", `threadId` is the empty string — the runtime clears messages and sets an empty `thread_id`

### Tool Call UI

Tool calls are rendered inline in the assistant message:

- **ToolGroup** — consecutive tool calls (e.g. `search_codebase` → `run_tests`) are grouped into a single collapsible card with count badge, status dot (animated pulse for running), and expand/collapse
- **ToolFallback** — fallback renderer for unregistered tool types

Each tool has a dedicated UI component registered in `ossia-toolkit`:

| Module | Components |
|--------|-----------|
| `search.tsx` | SearchKBUI, InternetSearchUI, QnaSearchUI |
| `code.tsx` | SearchCodebaseUI, RunTestsUI, ProposeFixUI, FetchUrlUI |
| `pr-tools.tsx` | CreatePrUI, FetchIssueUI |
| `response.tsx` | SendResponseUI, GradeResponseUI |

### Streaming Runtime

The runtime (`ossia-external-store.ts`) is a custom `ExternalStoreAdapter` for `@assistant-ui/react`. It:

1. **Loads history** — on mount or thread switch, fetches `GET /v1/threads/{id}/history`
2. **Streams responses** — on user message, POSTs to `/v1/chat/stream` and incrementally updates the message list from SSE events
3. **Tracks tool calls** — maps SSE tool events to `tool-call` content parts for the Tool UI components
4. **Dispatches side events** — forwards raw Ossia events to the side-channel store
5. **Handles reload** — `onReload` trims back to the parent message and re-streams

### Dark Mode

- Defaults to dark mode on first visit
- Toggles via Sun/Moon button in the header
- Persists to localStorage (`ossia:darkMode`)
- Flash-free: an inline `<script>` in `index.html` reads localStorage before the page renders and applies the `dark` class immediately

### Color Palette (ChatGPT-inspired)

| Element | Light | Dark |
|---------|-------|------|
| Background | `#ffffff` | `#000000` |
| Composer surface | `#ffffff` | `#212121` |
| Composer border | `#e5e5e5` | transparent + `inset 0 0 0 1px rgba(255,255,255,0.1)` |
| Primary text | `#0d0d0d` | `#ececec` |
| Muted text | `#5d5d5d` | `#afafaf` |
| User bubble | `#0d0d0d` (white text) | `#ececec` (`#0d0d0d` text) |
| Action icon | `#5d5d5d` | `#cdcdcd` |
| Icon hover bg | black at 7% | white at 15% |
| Send button | `#0d0d0d` (white icon) | `#ffffff` (black icon) |

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `@assistant-ui/react` | ^0.14.26 | Thread, message, composer primitives; ExternalStoreRuntime |
| `@assistant-ui/react-markdown` | ^0.14.5 | Markdown rendering |
| `lucide-react` | ^1.23.0 | Icon set (all icons from this library) |
| `motion` | ^12.x | Animations (config panel, transitions) |
| `react` | ^19.2.7 | UI framework |
| `shiki` | ^4.x | Syntax highlighting for code blocks |

## E2E Tests

```bash
cd src/webui && npm run test:e2e
```

Uses Playwright against the Vite dev server. Tests are in `src/webui/tests/`.

## Project Status

The Web UI has been fully rewritten from a multi-panel layout (Chat/Subagents/Tools/ReAct tabs) to a single-panel ChatGPT-style interface. The old panel components (`SubagentPanel.tsx`, `ToolPanel.tsx`, `ReActPanel.tsx`) have been removed as dead code. The side-channel store (`sideChannel.ts`) still tracks the raw event data for potential future use.

### Screenshots & demo video

| # | File | Shows |
|---|------|-------|
| 1 | `01-empty-state.png` | "Where should we begin?" centered empty state with suggestion chips |
| 2 | `02-sidebar-open.png` | Session sidebar with thread list (titles + relative timestamps) |
| 3 | `03-streaming.png` | User message + streaming assistant placeholder with full action bar |
| 4 | `04-response-complete.png` | Full markdown response (headings, bullets, inline code) |
| 5 | `05-hitl-interrupt.png` | 🛡️ Human review required card with action request preview + Approve/Reject |
| 6 | `06-hitl-approved.png` | HITL resumed run, final agent response |
| 7 | `07-code-response.png` | Python code with Shiki syntax highlighting |
| 8 | `08-code-full.png` | Full code block + assistant action bar |

A 21-second demo video with crossfade transitions is at `ossia-demo-xfade.mp4`.

### Recent Changes

- **ChatGPT-style rewrite** — Full layout redesign matching chatgpt.com: centered empty state, sticky composer, high-contrast bubbles, full action bar
- **Session sidebar** — Thread list from `GET /v1/threads` with click-to-switch, lazy-loaded titles from thread history
- **Direct switchThread** — `useOssiaControls().switchThread()` exposed via React context (no signal-based side-channel dance)
- **HITL resume** — `InterruptPrompt` banner + `useOssiaControls().resume()` posts decisions to `POST /v1/threads/{id}/resume`
- **Thread metadata endpoints** — Backend gained `POST /v1/threads`, `GET /v1/threads/{id}`, `PATCH /v1/threads/{id}`, `POST /v1/threads/{id}/unarchive` for the assistant-ui `RemoteThreadListAdapter` shape. Frontend `threadList/ossiaAdapter.ts` implements the adapter (ready for `useRemoteThreadListRuntime` migration once the inner `ExternalStore` exposes a `history` adapter).
- **Nested-button fix** — Thread button and delete action are now siblings inside `<li>`, not nested. React hydration error gone.
- **Streaming rewrite** — Prepended empty assistant placeholder with `status: "running"` to short-circuit v0.14's optimistic-assistant creation. In-place content updates via `updatePlaceholder(status)` with the proper `MessagePartStatus` discriminated union.
- **ToolGroup** — Collapsible wrapper for consecutive tool calls
- **lucide-react** — Replaced `@phosphor-icons/react` with `lucide-react` (single icon dependency)
- **Dead code removal** — Removed old multi-panel components no longer rendered
