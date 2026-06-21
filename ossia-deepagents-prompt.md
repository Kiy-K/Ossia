# Bootstrap Prompt for Your Local Agent

Copy this into whatever agent you're using locally (Claude Code, Codex, whatever):

---

## Research Snapshot: Maybe Better If You're Optimized For Building Agents

**What it is:** `deepagents` is a standalone **agent harness** built on LangChain/LangGraph, designed to be opinionated but replaceable end-to-end.

**Capital G for your use case:** `create_deep_agent(...)` gives you tool calling, planning, filesystem, and built-in context compression out of the box instead of wiring a StateGraph by hand. You can still override or replace any piece. It supports subagents, long-term memory across sessions, and human-in-the-loop approval flows.

**The tradeoff:** the deeper the behavior customizations, the higher the risk you end up fighting the harness; but the Nebius deliverable doesn't require that much custom orchestration, so it should fit well.

---

## Build: Ossia — Production-Ready Support Agent for Nebius Serverless Challenge

### GOAL
Produce a reproducible, model-agnostic support agent deployable on Nebius Serverless AI Endpoints and Jobs.  
Artifact: repo + docs + reproducible run evidence + 600+ word technical blog post.

### PRIMARY FRAMEWORK
**LangChain Deep Agents** as the runtime harness, not raw LangGraph graph construction.

**Prefer the harness:**
- Use `create_deep_agent(...)` for the main agent
- Use built-in tools (filesystem, planning, subagents, memory)
- Use LangGraph internals only where the harness doesn't expose the behavior you need

**Stay portable:**
- No vendor SDK lock-in
- Provider/model from env/CLI/config, never hardcoded
- Structured I/O with Pydantic v2, not raw dicts

### ARCHITECTURE
Conceptual flow:
```
classify_intent → search_knowledge_base → draft_response → human_review → send_response
```

**Implement via Deep Agent:**
- **classify_intent**: LangGraph conditional edge or tool call on the agent
- **search_knowledge_base**: custom tool (KB search + web fallback)
- **draft_response**: agent node with retry policy
- **human_review**: **pause/approval gate** — use Deep Agent's human-in-the-loop (`interrupt()` / approval flow)
- **send_response**: final action tool

**State & persistence:**
- Postgres checkpointing (not SQLite): survives Nebius cold starts
- `messages: Annotated[list, add]` for append-only merge
- Hard cap: max 3 revision loops → force finalize
- Graceful degradation: KB empty → fallback to LLM internal knowledge (never fail silently)

### TECH STACK
- **Python 3.11+**
- **LangChain Deep Agents** (`pip install deepagents`)
- **LangGraph** runtime (Deep Agents dependency)
- **Pydantic v2** for all I/O schemas
- **Postgres** for checkpointing (`langgraph-checkpoint-postgres`)
- **Nebius SDK / REST** for model endpoints
- **7+ provider support**: Google Gemini, OpenAI, Anthropic, OpenRouter, Fireworks, Baseten, Ollama

### CONSTRAINTS
- Model-agnostic: provider/model from env vars, zero hardcoding
- RetryPolicy on all external calls (3 retries, exponential backoff)
- Streaming via `astream_events` for real-time UI
- Single-file agent (`agent.py`) unless >300 lines
- Type hints everywhere
- Docstrings on every function
- No broad imports — only what you use

### DELIVERABLES
1. **`src/ossia/agent.py`** — wire up Deep Agent with tools, system prompt, human review gate, retry pipeline
2. **`src/ossia/memory.py`** — Postgres-backed checkpoint/history + optional cross-session memory
3. **`src/ossia/tools.py`** — KB search, web search, fallback, grading/eval tools
4. **`src/ossia/config.py`** — env/model/provider config with Pydantic models
5. **`tests/test_graph.py`** — minimum 3 test scenarios (happy path, KB miss fallback, human review loop)
6. **`notebooks/demo.ipynb`** — end-to-end walkthrough
7. **`README.md`** — Mermaid architecture diagram, setup, Nebius deployment guide, cost estimate

### NEBIUS DEPLOYMENT CONTRACT
Create clear separation between portable agent logic and Nebius-specific deployment:

```
src/ossia/
├── agent.py           # Core Deep Agent logic (portable)
├── memory.py          # Postgres checkpointing (portable)
├── tools.py           # KB/search/fallback tools (portable)
├── config.py          # Env-based config (portable)
├── adapters/
│   └── nebius.py      # Nebius-specific: endpoint clients, job runners, auth
└── prompts/
    └── system.md      # Versioned prompts (pull from registry in prod)

nebius/
├── endpoints/         # vLLM server configs for candidate/judge/embedder models
├── jobs/              # Batch eval pipelines (Job 1, 2, 3 from EvalOps)
├── docker/            # Container images for jobs + endpoints
└── deploy.sh          # One-command deploy script
```

**The story:** here is a portable production agent; here is how to run it on Nebius (docs + one deploy command).

### TESTING STRATEGY
- **Unit tests**: mock tools, verify routing, state transitions
- **Integration tests**: run against a local LLM (Ollama) or tiny provider
- **Eval tests**: golden dataset of 10 support queries with expected intents/responses
- **Human review loop test**: simulate approval/rejection with `Command(resume=...)`

### CODE STYLE
- **Deep Agents idioms first**: `create_deep_agent()`, built-in `task` tool for subagents, filesystem middleware for context management
- **Override selectively**: only if harness behavior conflicts with requirements
- Single-file `agent.py` unless >300 lines, then split by concern
- Pydantic for all tool inputs/outputs
- No LangChain bloat — import only what you use

### SUCCESS CRITERIA
✅ Agent runs end-to-end locally with mocked or tiny provider  
✅ Postgres checkpointing persists state across restarts  
✅ Human-in-the-loop blocks at `human_review` until explicit approval  
✅ KB fallback works when search returns empty  
✅ 3-iteration cap prevents infinite loops  
✅ All external calls wrapped in RetryPolicy (3 retries, exponential backoff)  
✅ Streaming UI feedback via `astream_events`  
✅ One-command deploy to Nebius (`./nebius/deploy.sh`)  
✅ Blog post ready to publish (600+ words, #NebiusServerlessChallenge)

### NEBITUS-SPECIFIC FEATURES TO HIGHLIGHT
- **Serverless cold-start survival**: Postgres checkpointing lets the agent resume mid-conversation
- **Cost optimization**: min_containers=0 on endpoints, batch jobs for eval/training
- **Multi-product usage**: Endpoints (serving) + Jobs (batch eval pipeline)
- **GPU utilization**: L40S for vLLM serving, batch inference
- **Observability**: LangSmith tracing + Nebius job logs integration

---

**Prompt author context:** This is a local instruction prompt for a coding agent. Its role model is Claude Code or Codex prompts; keep it concise, prescriptive, and unambiguous.
