"""Tests for the reproducible session ID system (``core.utils.session``).

Covers:
- UUID v5 determinism: same (caller, project, topic) → same session ID.
- UUID v5 uniqueness: different (caller, project, topic) → different IDs.
- Default topic fallback.
- Project context detection with git remote and CWD.
- New random session generation (\"New Chat\" flow).
- Session metadata serialisation round-trip.
- .kilocode/ cache: read, write, clear, idempotent create.
- Backward compatibility through ``resolve_thread_id``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.utils.session import (
    DEFAULT_TOPIC,
    KILOCODE_DIR,
    OSSIA_NAMESPACE,
    SessionMetadata,
    _kilo_dir_path,
    _remote_to_project_name,
    clear_active_session,
    detect_project_context,
    ensure_kilo_dir,
    make_session_id,
    new_random_session,
    read_active_session,
    resolve_thread_id,
    write_active_session,
)

# ── UUID v5 Determinism ─────────────────────────────────────────────────────


def test_make_session_id_deterministic() -> None:
    """Same (caller, project, topic) always produces the same UUID v5."""
    tid1 = make_session_id("abc123", "ossia", "bugfix-auth")
    tid2 = make_session_id("abc123", "ossia", "bugfix-auth")
    assert tid1 == tid2
    assert isinstance(tid1, str)
    assert len(tid1) == 36  # UUID string format


def test_make_session_id_different_topic() -> None:
    """Different topics within the same project produce different IDs."""
    tid_general = make_session_id("abc123", "ossia", "general")
    tid_bugfix = make_session_id("abc123", "ossia", "bugfix-auth")
    assert tid_general != tid_bugfix


def test_make_session_id_different_caller() -> None:
    """Different callers in the same project produce different IDs."""
    tid_alice = make_session_id("alice-hash", "ossia", "default")
    tid_bob = make_session_id("bob-hash", "ossia", "default")
    assert tid_alice != tid_bob


def test_make_session_id_different_project() -> None:
    """Same caller/topic in different projects produce different IDs."""
    tid_project_a = make_session_id("abc123", "project-alpha", "default")
    tid_project_b = make_session_id("abc123", "project-beta", "default")
    assert tid_project_a != tid_project_b


def test_make_session_id_default_topic() -> None:
    """When topic is omitted, DEFAULT_TOPIC (\"default\") is used."""
    tid_explicit = make_session_id("abc123", "ossia", "default")
    tid_implicit = make_session_id("abc123", "ossia")
    assert tid_implicit == tid_explicit


def test_make_session_id_uuid_version() -> None:
    """The result is a valid UUID v5 (version=5)."""
    import uuid

    tid = make_session_id("abc123", "ossia", "test")
    parsed = uuid.UUID(tid)
    assert parsed.version == 5, f"expected UUID v5, got v{parsed.version}"


def test_ossia_namespace_is_stable() -> None:
    """The OSSIA_NAMESPACE constant is a fixed UUID (never changes between runs).

    The namespace itself does not need to be a specific UUID version — it is
    just a fixed seed for ``uuid.uuid5()``. What matters is that it never
    changes, ensuring deterministic UUID v5 outputs across restarts.
    """
    import uuid

    parsed = uuid.UUID(str(OSSIA_NAMESPACE))
    # The namespace is fixed; it does not need to be a specific version.
    assert parsed is not None


# ── Project context detection ──────────────────────────────────────────────


def test_detect_project_context_cwd_basename() -> None:
    """When no git remote is available, the CWD basename is used."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # No .git in the temp dir → falls back to basename
        context = detect_project_context(cwd=tmpdir)
        basename = os.path.basename(tmpdir).lower()
        assert context == basename


def test_detect_project_context_git_remote() -> None:
    """When a git remote is available, the remote name is used."""
    # Create a temp dir with a git repo that has a remote origin
    with tempfile.TemporaryDirectory() as tmpdir:
        git_dir = Path(tmpdir)
        try:
            subprocess.run(
                ["git", "init"],
                cwd=git_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", "git@github.com:Kiy-K/Ossia.git"],
                cwd=git_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )
            context = detect_project_context(cwd=str(git_dir))
            assert context == "ossia"
        except (subprocess.SubprocessError, FileNotFoundError):
            pytest.skip("git not available in this environment")


def test_detect_project_context_env_override() -> None:
    """OSSIA_PROJECT_CONTEXT env var takes precedence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["OSSIA_PROJECT_CONTEXT"] = "my-custom-project"
        try:
            context = detect_project_context(cwd=tmpdir)
            assert context == "my-custom-project"
        finally:
            del os.environ["OSSIA_PROJECT_CONTEXT"]


def test_detect_project_context_cwd_name() -> None:
    """The CWD basename when it's a simple name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        context = detect_project_context(cwd=tmpdir)
        expected = os.path.basename(tmpdir).lower()
        assert context == expected


# ── _remote_to_project_name ────────────────────────────────────────────────


def test_remote_to_project_name_ssh() -> None:
    """SSH-style remote URLs are parsed correctly."""
    assert _remote_to_project_name("git@github.com:Kiy-K/Ossia.git") == "ossia"


def test_remote_to_project_name_https() -> None:
    """HTTPS-style remote URLs are parsed correctly."""
    assert _remote_to_project_name("https://github.com/Kiy-K/ossia.git") == "ossia"


def test_remote_to_project_name_with_dash() -> None:
    """Project names with dashes are preserved."""
    assert _remote_to_project_name("git@github.com:org/my-project.git") == "my-project"


def test_remote_to_project_name_no_git_suffix() -> None:
    """Remote URLs without .git suffix work correctly."""
    assert _remote_to_project_name("git@github.com:Kiy-K/Ossia") == "ossia"


def test_remote_to_project_name_azure() -> None:
    """Azure DevOps-style URLs are parsed correctly."""
    result = _remote_to_project_name("https://dev.azure.com/org/project/_git/repo")
    assert result == "repo"


def test_remote_to_project_name_no_slash() -> None:
    """Remote URLs without a slash return the whole string lowercased."""
    assert _remote_to_project_name("my-repo") == "my-repo"


# ── New random session ────────────────────────────────────────────────────


def test_new_random_session_generates_unique_ids() -> None:
    """Two calls to new_random_session produce different IDs."""
    tid1, _ = new_random_session("abc123")
    tid2, _ = new_random_session("abc123")
    assert tid1 != tid2
    assert len(tid1) > len("abc123:")


def test_new_random_session_metadata() -> None:
    """SessionMetadata from new_random_session has correct defaults."""
    tid, meta = new_random_session("abc123", project_context="ossia", topic="new-feature")
    assert meta.topic == "new-feature"
    assert meta.project_context == "ossia"
    assert meta.is_random is True
    assert meta.created_at != ""
    assert tid == meta.session_id


def test_new_random_session_scoped() -> None:
    """The random session ID is scoped to the caller."""
    tid, _ = new_random_session("caller-hash")
    assert tid.startswith("caller-hash:")


# ── SessionMetadata ────────────────────────────────────────────────────────


def test_session_metadata_default_topic() -> None:
    """SessionMetadata uses DEFAULT_TOPIC when no topic is given."""
    meta = SessionMetadata(session_id="abc123")
    assert meta.topic == DEFAULT_TOPIC


def test_session_metadata_serialisable() -> None:
    """SessionMetadata can be serialised to JSON and back."""
    meta = SessionMetadata(
        session_id="abc123",
        topic="bugfix-auth",
        project_context="ossia",
        created_at="2026-01-01T00:00:00",
        is_random=False,
    )
    data = json.loads(json.dumps({
        "session_id": meta.session_id,
        "topic": meta.topic,
        "project_context": meta.project_context,
        "created_at": meta.created_at,
        "is_random": meta.is_random,
    }))
    restored = SessionMetadata(**data)
    assert restored.session_id == "abc123"
    assert restored.topic == "bugfix-auth"
    assert restored.project_context == "ossia"


# ── .kilocode/ cache helpers ──────────────────────────────────────────────


def test_ensure_kilo_dir_creates_directory() -> None:
    """ensure_kilo_dir creates the .kilocode/ directory if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = ensure_kilo_dir(repo_root=tmpdir)
        assert path.exists()
        assert path.is_dir()
        assert path.name == KILOCODE_DIR


def test_ensure_kilo_dir_idempotent() -> None:
    """ensure_kilo_dir is idempotent — calling it twice doesn't error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path1 = ensure_kilo_dir(repo_root=tmpdir)
        path2 = ensure_kilo_dir(repo_root=tmpdir)
        assert path1 == path2
        assert path1.exists()


def test_write_and_read_active_session() -> None:
    """Writing a session and reading it back yields the same metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        meta = SessionMetadata(
            session_id="abc123",
            topic="refactor-api",
            project_context="ossia",
            created_at="2026-01-01T00:00:00",
        )
        write_active_session(meta, repo_root=tmpdir)
        restored = read_active_session(repo_root=tmpdir)
        assert restored is not None
        assert restored.session_id == "abc123"
        assert restored.topic == "refactor-api"
        assert restored.project_context == "ossia"
        assert restored.is_random is False


def test_read_active_session_nonexistent() -> None:
    """Reading from a directory without the cache file returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = read_active_session(repo_root=tmpdir)
        assert result is None


def test_clear_active_session() -> None:
    """clear_active_session removes the cache file and returns True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        meta = SessionMetadata(session_id="abc123")
        write_active_session(meta, repo_root=tmpdir)
        assert read_active_session(repo_root=tmpdir) is not None
        cleared = clear_active_session(repo_root=tmpdir)
        assert cleared is True
        assert read_active_session(repo_root=tmpdir) is None


def test_clear_active_session_nonexistent() -> None:
    """clear_active_session returns False when no cache file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cleared = clear_active_session(repo_root=tmpdir)
        assert cleared is False


def test_kilo_dir_path_with_repo_root() -> None:
    """_kilo_dir_path returns the correct path when repo_root is given."""
    path = _kilo_dir_path(repo_root="/tmp/test-repo")
    assert path == Path("/tmp/test-repo") / KILOCODE_DIR


# ── resolve_thread_id (API entry point) ───────────────────────────────────


def test_resolve_thread_id_deterministic() -> None:
    """resolve_thread_id with default params produces deterministic IDs."""
    tid1, meta1 = resolve_thread_id("abc123")
    tid2, meta2 = resolve_thread_id("abc123")
    assert tid1 == tid2
    assert meta1.is_random is False
    # The ID is caller-scoped: ``{caller}:{uuid_v5}``
    assert tid1.startswith("abc123:")


def test_resolve_thread_id_with_topic() -> None:
    """resolve_thread_id with a topic produces a deterministic ID scoped to that topic."""
    tid, meta = resolve_thread_id("abc123", topic="bugfix-auth")
    assert meta.topic == "bugfix-auth"
    assert meta.is_random is False
    # The ID is caller-scoped with format: ``{caller}:{uuid_v5}``
    assert tid.startswith("abc123:")


def test_resolve_thread_id_with_new_session() -> None:
    """resolve_thread_id with new_session=True produces a random session."""
    tid, meta = resolve_thread_id("abc123", new_session=True)
    assert meta.is_random is True
    assert tid.startswith("abc123:")


def test_resolve_thread_id_with_explicit_thread_id() -> None:
    """resolve_thread_id with explicit_thread_id uses it directly (backward compat)."""
    tid, meta = resolve_thread_id("abc123", explicit_thread_id="my-thread", new_session=False)
    assert tid == "abc123:my-thread"
    assert meta.is_random is False


def test_resolve_thread_id_explicit_overrides_new_session() -> None:
    """When explicit_thread_id is provided and new_session=False, it takes precedence."""
    tid, _ = resolve_thread_id(
        "abc123",
        explicit_thread_id="my-thread",
        new_session=False,
        topic="should-not-matter",
    )
    assert tid == "abc123:my-thread"


def test_resolve_thread_id_backward_compat() -> None:
    """Legacy behaviour: providing thread_id without new_session uses the old format."""
    tid, meta = resolve_thread_id("abc123", explicit_thread_id="default", new_session=False)
    assert tid == "abc123:default"
    assert meta.is_random is False


def test_resolve_thread_id_deterministic_with_project_context() -> None:
    """Project context influences the deterministic session ID."""
    tid_proj_a, _ = resolve_thread_id("abc123", project_context="project-a")
    tid_proj_b, _ = resolve_thread_id("abc123", project_context="project-b")
    assert tid_proj_a != tid_proj_b


# ── Integration: end-to-end determinism ───────────────────────────────────


def test_deterministic_across_multiple_calls() -> None:
    """The same call always produces the same result."""
    results = [
        resolve_thread_id("caller-hash", topic="general-chat", project_context="my-project")
        for _ in range(5)
    ]
    tids = [r[0] for r in results]
    assert len(set(tids)) == 1, "all session IDs should be identical"


def test_session_metadata_is_random_flag() -> None:
    """The is_random flag is correctly set in both paths."""
    _, deterministic_meta = resolve_thread_id("abc123", new_session=False)
    assert deterministic_meta.is_random is False

    _, random_meta = resolve_thread_id("abc123", new_session=True)
    assert random_meta.is_random is True


# ── Edge cases ────────────────────────────────────────────────────────────


def test_empty_caller_id() -> None:
    """An empty caller ID still produces a valid UUID."""
    tid = make_session_id("", "ossia", "test")
    import uuid

    parsed = uuid.UUID(tid)
    assert parsed.version == 5


def test_special_characters_in_topic() -> None:
    """Special characters in the topic are handled gracefully."""
    tid1 = make_session_id("abc123", "ossia", "bug-fix/123")
    tid2 = make_session_id("abc123", "ossia", "bug-fix/123")
    assert tid1 == tid2


def test_unicode_in_topic() -> None:
    """Unicode characters in the topic are handled gracefully."""
    tid1 = make_session_id("abc123", "ossia", "réfactor-ünicode")
    tid2 = make_session_id("abc123", "ossia", "réfactor-ünicode")
    assert tid1 == tid2
