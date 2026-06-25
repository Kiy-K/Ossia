# Agent Tools

This directory contains configuration files for the Matt Pocock agent skills
setup (to-issues, to-prd, triage, diagnose, tdd, improve-codebase-architecture,
zoom-out).

## Issue Tracker

**Type:** GitHub Issues
**Repository:** Kiy-K/Ossia
**URL:** https://github.com/Kiy-K/Ossia/issues

## Triage Labels

| Label | Meaning |
|-------|---------|
| `bug` | Something is broken |
| `feature` | New capability |
| `enhancement` | Improvement to existing capability |
| `ready-for-agent` | Fully specified, ready for an AFK agent to implement |
| `needs-triage` | Not yet triaged |
| `blocked` | Waiting on another issue or external dependency |
| `good-first-issue` | Accessible to new contributors |

## Domain Glossary

- **Ossia** — the project (brand, PyPI, env-var prefix `OSSIA_*`)
- **core** — the importable module (`from core.X import ...`)
- **Deep Agents** — the agent framework (`deepagents` package)
- **HITL** — human-in-the-loop (approval workflow on `send_response`)
- **PTC** — programmatic tool calling (interpreter calling tools from JS)
- **MCP** — Model Context Protocol (external tool servers)
- **SSE** — server-sent events (streaming protocol)
- **checkpointer** — LangGraph persistence layer (Postgres or in-memory)
- **store** — LangGraph semantic memory store
- **subagent** — delegate worker spun up by the supervisor
- **tool** — agent-callable function with typed schemas
- **middleware** — pre/post-processing around tool calls and agent runs
- **skills** — progressive markdown instruction packs
- **ADR** — architecture decision record (in `docs/adr/`)
- **spec-driven** — pinned OpenAPI contract + drift test workflow
- **TUI** — terminal UI (OpenTUI/React client at `src/tui/`)
- **episodic memory** — per-thread recall via `recall_thread_turns`
- **semantic memory** — agent-scoped long-term store at `/memories/AGENTS.md`
