"""Runtime context schema for the Ossia agent.

Per the Deep Agents "Context engineering" doc, runtime context is
per-invoke configuration that is *not* automatically included in the
model prompt — the model only sees it if a tool, middleware, or other
logic reads it and adds it to messages or the system prompt. Runtime
context **propagates to all subagents**.

We model the per-invoke shape as a frozen dataclass with the fields
we actually inject from the FastAPI layer:

- ``caller`` — short hash of the X-API-Key (the strongest identity
  we have; there is no per-user auth). Same value used to scope
  thread ids in the checkpointer.
- ``request_id`` — UUID for tracing. Echoed on every response via
  the ``X-Request-ID`` header.
- ``provider`` — model provider for this call (default
  ``"openrouter"``). Set by the FastAPI layer; tools can branch on
  this if they need provider-specific behavior.

Future: add ``user_id`` once the FastAPI layer gains per-user auth;
add ``feature_flags`` once we have a real flag system; add
``api_key_overrides`` if a caller wants to inject a per-call
override (the env-var path is the default today).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OssiaContext:
    """Per-invoke configuration passed to the agent.

    Attributes:
        caller: Stable hash of the X-API-Key; scopes thread ids and
            audit log entries. Required.
        request_id: UUID for tracing. Optional; the FastAPI layer
            generates one per request if absent.
        provider: Model provider in use. Defaults to ``"openrouter"``;
            override via the FastAPI layer if a caller requests a
            different provider.

    LangGraph passes extra fields (e.g. ``thread_id``) through context
    during astream_events. ``_extra`` captures them silently so the
    graph doesn't raise on unknown kwargs.
    """

    caller: str = ""
    request_id: str | None = None
    provider: str = "openrouter"
    _extra: dict[str, object] = field(default_factory=dict)

    # Accept any unknown kwargs into _extra so LangGraph's streaming
    # context injection (thread_id, etc.) doesn't raise.
    def __init__(
        self,
        caller: str = "",
        request_id: str | None = None,
        provider: str = "openrouter",
        **extra: object,
    ) -> None:
        object.__setattr__(self, "caller", caller)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "_extra", extra)
