# ADR-0006: Streaming switches to the langgraph v3 protocol

**Status:** accepted.
**Date:** 2026-06-22.
**Supersedes:** ADR-0004 §4, which used `astream_events(version="v2")` for the streaming endpoint.

## Context

```mermaid
flowchart TB
    subgraph Input["📥 DeepAgent v3 Stream"]
        direction LR
        MS["stream.messages<br/><i>coordinator text</i>"]
        TC["stream.tool_calls<br/><i>tool lifecycle</i>"]
        SA["stream.subagents<br/><i>subagent lifecycle</i>"]
        VL["stream.values<br/><i>state updates</i>"]
    end

    subgraph Normalizer["🔄 EventNormalizer"]
        direction TB
        Q["asyncio.Queue<br/><i>shared event queue</i>"]

        RM["_relay_messages<br/><i>→ message_started/delta/completed</i>"]
        RT["_relay_tool_calls<br/><i>→ tool_started/progress/completed/failed<br/>detects pipeline orchestrator tools</i>"]
        RS["_relay_subagents<br/><i>→ subagent_spawned/completed/failed<br/>annotates pipeline step events</i>"]
        RV["_relay_values<br/><i>→ async_task_started/updated/completed</i>"]
        RA["_relay_artifacts<br/><i>→ artifact_received</i>"]

        RM -->|put events| Q
        RT -->|put events| Q
        RS -->|put events| Q
        RV -->|put events| Q
        RA -->|put events| Q

        MAIN["normalize()<br/><i>→ consumes queue → yields OssiaEvent<br/>→ emits interrupt + complete at end</i>"]
        Q --> MAIN
    end

    subgraph Events["📦 OssiaEvent Stream"]
        direction LR
        MSG["message_*<br/><i>coordinator tokens</i>"]
        SUB["subagent_*<br/><i>subagent lifecycle</i>"]
        TOL["tool_*<br/><i>tool call lifecycle</i>"]
        PL["pipeline_*<br/><i>pipeline orchestration</i>"]
        AT["async_task_*<br/><i>background tasks</i>"]
        ART["artifact_*<br/><i>multimodal artifacts</i>"]
        SYS["interrupt / complete / error"]
    end

    subgraph Output["📤 Serialization & Storage"]
        direction LR
        SSE["serialize_sse()<br/><i>→ SSE text/event-stream</i>"]
        BUF["ThreadEventBuffer<br/><i>→ GET /v1/threads/{id}/events<br/>bounded at 10K events/thread</i>"]
        JSON["serialize_json()<br/><i>→ standalone JSON</i>"]
    end

    MS --> RM
    TC --> RT
    SA --> RS
    VL --> RV

    MAIN --> MSG
    MAIN --> SUB
    MAIN --> TOL
    MAIN --> PL
    MAIN --> AT
    MAIN --> ART
    MAIN --> SYS

    MSG --> SSE
    SUB --> SSE
    TOL --> SSE
    PL --> SSE
    AT --> SSE
    ART --> SSE
    SYS --> SSE

    MSG --> BUF
    SUB --> BUF
    TOL --> BUF
    PL --> BUF
    AT --> BUF
    ART --> BUF
    SYS --> BUF

    MSG --> JSON
    SUB --> JSON
    TOL --> JSON
    PL --> JSON
    AT --> JSON
    ART --> JSON
    SYS --> JSON

    style MS fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style TC fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style SA fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style VL fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff

    style RM fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style RT fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style RS fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style RV fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style RA fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style Q fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style MAIN fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff

    style MSG fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style SUB fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style TOL fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style PL fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff
    style AT fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style ART fill:#1a1a2e,stroke:#533483,stroke-width:2px,color:#fff
    style SYS fill:#1a1a2e,stroke:#e94560,stroke-width:2px,color:#fff

    style SSE fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff
    style BUF fill:#1a1a2e,stroke:#0f3460,stroke-width:2px,color:#fff
    style JSON fill:#1a1a2e,stroke:#16213e,stroke-width:2px,color:#fff

    style Input fill:#0d0d1a,stroke:#0f3460,stroke-width:1px,color:#888
    style Normalizer fill:#0d0d1a,stroke:#e94560,stroke-width:1px,color:#888
    style Events fill:#0d0d1a,stroke:#533483,stroke-width:1px,color:#888
    style Output fill:#0d0d1a,stroke:#16213e,stroke-width:1px,color:#888
```

