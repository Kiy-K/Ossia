# Ossia — Context Engineering Document

> **Status:** Active pivot document  
> **Date:** 2026-06-21  
> **Project:** `~/ossia`  
> **Related:** `ossia-deepagents-prompt.md`, Kaggle capstone (`vibecoding-agents-capstone-project`), Nebius Serverless Challenge

---

## 1. Original Conception

Ossia was originally specified as a **customer support agent**:

```
classify_intent → search_knowledge_base → draft_response → human_review → send_response
```

- Domain: generic customer support
- Framework: raw LangGraph StateGraph
- Deployment target: Nebius Serverless (Endpoints + Jobs)
- Goal: production-ready support agent for the Nebius challenge

---

## 2. Pivot: New Direction

Ossia is now a **personal dev concierge agent** — a 24/7 assistant that watches your GitHub repos, triages bugs, researches code, proposes fixes, and opens PRs.

```
GitHub Issue/PR → Triage → Research → Diagnose → Fix Proposal → Human Review → Apply + Test → Open PR
```

- Domain: individual developer productivity
- Framework: **LangChain Deep Agents** (`create_deep_agent`)
- Deployment: `~/ossia` runs anywhere (local/modal/etc.) for Kaggle + personal use
- Training artifact: agent trajectories feed a separate **orchestrator model** trained on Nebius
- Goal: one codebase satisfying **both** Kaggle Capstone (Concierge Agents track) and Nebius Challenge (AI/ML domain)

---

## 3. Why DeepAgent Instead of Raw LangGraph

| Requirement | DeepAgent | Raw LangGraph |
|-------------|-----------|---------------|
| Subagent delegation | ✅ Native `task` tool | ❌ Manual graph wiring |
| Context compression (24/7 agent) | ✅ Auto at 85% threshold + filesystem offload | ❌ Manual summarization |
| Virtual filesystem | ✅ Built-in, pluggable backends | ❌ Custom state |
| Human-in-the-loop | ✅ `interrupt_on` via LangGraph | ✅ `interrupt()` |
| Postgres checkpointing | ✅ LangGraph runtime | ✅ Direct |
| Production maturity | ✅ Harness handles infra plumbing | ❌ You build it |

**Decision:** DeepAgent wins because it bundles the production patterns (context mgmt, filesystem, subagents) that Ossia needs for 24/7 operation without fighting the framework.

---

## 4. Why DeepAgent Instead of Google ADK

| Requirement | DeepAgent | Google ADK |
|-------------|-----------|-----------|
| Cloud independence | ✅ Any provider/cloud | ❌ GCP-flavored |
| Model-agnostic | ✅ 7+ providers out of the box | ⚠️ Best with Gemini |
| Production state mgmt | ✅ LangGraph Postgres/Redis | ❌ Limited |
| MCP support | ✅ First-class | ✅ Supported |
| Community/docs | ✅ Mature | ✅ Newer, smaller |

**Decision:** Ossia targets multiple providers and runtimes. ADK's architectural lock-in to GCP conflicts with that.

---

## 5. Architecture Boundary: Runtime vs Training Target

The key design decision: **Ossia is a data generator, not just an agent.**

```
┌─────────────────────────────────────────────────────────────────────┐
│  OSSIA RUNTIME (portable) — Kaggle Capstone + personal use         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  DeepAgent + tools + MCP + Postgres checkpointing            │  │
│  │  Input: GitHub issues, PRs, chat commands                    │  │
│  │  Output: BugReport, DiagnosisResult, FixProposal, PR         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│                              ▼                                       │
│                     JSONL trajectory data                            │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  OSSIA ORCHESTRATOR MODEL (Nebius) — training target               │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Fine-tuned small model (e.g., Qwen/Gemma 4B)                │  │
│  │  Input: issue text + repo context                             │  │
│  │  Output: structured plan (triage → research → fix → test)    │  │
│  │  Trained on: agent trajectories from the portable runtime     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                       │
│              ┌───────────────┴───────────────┐                     │
│              ▼                               ▼                     │
│   Nebius Serverless Jobs               Nebius Serverless Endpoints  │
│   (training/eval)                      (serving orchestrator)       │
└─────────────────────────────────────────────────────────────────────┘
```

**Why this boundary:**
1. **Kaggle requirement:** public, reproducible repo that runs without Nebius
2. **Nebius requirement:** actual usage of Nebius Serverless (Jobs + Endpoints)
3. **Personal use:** agent runs locally without cloud dependencies
4. **Future flexibility:** orchestrator model can be AB-tested against direct DeepAgent reasoning

---

## 6. Predefined Tool Responsibilities

| Tool | Responsibility | Implementation Status |
|------|---------------|----------------------|
| `fetch_issue` | GitHub Issues/PR API | Stub → real impl |
| `search_codebase` | Local code search (ripgrep / embedding) | Stub |
| `run_tests` | Sandbox test execution | Stub |
| `propose_fix` | Convert DiagnosisResult → FixProposal | Skeleton |
| `create_pr` | Open GitHub PR | Stub (API ready) |
| `finalize_reply` | Human review gate exit point | ✅ Done |
| **MCP tools** | LangChain Docs server (load on demand) | ✅ Wired |

---

## 7. State Model

```python
messages: Annotated[list, add]        # Append-only conversation history
revision_count: int                    # Hard cap: 3 before escalate
human_review_pending: bool            # interrupt() gate
checkpoint: Postgres checkpoint       # Survives cold starts
```

---

## 8. Pending Work (from HANDOFF.md)

1. **MCP graceful-degradation bug** — unreachable MCP server crashes agent start (critical)
2. **Real GitHub integration** — wire fetch_issue/search_codebase/run_tests/create_pr
3. **README refocus** —盖上 for Concierge Agents track + Nebius appendix
4. **Tests** — ≥3 scenarios (currently 5 passing, need GitHub integration tests)
5. **Blog post** — 600+ words, `#NebiusServerlessChallenge`
6. **Video** — ≤5 min, demonstrates 3+ course concepts
7. **Kaggle writeup** — ≤2,500 words, track selection required

---

## 9. Key Decisions (log)

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-20 | DeepAgent over raw LangGraph | Production patterns bundled (context, filesystem, subagents) |
| 2026-06-20 | DeepAgent over ADK | Cloud independence, model-agnostic, no GCP tether |
| 2026-06-21 | Agent-as-data-generator pattern | Satisfies both Kaggle + Nebius with one runtime |
| 2026-06-21 | Concierge Agents track | Personal dev assistant, strong data security angle |
| 2026-06-21 | Separate orchestrator model | Nebius needs actual training/serving; runtime stays portable |
