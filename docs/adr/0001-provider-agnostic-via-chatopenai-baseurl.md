# ADR-0001: Provider-agnostic chat model via `ChatOpenAI` with `base_url`

**Status:** accepted.
**Date:** 2026-06-20.
**Supersedes:** none.

## Context

Ossia needs to work with multiple model providers (OpenAI, Anthropic, Google, Nebius, OpenRouter, Fireworks, Baseten, Ollama) without committing to a single vendor or rewriting the agent when a new one is added. DeepAgents accepts any LangChain `BaseChatModel`, so the question is which concrete type to construct per provider.

## Decision

`create_chat_model(settings)` returns a `BaseChatModel` chosen by `settings.provider`. The OpenAI-compatible providers (OpenAI, OpenRouter, Fireworks, Baseten) all use `langchain_openai.ChatOpenAI` with an optional `base_url`. Anthropic, Google, and Ollama use their own dedicated constructors. No local abstraction layer is introduced.

## Consequences

- **Pro:** zero new dependencies for the three "minor" providers; their URLs and auth are the only differences from OpenAI.
- **Pro:** adding a new OpenAI-compatible provider is a one-line entry in the `openai_like_providers` dict.
- **Con:** features that don't exist in `ChatOpenAI` (e.g. Anthropic prompt caching, Google grounding) are unavailable through the OpenAI-compatible shim. Users who need them must pick the dedicated provider.
- **Con:** provider-specific quirks leak through (e.g. `max_tokens` vs `max_output_tokens`); we normalize at the call site, not in the constructor.

## Alternatives considered

1. **LangChain `init_chat_model` universal shim.** Would have reduced the dispatch table, but it loads the provider's package lazily and provides less control over `base_url` / streaming / `max_tokens` per provider. Deferred; revisit if LangChain ships per-provider defaults we want.
2. **One provider per concrete type with no shared base.** Maximally explicit, but adds a permanent maintenance tax for every new provider.
