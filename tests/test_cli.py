"""Tests for the unified ``ossia`` CLI (``core.cli``).

Covers:

- ``_find_bun()`` — Bun binary discovery
- ``_start_server()`` — uvicorn subprocess creation
- ``_start_tui()`` — TUI subprocess creation (env vars passed correctly)
- ``main()`` — full orchestration in all modes

We use ``unittest.mock`` to avoid needing a real server or TUI installation.
The while-True wait loop in ``main()`` is short-circuited by making the
fake subprocesses return a non-None ``poll()`` value after a brief delay.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

# mypy: disable-error-code="import-untyped"

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_env() -> Generator[None, None, None]:
    """Ensure OSSIA_API_KEY is set for all tests (CLI calls require_api_key)."""
    old = os.environ.get("OSSIA_API_KEY")
    os.environ["OSSIA_API_KEY"] = "dev-test-key"
    yield
    if old is None:
        os.environ.pop("OSSIA_API_KEY", None)
    else:
        os.environ["OSSIA_API_KEY"] = old


def _running_process() -> MagicMock:
    """Return a mock subprocess whose poll() returns None indefinitely.

    main()'s wait loop will block until we break out via KeyboardInterrupt
    or a process exits. Use *inside* a patch('core.cli.terminate') context
    to avoid calling the real terminate on the mock.
    """
    proc: MagicMock = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = None
    proc.returncode = None
    return proc


def _exiting_process(exit_code: int = 0, after_calls: int = 3) -> MagicMock:
    """Return a mock subprocess that exits after ``after_calls`` polls."""
    proc: MagicMock = MagicMock(spec=subprocess.Popen)
    proc.poll.side_effect = [None] * after_calls + [exit_code]
    proc.returncode = exit_code
    return proc


# ── _find_bun ────────────────────────────────────────────────────────────────


class TestFindBun:
    """Tests for the _find_bun() helper function."""

    def test_returns_none_when_not_installed(self) -> None:
        """_find_bun returns None when bun is not on PATH and not local."""
        from core.cli import _find_bun

        with patch("core.cli.os.path.isfile", return_value=False), \
             patch("core.cli.shutil.which", return_value=None):
            assert _find_bun() is None

    def test_returns_path_when_on_path(self) -> None:
        """_find_bun returns the path from shutil.which when bun is on PATH."""
        from core.cli import _find_bun

        with patch("core.cli.os.path.isfile", return_value=False), \
             patch("core.cli.shutil.which", return_value="/usr/local/bin/bun"):
            result = _find_bun()
            assert result == "/usr/local/bin/bun"

    def test_prefers_local_bun(self) -> None:
        """_find_bun prefers the project's local bun over global."""
        from core.cli import _TUI_DIR, _find_bun

        local = os.path.join(_TUI_DIR, "node_modules", ".bin", "bun")
        with patch("core.cli.os.path.isfile", return_value=True), \
             patch("core.cli.shutil.which") as mock_which:
            result = _find_bun()
            assert result == local
            mock_which.assert_not_called()


# ── _start_server ────────────────────────────────────────────────────────────


class TestStartServer:
    """Tests for the _start_server() helper function."""

    def test_uses_uvicorn_with_correct_args(self) -> None:
        """_start_server builds the correct uvicorn command."""
        from core.cli import _start_server

        host = "127.0.0.1"
        port = 8000
        env = {"OSSIA_API_KEY": "test", "POSTGRES_URL": ""}

        with patch("core.cli.subprocess.Popen") as mock_popen:
            _start_server(host, port, env)
            mock_popen.assert_called_once()

            # Inspect the command passed to Popen
            args, kwargs = mock_popen.call_args
            cmd = args[0] if args else kwargs["args"]
            assert "-m" in cmd
            assert "uvicorn" in cmd
            assert "core.api:app" in cmd
            assert cmd[cmd.index("--host") + 1] == host
            assert cmd[cmd.index("--port") + 1] == str(port)

    def test_uses_custom_host_port(self) -> None:
        """_start_server passes custom host and port to uvicorn."""
        from core.cli import _start_server

        with patch("core.cli.subprocess.Popen") as mock_popen:
            _start_server("0.0.0.0", 9999, {"KEY": "val"})
            args, kwargs = mock_popen.call_args
            cmd = args[0] if args else kwargs["args"]
            assert "0.0.0.0" in cmd
            assert "9999" in cmd


