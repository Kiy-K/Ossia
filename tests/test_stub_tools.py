"""Tests for the formerly-stubbed dev-concierge tools.

Covers the five tools that returned ``[STUB]`` placeholders before
the v0.5 sweep:

  - ``search_codebase``: ripgrep-backed regex search of the project.
  - ``fetch_issue``: GitHub REST issue / PR fetch.
  - ``run_tests``: subprocess test runner with timeout.
  - ``create_pr``: GitHub REST PR open.
  - ``propose_fix``: context-gatherer (the LLM proposes the patch).

All network / subprocess tools are mocked at the boundary so the
tests run offline. The mocks mirror the documented shapes (rg
--json, GitHub API responses, pytest stdout) so the wrapper logic
is exercised end-to-end.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.tools import (
    _github_headers,
    _tokenize_args,
    _validate_github_repo,
    create_pr,
    fetch_issue,
    propose_fix,
    run_tests,
    search_codebase,
)

# ---------------------------------------------------------------------------
# search_codebase — ripgrep
# ---------------------------------------------------------------------------


def _rg_json_event(path: str, line: int, col: int, text: str) -> str:
    """Build a single ``rg --json`` match event line."""
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": text + "\n"},
                "line_number": line,
                "absolute_offset": 0,
                "submatches": [
                    {
                        "match": {"text": text},
                        "start": col - 1,
                        "end": col - 1 + len(text),
                    }
                ],
            },
        }
    )


def test_search_codebase_returns_ripgrep_matches() -> None:
    events = "\n".join(
        [
            _rg_json_event("src/core/foo.py", 10, 5, "def hello():"),
            _rg_json_event("src/core/bar.py", 20, 1, "def hello_variant():"),
        ]
    )
    fake_proc = MagicMock(returncode=0, stdout=events, stderr="")
    with patch("core.tools.subprocess.run", return_value=fake_proc) as run:
        out = search_codebase.invoke({"query": "hello", "path": "src/"})
    matches = dict(out)["matches"]
    assert "src/core/foo.py:10:5: def hello():" in matches
    assert "src/core/bar.py:20:1: def hello_variant():" in matches
    args = run.call_args.args[0]
    assert args[0].endswith("rg")
    assert "--json" in args


def test_search_codebase_no_matches_returns_empty_list() -> None:
    fake_proc = MagicMock(returncode=1, stdout="", stderr="")
    with patch("core.tools.subprocess.run", return_value=fake_proc):
        out = search_codebase.invoke({"query": "missing", "path": "."})
    assert dict(out)["matches"] == []


def test_search_codebase_handles_ripgrep_missing() -> None:
    """If ``rg`` is not on PATH, return empty matches and do not raise."""
    with patch("core.tools.shutil.which", return_value=None):
        out = search_codebase.invoke({"query": "x", "path": "."})
    assert dict(out)["matches"] == []


def test_search_codebase_timeout_returns_empty() -> None:
    def _raise(*_a: Any, **_k: Any) -> None:
        raise subprocess.TimeoutExpired(cmd=["rg"], timeout=30)

    with patch("core.tools.subprocess.run", side_effect=_raise):
        out = search_codebase.invoke({"query": "x", "path": "."})
    assert dict(out)["matches"] == []


def test_search_codebase_caps_at_max_matches() -> None:
    events = "\n".join(_rg_json_event(f"f{i}.py", i, 1, f"match_{i}") for i in range(100))
    fake_proc = MagicMock(returncode=0, stdout=events, stderr="")
    with patch("core.tools.subprocess.run", return_value=fake_proc):
        out = search_codebase.invoke({"query": "match", "path": "."})
    assert len(dict(out)["matches"]) == 50


# ---------------------------------------------------------------------------
# fetch_issue — GitHub REST
# ---------------------------------------------------------------------------


def test_validate_github_repo_accepts_owner_slash_name() -> None:
    assert _validate_github_repo("octocat/Hello-World") == ("octocat", "Hello-World")


def test_validate_github_repo_rejects_other_shapes() -> None:
    assert _validate_github_repo("solo") is None
    assert _validate_github_repo("a/b/c") is None
    assert _validate_github_repo("/leading") is None
    assert _validate_github_repo("trailing/") is None


def test_github_headers_include_token_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-123")
    h = _github_headers()
    assert h["Authorization"] == "Bearer secret-123"


def test_github_headers_omit_token_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    h = _github_headers()
    assert "Authorization" not in h


def test_fetch_issue_returns_normalized_fields() -> None:
    payload = {
        "number": 42,
        "title": "Bug: foo bar",
        "body": "It broke when...",
        "state": "open",
        "html_url": "https://github.com/owner/repo/issues/42",
    }
    resp = MagicMock(status_code=200, json=lambda: payload, text="{}")
    with patch("core.tools.httpx.get", return_value=resp):
        out = fetch_issue.invoke({"repo": "owner/repo", "issue_number": 42})
    d = dict(out)
    assert d["number"] == 42
    assert d["title"] == "Bug: foo bar"
    assert d["body"] == "It broke when..."
    assert d["state"] == "open"
    assert d["url"].endswith("/issues/42")


def test_fetch_issue_404_returns_placeholder() -> None:
    resp = MagicMock(status_code=404, text="not found")
    with patch("core.tools.httpx.get", return_value=resp):
        out = fetch_issue.invoke({"repo": "owner/repo", "issue_number": 1})
    d = dict(out)
    assert d["title"] == "[not found]"
    assert "No issue/PR" in d["body"]


def test_fetch_issue_invalid_repo_returns_placeholder() -> None:
    out = fetch_issue.invoke({"repo": "not-a-repo", "issue_number": 1})
    assert dict(out)["title"] == "[invalid repo]"


def test_fetch_issue_handles_network_error() -> None:
    import httpx as real_httpx

    def _raise(*_a: Any, **_k: Any):
        raise real_httpx.ConnectError("boom")

    with patch("core.tools.httpx.get", side_effect=_raise):
        out = fetch_issue.invoke({"repo": "owner/repo", "issue_number": 1})
    assert dict(out)["title"] == "[network error]"


def test_fetch_issue_5xx_returns_error_envelope() -> None:
    resp = MagicMock(status_code=500, text="internal error")
    with patch("core.tools.httpx.get", return_value=resp):
        out = fetch_issue.invoke({"repo": "owner/repo", "issue_number": 1})
    assert "[http 500]" in dict(out)["title"]


# ---------------------------------------------------------------------------
# run_tests — subprocess
# ---------------------------------------------------------------------------


def test_tokenize_args_splits_shell_style() -> None:
    assert _tokenize_args("pytest -x -q") == ["pytest", "-x", "-q"]
    assert _tokenize_args('pytest -k "my test"') == ["pytest", "-k", "my test"]


def test_tokenize_args_handles_quoted_paths() -> None:
    assert _tokenize_args('pytest "tests/foo bar/"') == ["pytest", "tests/foo bar/"]


def test_run_tests_passes_when_proc_returns_0() -> None:
    fake_proc = MagicMock(returncode=0, stdout="passed", stderr="")
    with patch("core.tools.subprocess.run", return_value=fake_proc) as run:
        out = run_tests.invoke({"path": "tests/", "command": "pytest"})
    d = dict(out)
    assert d["passed"] is True
    assert "passed" in d["output"]
    args = run.call_args.args[0]
    assert args == ["pytest", "tests/"]


def test_run_tests_fails_when_proc_returns_nonzero() -> None:
    fake_proc = MagicMock(returncode=1, stdout="", stderr="FAIL: nope")
    with patch("core.tools.subprocess.run", return_value=fake_proc):
        out = run_tests.invoke({"path": "tests/", "command": "pytest"})
    d = dict(out)
    assert d["passed"] is False
    assert "FAIL: nope" in d["output"]


def test_run_tests_timeout_returns_failure() -> None:
    def _raise(*_a: Any, **_k: Any):
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=300)

    with patch("core.tools.subprocess.run", side_effect=_raise):
        out = run_tests.invoke({"path": "tests/", "command": "pytest"})
    d = dict(out)
    assert d["passed"] is False
    assert "timed out" in d["output"]


def test_run_tests_missing_command_returns_failure() -> None:
    def _raise(*_a: Any, **_k: Any):
        raise FileNotFoundError("pytest: not found")

    with patch("core.tools.subprocess.run", side_effect=_raise):
        out = run_tests.invoke({"path": "tests/", "command": "pytest"})
    d = dict(out)
    assert d["passed"] is False
    assert "not found" in d["output"]


def test_run_tests_truncates_huge_output() -> None:
    big = "x" * 100_000
    fake_proc = MagicMock(returncode=1, stdout=big, stderr="")
    with patch("core.tools.subprocess.run", return_value=fake_proc):
        out = run_tests.invoke({"path": "tests/", "command": "pytest"})
    d = dict(out)
    assert "[truncated]" in d["output"]
    assert len(d["output"]) < 60_000  # cap + a little overhead


# ---------------------------------------------------------------------------
# create_pr — GitHub REST
# ---------------------------------------------------------------------------


def test_create_pr_requires_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    out = create_pr.invoke({"repo": "owner/repo", "title": "t", "head": "feat", "base": "main"})
    assert dict(out)["number"] == 0


def test_create_pr_requires_head() -> None:
    with patch.dict("os.environ", {}, clear=True):
        out = create_pr.invoke({"repo": "owner/repo", "title": "t", "head": "", "base": "main"})
    assert dict(out)["number"] == 0


def test_create_pr_rejects_invalid_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with patch.dict("os.environ", {"GITHUB_TOKEN": "x"}):
        out = create_pr.invoke({"repo": "nope", "title": "t", "head": "feat", "base": "main"})
    assert dict(out)["number"] == 0


def test_create_pr_opens_pr_on_success() -> None:
    payload = {
        "number": 7,
        "html_url": "https://github.com/owner/repo/pull/7",
    }
    resp = MagicMock(status_code=201, json=lambda: payload, text="{}")
    with (
        patch.dict("os.environ", {"GITHUB_TOKEN": "x"}),
        patch("core.tools.httpx.post", return_value=resp) as post,
    ):
        out = create_pr.invoke(
            {
                "repo": "owner/repo",
                "title": "feat: x",
                "body": "b",
                "head": "feat",
                "base": "main",
            }
        )
    d = dict(out)
    assert d["number"] == 7
    assert d["url"].endswith("/pull/7")
    body = post.call_args.kwargs["json"]
    assert body["head"] == "feat"
    assert body["base"] == "main"
    assert body["title"] == "feat: x"


def test_create_pr_4xx_returns_zero() -> None:
    resp = MagicMock(status_code=422, text="validation failed")
    with (
        patch.dict("os.environ", {"GITHUB_TOKEN": "x"}),
        patch("core.tools.httpx.post", return_value=resp),
    ):
        out = create_pr.invoke(
            {
                "repo": "owner/repo",
                "title": "t",
                "head": "feat",
                "base": "main",
            }
        )
    assert dict(out)["number"] == 0


def test_create_pr_handles_network_error() -> None:
    import httpx as real_httpx

    def _raise(*_a: Any, **_k: Any):
        raise real_httpx.ConnectError("boom")

    with (
        patch.dict("os.environ", {"GITHUB_TOKEN": "x"}),
        patch("core.tools.httpx.post", side_effect=_raise),
    ):
        out = create_pr.invoke(
            {
                "repo": "owner/repo",
                "title": "t",
                "head": "feat",
                "base": "main",
            }
        )
    assert dict(out)["number"] == 0


# ---------------------------------------------------------------------------
# propose_fix — context gatherer
# ---------------------------------------------------------------------------


def test_propose_fix_reads_target_file(tmp_path: Path) -> None:
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    return 1\n")
    out = propose_fix.invoke({"issue_description": "foo returns wrong value", "file_path": str(f)})
    d = dict(out)
    assert "Fix proposed" in d["summary"]
    assert "foo returns wrong value" in d["summary"]
    assert "def foo():" in d["context_file"]


def test_propose_fix_no_file_works() -> None:
    out = propose_fix.invoke({"issue_description": "something broke", "file_path": ""})
    d = dict(out)
    assert "the issue" in d["summary"]
    assert d["context_file"] == ""


def test_propose_fix_handles_missing_file(tmp_path: Path) -> None:
    out = propose_fix.invoke({"issue_description": "x", "file_path": str(tmp_path / "nope.py")})
    d = dict(out)
    assert "file " in d["summary"]
    assert d["context_file"] == ""


def test_propose_fix_truncates_huge_file(tmp_path: Path) -> None:
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 50_000)
    out = propose_fix.invoke({"issue_description": "x", "file_path": str(f)})
    d = dict(out)
    assert "[truncated]" in d["context_file"]
    assert len(d["context_file"]) < 250_000
