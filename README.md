# Ossia — Production-Ready Support Agent

Ossia is a portable, model-agnostic support agent built on [LangChain Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) for the Nebius Serverless Challenge. It demonstrates production patterns: Postgres checkpointing, human-in-the-loop approval, knowledge base fallback, retry policies, and one-command deployment to Nebius.

## Architecture

```mermaid
graph LR
    A[User Query] --> B[classify_intent]
    B --> C[search_knowledge_base]
    C --> D[draft_response]
    D --> E{grade_response}
    E -->|needs revision| D
    E -->|ok| F[human_review]
    F -->|approved| G[send_response]
    F -->|rejected| D
    G --> H[User]
```

## Project Structure

```
src/ossia/
├── agent.py           # Core Deep Agent logic (portable)
├── memory.py          # Postgres checkpointing (portable)
├── tools.py           # KB/search/fallback/grading tools (portable)
├── config.py          # Env-based config with Pydantic v2
├── mcp_tools.py       # MCP client for external tool servers
├── adapters/
│   └── nebius.py      # Nebius-specific endpoint / job helpers
└── prompts/
    └── system.md      # Versioned system prompt

nebius/
├── endpoints/         # vLLM server configs
├── jobs/              # Batch eval Job manifests
├── docker/            # Container images
└── deploy.sh          # One-command Nebius deploy

tests/test_graph.py    # Happy path, KB fallback, human review tests
notebooks/demo.ipynb   # End-to-end walkthrough
```

## Quick Start

1. **Install dependencies**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,notebook]"
```

2. **Configure environment**

```bash
cp .env.example .env
# Edit .env with your API keys and provider/model choice.
```

3. **Run Postgres locally (optional)**

```bash
docker run -d --name ossia-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 postgres:16
```

4. **Run tests**

```bash
pytest tests/test_graph.py -v
```

5. **Run the demo notebook**

```bash
jupyter notebook notebooks/demo.ipynb
```

## Configuration

All provider, model, persistence, and behavior settings are driven by environment variables parsed through Pydantic in `src/ossia/config.py`.

| Variable | Description | Default |
|---|---|---|
| `PROVIDER` | Model provider | `openrouter` |
| `MODEL` | Model identifier | `openai/gpt-4o-mini` |
| `OPENROUTER_API_KEY` | OpenRouter key | — |
| `OPENAI_API_KEY` | OpenAI key | — |
| `ANTHROPIC_API_KEY` | Anthropic key | — |
| `GOOGLE_API_KEY` | Google Gemini key | — |
| `NEBIUS_API_KEY` | Nebius key | — |
| `POSTGRES_URL` | Postgres DSN | `postgresql://postgres:postgres@localhost:5432/ossia` |
| `ENABLE_HUMAN_REVIEW` | Pause before sending | `true` |
| `MAX_REVISION_LOOPS` | Revision cap | `3` |

## MCP Server

Ossia loads additional tools from MCP servers defined in `.mcp.json`. The default configuration installs the LangChain Docs MCP server:

```json
{
  "mcpServers": {
    "langchain-docs": {
      "transport": "http",
      "url": "https://docs.langchain.com/mcp"
    }
  }
}
```

MCP tools are fetched asynchronously when `build_agent_async(include_mcp_tools=True)` is called. If the server is unavailable, the agent falls back to its core tool set.

## Nebius Deployment

```bash
./nebius/deploy.sh
```

The deploy script builds container images, pushes them to Nebius, creates Serverless Endpoints for the candidate/judge models, and submits evaluation Jobs. See `nebius/deploy.sh` for details.

## Cost Estimate

| Component | Unit | Approx. Cost |
|---|---|---|
| vLLM serving on L40S | 1 GPU-hour | ~$1.50 |
| Serverless Endpoint (min=0) | requests | pay-per-use |
| Eval Job | run | ~$2-5 |
| Postgres | managed instance | ~$15/mo |

## License

MIT