The first cut of `POST /v1/chat/stream` (ADR-0004) used the v2 protocol: `agent.astream_events(input, config, version="v2")` returns a flat async stream of `{event, name, data}` dicts that clients narrow by `event` (e.g. `on_chat_model_stream`, `on_tool_start`, `on_tool_end`).

langgraph 1.x ships a v3 protocol (`astream_events(..., version="v3")`) that returns a typed projection object with `.messages`, `.values`, `.subagents`, `.tool_calls`, `.output`, `.interrupted`, `.interrupts`. The consumer drives the run by iterating typed projections rather than receiving a stream of opaque events.

The v3 protocol is marked experimental upstream (`@beta(message="The v3 streaming protocol on Pregel is experimental.")`) but is the only protocol that supports typed subagent streaming, the caller-driven `output`/`interrupted`/`interrupts` surface, and the content-block-centric streaming model that the rest of langgraph is converging on. The v2 path remains supported in langgraph 1.x but is no longer the recommended surface.

## Decision

`POST /v1/chat/stream` is rebuilt on `astream_events(input, config, version="v3")`. The wire format becomes a discriminated-union SSE envelope: each event's SSE `event:` field is one of seven `kind` values (`message`, `tool_call`, `subagent`, `value`, `interrupt`, `complete`, `protocol`) and the `data:` payload is a per-kind typed object. A final `kind="complete"` event is always sent; `data.interrupted=true` means the run paused on a human-review interrupt and the client should call `POST /v1/threads/{id}/resume`.

The URL prefix stays `/v1/...` because the route shape and auth are unchanged; only the streaming wire format changes. Per the v1.1.0 entry in `specs/changelog.md`, this is documented as a breaking change to `POST /v1/chat/stream` only — other v1 routes are unaffected.

We do **not** keep a v2 alias. Per house style, breaking changes do not get a deprecated twin. Clients on the v2 wire shape must migrate to v3; the migration is a one-liner (loop over `kind` instead of `event`).

## Consequences

- **Pro:** typed projections are stable across langgraph versions; the wire contract is the part we promise to clients, the projection adapters in `api.py:chat_stream` are the part we adapt.
- **Pro:** subagent streams are first-class — clients can render a "researcher" card alongside the coordinator's stream without re-implementing the v3 mux.
- **Pro:** pause/resume is part of the wire contract (`kind="complete"` with `interrupted=true`); clients no longer need to detect interrupts out-of-band.
- **Con:** v3 is experimental. If upstream changes a projection's attribute names (e.g. `tool_name` → `name`), we have to update the adapters and clients don't have to change. If upstream removes v3 entirely, the wire contract survives and the adapters need a rewrite.
- **Con:** the v2 audit harness internal call (`scripts/audit_ossia.py` uses `astream_events(version="v2")` to enumerate tool events) is fine but no longer mirrors the public streaming path. Documented in AGENTS.md.

## Alternatives considered

1. **Keep v2 and add a new `/v2/chat/stream` for v3.** Splits the surface into "old" and "new" and requires clients to pick. The v2 path is the wrong default now that v3 is the recommended upstream. Rejected; the v1 endpoint *is* the breaking change.
2. **Wait for v3 to drop the @beta marker.** v3 has been experimental for several langgraph releases and is unlikely to stabilize on a near-term horizon. Waiting for stability would leave the API on the deprecated v2 path indefinitely. Rejected; the wire-contract / adapter split makes the bet affordable.
3. **Wrap v3 in a per-event v2-emulation layer.** Translates v3 events back to v2 dicts for the wire. Strictly worse than using the typed projections directly. Rejected.
