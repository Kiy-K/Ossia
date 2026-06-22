# ADR-0009: Tool surface (Tavily-backed web tools + DuckDuckGo fallback)

**Status:** accepted.
**Date:** 2026-06-22.
**Supersedes:** none (first tools ADR).

## Context

The Deep Agents "Tools" doc lays out the canonical pattern: any
callable — plain function, `@tool`-decorated function, or tool
dict — passed to `create_deep_agent(tools=...)`. Deep Agents infers
the schema from the function signature and docstring.

Ossia's prior tool set was a narrow dev-concierge surface
(`search_codebase`, `search_knowledge_base`, `run_tests`,
`propose_fix`, `fetch_issue`, `create_pr`, `grade_response`,
`send_response`). It had no first-class web search or URL-fetch
primitive; `search_knowledge_base` could fall back to DuckDuckGo
when the local KB was empty, but only as a side effect of a KB
search, not as a standalone tool the model could call with a
specific query and topic.

The user added `TAVILY_API_KEY` to `.env` and asked for new tools
based on the docs. The Tavily-backed trio — `internet_search`,
`fetch_url`, `qna_search` — covers three distinct shapes the docs
call out:

1. **`internet_search`** — structured results with title, URL,
   snippet, and a synthesized `answer` field. Topic-aware
   (`general` / `news` / `finance`).
2. **`fetch_url`** — extract content from a known URL; with an
   optional `question` parameter, the extract is grounded as a
   Q&A. Backed by Tavily's `extract` endpoint.
3. **`qna_search`** — one-shot "what is X?" answer. The model
   doesn't need a list of citations; just a single string.
   Backed by Tavily's `qna_search` endpoint.

The Nebius adapter (`ossia.adapters.nebius`) was deleted in a
prior pass. The `Provider.NEBIUS` enum value still exists for
backward compatibility, but `create_chat_model` now raises a
clear `NotImplementedError` directing callers to
`Provider.OPENROUTER` (or another OpenAI-compatible provider) with
a Nebius-routed model id.

## Decision

Add three Tavily-backed tools in `ossia.tools` and wire them into
`create_core_tools` alongside the existing eight. Follow the
canonical docs pattern: each is a plain function with a Pydantic
`args_schema`, decorated with `@tool`, and infers its schema from
the signature and docstring. The `TavilyClient` is created lazily
inside the tool body (not at module import) so the `tavily-python`
import is paid only when a tool is actually called.

**Configuration** — `tavily_api_key: str | None` on `Settings`,
with `validation_alias=AliasChoices("TAVILY_API_KEY",
"OSSIA_TAVILY_API_KEY")`. The `TAVILY_API_KEY` env var (the
variable the user added) is the primary; `OSSIA_TAVILY_API_KEY`
is the alias for deployment-time configuration.

**Degradation policy**:

- `internet_search` — when `tavily_api_key` is unset, fall through
  to the existing `_ddgs_text` DuckDuckGo path (same one used by
  `search_knowledge_base`). `backend="duckduckgo"` is set on the
  output so the model can see which path served the query.
- `fetch_url` — when `tavily_api_key` is unset, fall through to a
  direct `httpx` + BeautifulSoup text extraction (the canonical
  pattern from the Deep Agents deep-research doc, sans
  `markdownify` which we don't depend on). When a `question` is
  also set, the fallback runs a DDG search and fetches the top hit
  (since the model probably wanted a search result anyway, and
  DDG has no Q&A primitive). `backend="duckduckgo"` is set on
  the output. The "duckduckgo" name is the umbrella tag for the
  DDG-side fallback family (search + search-then-fetch); the
  actual implementation is `httpx` + `bs4` for direct fetches.
- `qna_search` — when `tavily_api_key` is unset, fall through to
  a DDG web search and synthesize an answer from the top snippets
  (the canonical fallback pattern; the answer quality is lower
  than Tavily's grounded Q&A but the model still gets something
  to work with). `backend="duckduckgo"` is set on the output.

**Truncation** — `fetch_url` caps extracted content at 4000 chars
to keep the model's context lean. The full content is still
available upstream; this is just the size we hand back to the
tool result.

**Nebius** — `create_chat_model` raises `NotImplementedError`
with a clear message when `Provider.NEBIUS` is selected. The
`Provider` enum still has `NEBIUS` so existing `.env` values do
not crash at import; the failure happens at agent-build time
where it belongs.

## Consequences

- **Pro:** the model has first-class web search and URL-fetch
  tools, with topic filtering and grounded Q&A. Three new
  capabilities, one new dependency (`tavily-python`).
- **Pro:** every new tool has a working fallback. `internet_search`
  falls back to DDG search, `fetch_url` falls back to a direct
  `httpx` + `bs4` fetch (with DDG-then-fetch when a `question` is
  set), and `qna_search` falls back to a DDG-synthesized answer.
  The agent works offline and in tests without a Tavily key.
- **Pro:** the fallback path is the canonical pattern from the
  Deep Agents deep-research doc (`fetch_webpage_content` with
  `httpx` + `markdownify`), adapted to use `bs4` instead of
  `markdownify` to avoid adding another dependency.
- **Pro:** each new tool has a typed Pydantic input schema and a
  stable output shape; the API surface stays consistent.
- **Con:** the `backend` field is `"duckduckgo"` for the entire
  fallback family, even though the actual implementation is
  `httpx` for `fetch_url` and DDG search for `qna_search`. The
  name is the umbrella tag; the field tells the caller which
  quality class the response is in.
- **Con:** a new dependency (`tavily-python`) is in
  `pyproject.toml`. The repo is already heavy with
  provider SDKs; one more is acceptable.

## Alternatives considered

1. **Use the `langchain-tavily` integration** (`pip install
   langchain-tavily`) instead of the raw `tavily` client. The
   doc's example uses the raw `TavilyClient`; the LangChain
   integration wraps it but adds another indirection. Keep
   raw; if the integration gains something we need (rate
   limit, observability), swap later.
2. **Replace DuckDuckGo with Tavily in `search_knowledge_base`**
   too. Out of scope; the user asked to add Tavily tools, not
   to refactor the existing KB search. `search_knowledge_base`
   is KB-first with a generic web fallback; `internet_search`
   is web-first. Two distinct primitives.
3. **Use `langchain-mcp-adapters` to load Tavily as an MCP
   server**. The Tavily MCP server exists but the doc's
   canonical example is the raw client; MCP would add a
   network round trip for what is otherwise a simple HTTP
   call. Skip unless Tavily MCP becomes the recommended
   path.
4. **Keep the Nebius adapter and add a Nebius-routed Tavily
   search**. Not what the user asked for. The user
   explicitly said "We dont need adapters for Nebius btw, we
   focus on agent use usage more." Keep the
   `NotImplementedError` route so the failure is loud, not
   silent.
