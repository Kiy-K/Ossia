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
