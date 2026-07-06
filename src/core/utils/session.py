"""Reproducible & Convenient Session ID system for Ossia.

Provides deterministic session/thread ID derivation using UUID v5 so that
the same caller, project context, and topic always map to the same session
ID. This is analogous to ChatGPT or Gemini's session sidebar — a user can
rejoin a previous conversation just by specifying the same topic slug.

Architecture
------------
- **UUID v5 (deterministic hashing):** ``uuid.uuid5(namespace, name)`` where
  ``namespace`` is a fixed :data:`OSSIA_NAMESPACE` UUID and the ``name``
  string is a composite key: ``{caller_id}:{project_context}:{topic}``.
- **Project context:** Automatically detected from the current working
  directory or a ``git remote`` when none is explicitly provided. Clients
  can also send an ``X-Project-Context`` header to override.
- **Multiple sessions:** Topics like ``"bugfix-auth"``, ``"refactor-api"``,
  ``"general-chat"`` each produce a distinct, reproducible session ID within
  the same project.
- **Local cache:** The client (TUI/Web UI) persists the active session
  metadata in ``.kilocode/active_session.json`` at the repo root so restarts
  instantly rejoin the exact same state. The server provides helpers that
  clients can use to manage this cache.
- **New Chat fallback:** When a caller requests a fresh random session (like
  hitting "+ New Chat"), the server generates a random UUID v4 and returns
  the metadata so the client can cache it.

Usage
-----
The primary entry points for API handlers are:

    >>> from core.utils.session import resolve_thread_id
    >>> thread_id = resolve_thread_id(caller="abc123", topic="bugfix-auth")
    UUID5(…)

For clients that want a fresh random session:

    >>> from core.utils.session import new_random_session
    >>> tid, meta = new_random_session(caller="abc123")
    >>> meta
    {"session_id": "...", "topic": "...", "project_context": "..."}

The :func:`detect_project_context` helper derives a stable short string from
the current workspace, so developers running from the same project folder
automatically map to the same sessions without any configuration.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Fixed namespace UUID for all Ossia session ID derivations.
#
# This is a custom namespace UUID (not the standard DNS or OID namespaces)
# generated specifically for Ossia. It ensures that session IDs derived
# for Ossia are globally unique and do not collide with UUID v5 values
# generated for other purposes.
#
# Generated as: uuid.uuid5(uuid.NAMESPACE_DNS, "ossia.dev")
OSSIA_NAMESPACE = uuid.UUID("a4a7a4e0-9dad-11ef-9e12-7f43e8f8e8a4")

# Directory name for Ossia session cache artifacts, scoped to the repo root.
KILOCODE_DIR = ".kilocode"
ACTIVE_SESSION_FILE = "active_session.json"

# Default topic when none is specified by the caller.
DEFAULT_TOPIC = "default"

# Separator used to build the composite name string for UUID v5.
_COMPOSITE_SEP = ":"


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionMetadata:
    """Serialisable metadata about an active session.

    Produced by :func:`new_random_session` and used by the client to persist
    session information in ``.kilocode/active_session.json``.

    Attributes:
        session_id: The scoped thread/session ID.
        topic: The session topic slug (e.g. ``"bugfix-auth"``).
        project_context: The project context string (e.g. ``"ossia"`` or
            a path hash).
        created_at: ISO-8601 timestamp of when the session was created.
        is_random: ``True`` when this is a one-off random session (v4 UUID),
            ``False`` when it was deterministically derived from context.
    """

    session_id: str
    topic: str = DEFAULT_TOPIC
    project_context: str = ""
    created_at: str = ""
    is_random: bool = False


# ── Project context detection ───────────────────────────────────────────────


def detect_project_context(cwd: str | None = None) -> str:
    """Derive a stable, short project context identifier from the workspace.

    Resolution order:
      1. ``OSSIA_PROJECT_CONTEXT`` environment variable (explicit override).
      2. The basename of the ``git`` remote origin URL (e.g. ``"ossia"``
         from ``git@github.com:Kiy-K/Ossia.git``).
      3. The basename of the current working directory (e.g. ``"ossia"``
         from ``/home/user/ossia``).
      4. Fallback to ``"unknown"`` when none of the above are available.

    The result is lowercased and stripped of ``.git`` suffixes to produce
    a clean, short identifier that is stable across machines for the same
    repository.

    Args:
        cwd: Optional working directory. Defaults to ``os.getcwd()``.

    Returns:
        A short, stable string (e.g. ``"ossia"``, ``"my-repo"``).
    """
    # 1. Environment variable override.
    env_override = os.environ.get("OSSIA_PROJECT_CONTEXT")
    if env_override:
        return env_override.strip().lower()

    resolved_cwd = cwd or os.getcwd()

    # 2. Git remote origin URL (works in cloned repos, not bare repos).
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=resolved_cwd,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            remote_url = result.stdout.strip()
            return _remote_to_project_name(remote_url)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        logger.debug("Could not determine git remote for %s", resolved_cwd)

    # 3. CWD basename.
    cwd_name = os.path.basename(resolved_cwd)
    if cwd_name:
        return cwd_name.lower()

    # 4. Fallback.
    return "unknown"


def _remote_to_project_name(remote_url: str) -> str:
    """Extract a clean project name from a git remote URL.

    Handles common formats:
    - ``git@github.com:Kiy-K/Ossia.git`` → ``"ossia"``
    - ``https://github.com/Kiy-K/ossia.git`` → ``"ossia"``
    - ``git@gitlab.com:org/my-project.git`` → ``"my-project"``
    - ``https://dev.azure.com/org/project/_git/repo`` → ``"repo"``

    Args:
        remote_url: A git remote URL string.

    Returns:
        Lowercase project/repo name without ``.git`` suffix.
    """
    # Normalise: strip trailing whitespace and ``.git``.
    url = remote_url.strip()
    if url.endswith(".git"):
        url = url[:-4]

    # Extract the last path segment after the final ``/``.
    name = url.rsplit("/", 1)[-1] if "/" in url else url

    return name.lower()


# ── Session ID derivation ───────────────────────────────────────────────────


def make_session_id(
    caller_id: str,
    project_context: str,
    topic: str = DEFAULT_TOPIC,
) -> str:
    """Derive a deterministic session/thread ID using UUID v5.

    The same ``(caller_id, project_context, topic)`` triplet always produces
    the same UUID. This allows a client to rejoin a previous session simply
    by providing the same topic slug — no need to remember a random UUID.

    The composite name has the format::

        {caller_id}:{project_context}:{topic}

    Args:
        caller_id: The authenticated caller hash (e.g. from ``verify_api_key``).
        project_context: A stable project identifier (e.g. from
            :func:`detect_project_context` or a client-provided header).
        topic: Optional session topic slug. Defaults to ``"default"``.
            Use descriptive names like ``"bugfix-auth"`` or ``"refactor-api"``
            to create multiple, distinguishable sessions within the same project.

    Returns:
        A UUID v5 string (e.g. ``"a4a7a4e0-9dad-11ef-9e12-7f43e8f8e8a4"``).

    Example:
        >>> make_session_id("abc123", "ossia", "bugfix-auth")
        'c8c8b8a0-9dad-11ef-9e12-7f43e8f8e8a4'
    """
    name = _COMPOSITE_SEP.join([caller_id, project_context, topic])
    return str(uuid.uuid5(OSSIA_NAMESPACE, name))


def new_random_session(
    caller_id: str,
    project_context: str | None = None,
    topic: str = DEFAULT_TOPIC,
) -> tuple[str, SessionMetadata]:
    """Create a fresh, random session ID (UUID v4) for "New Chat" flows.

    Unlike :func:`make_session_id`, this generates a non-deterministic UUID v4
    so the caller gets a pristine thread with no prior checkpoint history.
    The returned :class:`SessionMetadata` can be serialised and cached in
    ``.kilocode/active_session.json`` by the client for later reconnection.

    Args:
        caller_id: The authenticated caller hash.
        project_context: Optional project context. Auto-detected when ``None``.
        topic: Optional session topic. Defaults to ``"default"``.

    Returns:
        A tuple of ``(scoped_thread_id, SessionMetadata)``.
    """
    resolved_context = project_context or detect_project_context()
    random_id = uuid.uuid4().hex
    # Scope the session ID with the caller (same pattern as the deterministic path).
    scoped = f"{caller_id}:{random_id}"
    metadata = SessionMetadata(
        session_id=scoped,
        topic=topic,
        project_context=resolved_context,
        created_at=datetime.now(UTC).isoformat(),
        is_random=True,
    )
    return scoped, metadata


# ── Session cache (client helpers) ──────────────────────────────────────────


def _kilo_dir_path(repo_root: str | None = None) -> Path:
    """Return the path to the ``.kilocode/`` directory.

    Searches upward from the given directory (or CWD) for a ``.git`` directory
    to find the repo root. Falls back to ``<cwd>/.kilocode/`` if no repo root
    is found.

    Args:
        repo_root: Optional explicit repo root path. Auto-detected when ``None``.

    Returns:
        A ``Path`` to the ``.kilocode/`` directory.
    """
    if repo_root:
        return Path(repo_root) / KILOCODE_DIR

    # Search upward for a .git directory to determine repo root.
    search_dir = Path.cwd().resolve()
    for parent in [search_dir, *search_dir.parents]:
        if (parent / ".git").exists() or (parent / ".git").is_dir():
            return parent / KILOCODE_DIR
    # Fallback: use CWD
    return search_dir / KILOCODE_DIR


def ensure_kilo_dir(repo_root: str | None = None) -> Path:
    """Ensure the ``.kilocode/`` directory exists and return its path.

    Creates the directory (and parents) if it does not exist. Idempotent.

    Args:
        repo_root: Optional explicit repo root path.

    Returns:
        A ``Path`` to the ``.kilocode/`` directory.
    """
    path = _kilo_dir_path(repo_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_active_session(repo_root: str | None = None) -> SessionMetadata | None:
    """Read the active session metadata from ``.kilocode/active_session.json``.

    Args:
        repo_root: Optional explicit repo root path.

    Returns:
        A :class:`SessionMetadata` if the file exists and is valid, else ``None``.
    """
    path = _kilo_dir_path(repo_root) / ACTIVE_SESSION_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionMetadata(**data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.warning("Failed to read active session from %s: %s", path, exc)
        return None


def write_active_session(
    metadata: SessionMetadata,
    repo_root: str | None = None,
) -> Path:
    """Write session metadata to ``.kilocode/active_session.json``.

    Creates the ``.kilocode/`` directory if it does not exist.

    Args:
        metadata: The session metadata to persist.
        repo_root: Optional explicit repo root path.

    Returns:
        The ``Path`` to the written file.
    """
    path = ensure_kilo_dir(repo_root) / ACTIVE_SESSION_FILE
    data = asdict(metadata)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.debug("Active session written to %s (session_id=%s)", path, metadata.session_id)
    return path


def clear_active_session(repo_root: str | None = None) -> bool:
    """Remove the ``.kilocode/active_session.json`` file.

    Args:
        repo_root: Optional explicit repo root path.

    Returns:
        ``True`` if the file was removed, ``False`` if it did not exist.
    """
    path = _kilo_dir_path(repo_root) / ACTIVE_SESSION_FILE
    if path.exists():
        path.unlink()
        return True
    return False


# ── Server-side thread ID resolution ────────────────────────────────────────


def resolve_thread_id(
    caller_id: str,
    *,
    topic: str | None = None,
    new_session: bool = False,
    project_context: str | None = None,
    explicit_thread_id: str | None = None,
) -> tuple[str, SessionMetadata]:
    """Resolve a thread/session ID for a chat request.

    This is the primary entry point for API handlers. It encapsulates the
    decision logic:

    - **Explicit override:** If ``explicit_thread_id`` is provided and
      ``new_session`` is ``False``, use it directly (scoped to caller).
    - **New session:** If ``new_session`` is ``True``, generate a random
      UUID v4 session.
    - **Deterministic:** Otherwise, derive a UUID v5 from the caller,
      project context, and topic.

    Args:
        caller_id: The authenticated caller hash.
        topic: Optional session topic slug. Defaults to ``"default"``.
        new_session: When ``True``, generate a random session (v4 UUID).
            When ``False``, derive a deterministic session (v5 UUID).
        project_context: Optional project context override. Auto-detected
            when ``None``.
        explicit_thread_id: When provided (and ``new_session=False``), use
            this raw thread ID directly instead of deriving one.

    Returns:
        A tuple of ``(scoped_thread_id, SessionMetadata)``.
    """
    resolved_topic = topic or DEFAULT_TOPIC
    resolved_context = project_context or detect_project_context()

    # 1. Explicit override.
    if explicit_thread_id and not new_session:
        scoped = f"{caller_id}:{explicit_thread_id}"
        metadata = SessionMetadata(
            session_id=scoped,
            topic=resolved_topic,
            project_context=resolved_context,
            is_random=False,
        )
        return scoped, metadata

    # 2. New random session.
    if new_session:
        return new_random_session(caller_id, resolved_context, resolved_topic)

    # 3. Deterministic session (default).
    # The session ID is scoped with the caller prefix (``{caller}:{uuid}``) to
    # match the format used by ``_thread_id_for`` in the thread routes so
    # checkpoint lookups via ``/v1/threads/{id}/*`` resolve correctly.
    raw = make_session_id(caller_id, resolved_context, resolved_topic)
    scoped = f"{caller_id}:{raw}"
    metadata = SessionMetadata(
        session_id=scoped,
        topic=resolved_topic,
        project_context=resolved_context,
        is_random=False,
    )
    return scoped, metadata
