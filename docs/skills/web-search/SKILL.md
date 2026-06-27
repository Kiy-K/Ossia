---
name: web-search
description: |
  Web search and online research. Use when you need up-to-date external
  information — API docs, package releases, bug reports, vendor pages,
  tutorials, error fixes, or anything not in the local knowledge base.
  Provides best practices for Tavily and DuckDuckGo search tools.
license: MIT
allowed-tools: internet_search fetch_url qna_search search_knowledge_base
metadata:
  priority: medium
  sources: "tavily duckduckgo"
---

# Web Search & Research

## When to use this skill

- **External API docs** — search for up-to-date function signatures, config options, or version differences
- **Package / library releases** — check latest version, changelog, or migration guide
- **Bug reports and fixes** — search GitHub issues, Stack Overflow, or community forums for known errors
- **Vendor pages** — look up pricing, region availability, or feature comparisons
- **Tutorials and guides** — find code examples, setup instructions, or best practices
- **Anything the knowledge base doesn't cover** — fall back to a targeted web search

## Tool selection guide

| Tool | Best for | Output |
|------|----------|--------|
| `internet_search` | Open-ended questions, broad research | Structured results list + optional synthesized answer |
| `fetch_url` | A known page — docs URL, blog post, issue link | Full page content as markdown (or Q&A if `question=` is set) |
| `qna_search` | Quick one-shot answers ("what is X?") | Single string answer, no citations |
| `search_knowledge_base` | Internal KB content (may fall back to DDG) | Structured results with source attribution |

## Best practices

1. **Prefer `fetch_url` for known targets.** If the model already has a URL (from search results, the user, or prior context), use `fetch_url` directly instead of searching again.

2. **Use `qna_search` for simple lookups.** When the question is a straightforward fact ("What is the latest Python version?"), `qna_search` saves tokens by returning just the answer.

3. **Set `question=` on `fetch_url` for focused extraction.** When you have a specific question about a page, passing `question=` makes Tavily extract a grounded answer, which is more efficient than reading the full page.

4. **Chain search → fetch for depth.** When a search result snippet is promising but shallow, call `fetch_url` on the result's URL to get the full content.

5. **Be specific in queries.** Include domain context and desired format: "FastAPI uvicorn multiple workers setting 2024" beats "how to run fastapi".

6. **Respect fallback quality.** When Tavily is unavailable, DuckDuckGo fallbacks are tagged `backend="duckduckgo"`. These are best-effort: lower snippet quality, no synthesized answers, plain text extraction instead of markdown.

## Examples

```
# Broad research → internet_search
internet_search(query="langchain deep agents multimodal documentation 2025", max_results=5)

# Known page → fetch_url
fetch_url(url="https://docs.langchain.com/docs/concepts/multimodal")

# Quick Q&A → qna_search
qna_search(query="What is the context window of GPT-4o?")

# Focused extraction → fetch_url with question
fetch_url(url="https://pypi.org/project/fastapi/", question="What is the latest FastAPI version?")
```
