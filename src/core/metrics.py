"""Prometheus metrics for Ossia agent middleware.

Custom counters are defined here and wired into middleware components at
construction time so they can be scraped via the /metrics endpoint exposed
by ``prometheus_fastapi_instrumentator``.

Counters
--------
- ``circuit_breaker_opens_total`` — circuit transitions CLOSED → OPEN
- ``circuit_breaker_blocks_total`` — tool calls blocked while circuit is OPEN
- ``circuit_breaker_probes_total`` — HALF_OPEN probe attempts
- ``circuit_breaker_probe_successes_total`` — HALF_OPEN probes that succeeded
- ``llm_requests_total`` — chat invocations that hit a model
- ``llm_tokens_total`` — prompt / completion / total tokens, by provider+model
- ``llm_cost_usd_total`` — approximate USD cost, by provider+model

Each counter carries a ``tool`` label (the tool name that triggered the event).
"""

from __future__ import annotations

from prometheus_client import Counter

# ── Circuit breaker counters ────────────────────────────────────────────────

CIRCUIT_BREAKER_OPENS: Counter = Counter(
    "circuit_breaker_opens_total",
    "Number of times the circuit breaker transitioned from CLOSED to OPEN",
    labelnames=["tool"],
)

CIRCUIT_BREAKER_BLOCKS: Counter = Counter(
    "circuit_breaker_blocks_total",
    "Number of tool calls blocked while the circuit breaker was OPEN",
    labelnames=["tool"],
)

CIRCUIT_BREAKER_PROBES: Counter = Counter(
    "circuit_breaker_probes_total",
    "Number of HALF_OPEN probe attempts",
    labelnames=["tool"],
)

CIRCUIT_BREAKER_PROBE_SUCCESSES: Counter = Counter(
    "circuit_breaker_probe_successes_total",
    "Number of HALF_OPEN probes that succeeded and closed the circuit",
    labelnames=["tool"],
)

# ── LLM usage + cost counters ──────────────────────────────────────────────
# Per-request cost tracking for the chat endpoints. Labels:
#   provider: openai / anthropic / openrouter / google / fireworks / baseten / ollama / unknown
#   model:    the configured model string (e.g. "openai/gpt-4o-mini")
#   kind:     prompt / completion / total (LLM_TOKENS_TOTAL only)

LLM_REQUESTS: Counter = Counter(
    "llm_requests_total",
    "Number of chat invocations that hit a model.",
    labelnames=["provider", "model"],
)

LLM_TOKENS: Counter = Counter(
    "llm_tokens_total",
    "LLM token usage, by phase.",
    labelnames=["provider", "model", "kind"],
)

LLM_COST_USD: Counter = Counter(
    "llm_cost_usd_total",
    "Approximate USD cost (1e-6 cent precision via Counter int).",
    labelnames=["provider", "model"],
)


# Price table: USD per 1K tokens. Keys are the model string the
# user configures (``openai/gpt-4o-mini`` etc.). The price-per-1K
# is multiplied by tokens/1000 and added to the counter as
# micro-USD. Ponytail: only the most common models. Add when a
# production user asks.
PRICE_PER_1K: dict[str, dict[str, float]] = {
    "openai/gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "openai/gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "openai/gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
    "openai/gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "anthropic/claude-sonnet-4-5": {"prompt": 0.003, "completion": 0.015},
    "anthropic/claude-sonnet-4-20250514": {"prompt": 0.003, "completion": 0.015},
    "anthropic/claude-haiku-4-5": {"prompt": 0.0008, "completion": 0.004},
    "google/gemini-2.0-flash": {"prompt": 0.000075, "completion": 0.0003},
}


def estimate_cost_usd_micros(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """Return approximate cost in micro-USD (1e-6 cent).

    Unknown models return 0 — better to under-report than to invent
    a price. The Counter takes an int; micro-USD keeps the
    resolution at 6 decimal places, enough for haiku-tier
    sub-cent responses.
    """
    prices = PRICE_PER_1K.get(model)
    if not prices:
        return 0
    usd = (
        prompt_tokens / 1000.0 * prices.get("prompt", 0.0)
        + completion_tokens / 1000.0 * prices.get("completion", 0.0)
    )
    return int(usd * 1_000_000)
