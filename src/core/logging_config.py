"""Structured JSON logging configuration for production.

Replaces ad-hoc ``logging.basicConfig`` with a JSON formatter that emits
parseable log records for Loki/Grafana. Every log line includes:

- ``timestamp`` (RFC 3339, UTC)
- ``level`` (INFO, WARNING, ERROR, etc.)
- ``logger`` (module path, e.g. ``core.api``)
- ``message`` (the formatted log message)
- ``request_id`` (when available from the current request context)
- ``caller`` (caller hash when available from the runtime context)
- ``exception`` (traceback, only on ``logger.exception``)

The formatter is designed to be a drop-in replacement for Python's
``logging.Formatter``. Configured via :func:`setup_logging`, which
should be called once at process startup (before any logger is used).

Usage::

    from core.logging_config import setup_logging

    setup_logging(level="INFO")  # call once at startup
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit log records as newline-delimited JSON objects.

    Each record is a single JSON line with the keys described in the module
    docstring. The ``message`` field uses the standard ``%``-style formatting
    from the log call. ``args`` are interpolated into the message string
    before serialization, so the JSON payload is self-contained.

    ``logger.exception`` calls include a ``"exception"`` key with the
    formatted traceback.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string.

        Args:
            record: The log record to format.

        Returns:
            A newline-terminated JSON string.
        """
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        # Build the base payload
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include optional fields when present on the record
        if hasattr(record, "request_id") and getattr(record, "request_id", None):
            payload["request_id"] = record.request_id  # pyright: ignore[reportAttributeAccessIssue]
        if hasattr(record, "caller") and getattr(record, "caller", None):
            payload["caller"] = record.caller  # pyright: ignore[reportAttributeAccessIssue]

        # Include exception info when present (from logger.exception)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = "".join(
                traceback.format_exception(*record.exc_info)
            ).rstrip()

        # Include any extra fields set by the caller
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "message", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName", "request_id", "caller",
            ):
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    *,
    root_logger: str = "core",
    json_output: bool = True,
) -> None:
    """Configure logging to emit structured JSON to stderr.

    Call this once at process startup (before any ``getLogger`` call) to
    ensure all loggers under ``root_logger`` use the JSON formatter.

    When ``json_output`` is ``False``, falls back to the default ``%(level)s``
    format (useful for local development where JSON lines are hard to read).

    The handler is paired with a :class:`RequestLoggingFilter` that
    automatically injects the current request's ``request_id`` and
    ``caller`` onto every log record — no ``extra=`` argument needed
    on individual ``logger.info()`` calls.

    Args:
        level: Log level string (``"DEBUG"``, ``"INFO"``, ``"WARNING"``, etc.)
        root_logger: Logger name to attach the handler to. Defaults to
            ``"core"`` so all ``core.*`` loggers produce JSON output.
        json_output: When ``True`` (default), use the JSON formatter.
            When ``False``, use the standard ``%(levelname)s`` formatter.
    """
    from core.request_context import RequestLoggingFilter

    handler = logging.StreamHandler(sys.stderr)

    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )

    # Attach the request-context filter so every log record automatically
    # inherits the current request_id and caller from the contextvars.
    handler.addFilter(RequestLoggingFilter())

    # Get the root logger (or the scoped root) and attach the handler
    logger = logging.getLogger(root_logger)
    logger.addHandler(handler)
    logger.setLevel(level.upper())

    # Prevent propagation to the root logger to avoid duplicate log lines
    # when the root logger also has a handler.
    logger.propagate = False

    # Silence noisy third-party loggers in production
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("langsmith").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