# ── _start_tui ───────────────────────────────────────────────────────────────


class TestStartTui:
    """Tests for the _start_tui() helper function."""

    def test_passes_correct_env_vars(self) -> None:
        """_start_tui sets OSSIA_API_URL and OSSIA_API_KEY in the subprocess env."""
        from core.cli import _start_tui

        bun = "/usr/local/bin/bun"
        api_url = "http://127.0.0.1:8000"
        api_key = "test-key"
        base_env = {"HOME": "/home/user"}

        with patch("core.cli.subprocess.Popen") as mock_popen:
            _start_tui(bun, api_url, api_key, base_env)
            args, kwargs = mock_popen.call_args
            tui_env = kwargs["env"]
            assert tui_env["OSSIA_API_URL"] == api_url
            assert tui_env["OSSIA_API_KEY"] == api_key

    def test_uses_bun_run_command(self) -> None:
        """_start_tui runs 'bun run src/index.tsx'."""
        from core.cli import _start_tui

        with patch("core.cli.subprocess.Popen") as mock_popen:
            _start_tui("/usr/bin/bun", "http://localhost:8000", "key", {})
            args, kwargs = mock_popen.call_args
            cmd = args[0] if args else kwargs["args"]
            assert cmd == ["/usr/bin/bun", "run", "src/index.tsx"]


# ── main() — server-only mode ────────────────────────────────────────────────


class TestMainServerOnly:
    """main() with --server-only flag."""

    def test_starts_server_and_waits_for_health(self) -> None:
        """--server-only starts the server subprocess and waits for /health."""
        from core.cli import main

        proc = _running_process()

        with patch("core.cli.sys.argv", ["ossia", "--server-only", "--port", "9000"]), \
             patch("core.cli._start_server", return_value=proc) as mock_start, \
             patch("core.cli.wait_for_health") as mock_health, \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            # KeyboardInterrupt to break out of the blocking wait loop
            mock_health.side_effect = KeyboardInterrupt()
            main()

        mock_start.assert_called_once()
        mock_health.assert_called_once()

    def test_uses_custom_startup_timeout(self) -> None:
        """--startup-timeout is passed to wait_for_health."""
        from core.cli import main

        proc = _running_process()

        with patch("core.cli.sys.argv",
                   ["ossia", "--server-only", "--startup-timeout", "5.0"]), \
             patch("core.cli._start_server", return_value=proc), \
             patch("core.cli.wait_for_health") as mock_health, \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            mock_health.side_effect = KeyboardInterrupt()
            main()

        mock_health.assert_called_once_with("http://127.0.0.1:8000", 5.0)

    def test_stops_on_process_exit(self) -> None:
        """When the server exits, main() returns its exit code."""
        from core.cli import main

        proc = _exiting_process(exit_code=3, after_calls=0)

        with patch("core.cli.sys.argv", ["ossia", "--server-only"]), \
             patch("core.cli._start_server", return_value=proc), \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            rc = main()
            assert rc == 3

    def test_calls_terminate_on_shutdown(self) -> None:
        """main() calls terminate() on the server process when it exits."""
        from core.cli import main

        srv = _exiting_process(exit_code=0, after_calls=2)

        with patch("core.cli.sys.argv", ["ossia", "--server-only"]), \
             patch("core.cli._start_server", return_value=srv), \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate") as mock_term, \
             patch("core.cli.os.path.isdir", return_value=True):
            rc = main()
            assert rc == 0

        mock_term.assert_called_once_with(srv, grace_s=5.0)


class TestMainTuiOnly:
    """main() with --tui-only flag."""

    def test_starts_tui_without_server(self) -> None:
        """--tui-only starts the TUI without starting the backend."""
        from core.cli import main

        proc = _exiting_process(exit_code=0, after_calls=1)

        with patch("core.cli.sys.argv", ["ossia", "--tui-only"]), \
             patch("core.cli._start_tui", return_value=proc) as mock_tui, \
             patch("core.cli._find_bun", return_value="/usr/bin/bun"), \
             patch("core.cli.wait_for_health") as mock_health, \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            main()

        mock_tui.assert_called_once()
        mock_health.assert_not_called()


