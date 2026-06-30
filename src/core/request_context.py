"""Request-scoped context for traceable logging.

Uses Python 3.11+ ``contextvars`` to attach the current HTTP request's
``request_id`` and ``caller`` to every log record emitted during that
request's lifecycle — without requiring callers to pass ``extra=`` on
every ``logger.info()`` call.

Usage
-----
The :class:`RequestLoggingFilter` is added to the ``core`` logger's handler
in :func:`setup_logging`. The filter reads from ``request_id_var`` and
``caller_var`` and injects them onto each log record.

The FastAPI ``request_id_middleware`` calls :func:`set_request_context`
at the start of each request and :func:`clear_request_context` at the end.
Subagent tasks that run in separate coroutines inherit the context vars
of their parent (contextvars propagates through ``asyncio`` natively).

Example log line produced::

    {"timestamp": "2026-06-28T12:00:00", "level": "INFO",
     "logger": "core.api", "message": "Agent invoked",
     "request_id": "a1b2c3d4e5f6", "caller": "abc123def456"}
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

# ── Context variables ────────────────────────────────────────────────────────

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
"""Current request's unique identifier. Set by the FastAPI middleware."""

caller_var: ContextVar[str | None] = ContextVar("caller", default=None)
"""Current request's authenticated caller hash. Set by the FastAPI middleware."""


def set_request_context(*, request_id: str | None = None, caller: str | None = None) -> None:
    """Set the current request's context variables.

    Called by the FastAPI ``request_id_middleware`` at the start of every
    request. If ``request_id`` is ``None`` the var is left unchanged (it
    defaults to ``None``). This allows the middleware to set request_id
    early (before auth) and caller later (after auth).

    Args:
        request_id: Unique request identifier.
        caller: Authenticated caller hash.
    """
    if request_id is not None:
        request_id_var.set(request_id)
    if caller is not None:
        caller_var.set(caller)


def clear_request_context() -> None:
    """Clear the current request's context variables.

    Called by the FastAPI middleware after each request completes (in
    a ``finally`` block) to prevent context leakage across requests.
    """
    request_id_var.set(None)
    caller_var.set(None)


def get_request_context() -> dict[str, str | None]:
    """Return the current request context as a dict.

    Returns:
        A dict with ``request_id`` and ``caller`` keys.
    """
    return {
        "request_id": request_id_var.get(),
        "caller": caller_var.get(),
    }


# ── Logging filter ───────────────────────────────────────────────────────────


class RequestLoggingFilter(logging.Filter):
    """Logging filter that injects request context onto every log record.

    Attach this filter to the ``core`` logger's handler in ``setup_logging``.
    Every log record passing through the handler will have ``request_id``
    and ``caller`` attributes set from the current context vars (if set).

    This means every ``logger.info()``, ``logger.warning()``, etc. call
    anywhere in the ``core.*`` namespace automatically includes the
    originating request context — no ``extra=`` argument needed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Inject request context onto the log record.

        Reads ``request_id_var`` and ``caller_var`` and sets them as
        attributes on the record. The ``JSONFormatter`` (in
        ``logging_config.py``) picks them up when serializing.

        Args:
            record: The log record to annotate.

        Returns:
            Always ``True`` so the record is never dropped.
        """
        rid = request_id_var.get()
        if rid is not None:
            record.request_id = rid
        clr = caller_var.get()
        if clr is not None:
            record.caller = clr
        return True