class TestMainBoth:
    """main() in default mode (starts both)."""

    def test_starts_both_server_and_tui(self) -> None:
        """Default mode starts server first, waits for health, then starts TUI."""
        from core.cli import main

        srv = _exiting_process(exit_code=0, after_calls=5)
        tui = _exiting_process(exit_code=0, after_calls=2)

        with patch("core.cli.sys.argv", ["ossia"]), \
             patch("core.cli._start_server", return_value=srv), \
             patch("core.cli._start_tui", return_value=tui) as mock_tui, \
             patch("core.cli._find_bun", return_value="/usr/bin/bun"), \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            main()

        assert mock_tui.called

    def test_sets_postgres_url_empty(self) -> None:
        """The env dict passed to _start_server has POSTGRES_URL=''."""
        from core.cli import main

        proc = _exiting_process(exit_code=0, after_calls=0)

        with patch("core.cli.sys.argv", ["ossia", "--server-only"]), \
             patch("core.cli._start_server") as mock_start, \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            mock_start.return_value = proc
            main()

        mock_start.assert_called_once()
        args, _ = mock_start.call_args
        env = args[2]
        assert env.get("POSTGRES_URL") == ""


class TestMainErrors:
    """main() error handling."""

    def test_returns_1_when_bun_not_found(self) -> None:
        """main() returns 1 when bun is not installed and --server-only is not set."""
        from core.cli import main

        with patch("core.cli.sys.argv", ["ossia"]), \
             patch("core.cli._find_bun", return_value=None), \
             patch("core.cli.os.path.isdir", return_value=True), \
             patch("core.cli._start_server"), \
             patch("core.cli.wait_for_health"):
            rc = main()
            assert rc == 1

    def test_returns_1_when_tui_dir_missing(self) -> None:
        """main() returns 1 when the TUI directory is not found."""
        from core.cli import main

        with patch("core.cli.sys.argv", ["ossia"]), \
             patch("core.cli._find_bun", return_value="/usr/bin/bun"), \
             patch("core.cli.os.path.isdir", return_value=False), \
             patch("core.cli._start_server"), \
             patch("core.cli.wait_for_health"):
            rc = main()
            assert rc == 1

    def test_returns_0_on_keyboard_interrupt(self) -> None:
        """main() returns 0 when KeyboardInterrupt is raised."""
        from core.cli import main

        proc = _running_process()

        with patch("core.cli.sys.argv", ["ossia", "--server-only"]), \
             patch("core.cli._start_server", return_value=proc), \
             patch("core.cli.wait_for_health") as mock_health, \
             patch("core.cli.terminate"):
            mock_health.side_effect = KeyboardInterrupt()
            rc = main()
            assert rc == 0

    def test_exits_on_process_exit(self) -> None:
        """main() returns the exit code of the first process that exits."""
        from core.cli import main

        proc = _exiting_process(exit_code=2, after_calls=0)

        with patch("core.cli.sys.argv", ["ossia", "--server-only"]), \
             patch("core.cli._start_server", return_value=proc), \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"):
            rc = main()
            assert rc == 2


# ── Argument parsing ─────────────────────────────────────────────────────────


class TestArgParsing:
    """Argument parsing edge cases."""

    def test_default_port_is_8000(self) -> None:
        """Default port is 8000 when not specified."""
        from core.cli import main

        proc = _exiting_process(exit_code=0, after_calls=0)

        with patch("core.cli.sys.argv", ["ossia", "--server-only"]), \
             patch("core.cli._start_server") as mock_start, \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            mock_start.return_value = proc
            main()

        args, _ = mock_start.call_args
        assert args[1] == 8000  # port arg

    def test_custom_port(self) -> None:
        """--port overrides the default port."""
        from core.cli import main

        proc = _exiting_process(exit_code=0, after_calls=0)

        with patch("core.cli.sys.argv", ["ossia", "--server-only", "--port", "9000"]), \
             patch("core.cli._start_server") as mock_start, \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            mock_start.return_value = proc
            main()

        args, _ = mock_start.call_args
        assert args[1] == 9000

    def test_custom_host(self) -> None:
        """--host overrides the default host."""
        from core.cli import main

        proc = _exiting_process(exit_code=0, after_calls=0)

        with patch("core.cli.sys.argv",
                   ["ossia", "--server-only", "--host", "0.0.0.0"]), \
             patch("core.cli._start_server") as mock_start, \
             patch("core.cli.wait_for_health"), \
             patch("core.cli.terminate"), \
             patch("core.cli.os.path.isdir", return_value=True):
            mock_start.return_value = proc
            main()

        args, _ = mock_start.call_args
        assert args[0] == "0.0.0.0"
