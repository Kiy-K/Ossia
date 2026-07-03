"""Tests for the browser_use_task tool.

These tests cover the *gate* logic (env var, package install, lazy
import) and the invocation counter. They deliberately do NOT spin up a
real browser — that requires the ``browser-use`` package, a network
connection, and a free-tier task. The expensive path is covered by
manual smoke tests against the running server.
"""

from __future__ import annotations

import importlib
import os

import pytest

from core.browser_use_tool import (
    _INVOCATIONS,
    _build_browser,
    _build_output_model,
    _check_prerequisites,
    _InvocationCounter,
    browser_use_task,
    get_browser_use_tool,
)


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    """Reset the shared invocation counter around each test."""
    _INVOCATIONS._n = 0  # noqa: SLF001 — test-only


def test_invocation_counter_is_monotonic() -> None:
    """Sanity check: the counter increments in order, thread-safely."""
    c = _InvocationCounter()
    assert c.increment() == 1
    assert c.increment() == 2
    assert c.increment() == 3


def test_check_prerequisites_reports_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without BROWSER_USE_API_KEY, the tool refuses with a clear error."""
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.delenv("OSSIA_BROWSER_USE_API_KEY", raising=False)
    monkeypatch.setenv("BROWSER_USE_API_KEY", "")
    msg = _check_prerequisites()
    assert msg is not None
    assert "BROWSER_USE_API_KEY" in msg
    assert "browser-use.com" in msg


def test_check_prerequisites_reports_missing_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the package is not installed, the tool refuses with install instructions.

    Sets the API key so the check progresses to the import probe, then
    forces the import to fail by stuffing a bogus module path into
    ``sys.modules`` for ``browser_use``. The check uses ``import`` so we
    have to monkeypatch ``sys.modules`` to simulate the missing package.
    """
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu-test")
    monkeypatch.setitem(__import__("sys").modules, "browser_use", None)
    msg = _check_prerequisites()
    assert msg is not None
    assert "not installed" in msg
    assert "browseruse" in msg or "browser-use" in msg


def test_get_browser_use_tool_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When prerequisites fail, ``get_browser_use_tool`` returns None.

    This is the signal the agent builder uses to skip wiring the
    web-reviewer subagent entirely.
    """
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.setenv("BROWSER_USE_API_KEY", "")
    assert get_browser_use_tool() is None


def test_browser_use_tool_returns_error_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling the tool without prerequisites returns a clean error result, not an exception."""
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.setenv("BROWSER_USE_API_KEY", "")
    import asyncio

    result = asyncio.run(browser_use_task.ainvoke({"task": "test", "max_steps": 1}))
    assert result.success is False
    assert result.error is not None
    assert "BROWSER_USE_API_KEY" in result.error


def test_browser_use_tool_module_reloadable() -> None:
    """The module can be re-imported without side effects beyond the counter.

    Guards against accidental module-level work that would make the
    tool un-importable in tests or in the API server.
    """
    import core.browser_use_tool as mod

    reloaded = importlib.reload(mod)
    assert reloaded.browser_use_task is not None
    assert reloaded.get_browser_use_tool is not None


def _install_fake_browser_use(
    monkeypatch: pytest.MonkeyPatch,
    *,
    browser: type | None = None,
    agent: type | None = None,
    chat_openai: type | None = None,
    chat_browser_use: type | None = None,
) -> None:
    """Install a fake ``browser_use`` package so ``from browser_use.llm import ...`` works.

    Tests stub the SDK by populating ``sys.modules`` with fake modules.
    We need ``browser_use`` to behave as a real package (with a
    ``llm`` submodule) for the new ``browser_use.llm.ChatOpenAI`` /
    ``browser_use.llm.ChatBrowserUse`` import paths. Ponytail: a tiny
    helper instead of repeating this 5x per test.
    """
    import sys
    import types

    bu_pkg = types.ModuleType("browser_use")
    bu_pkg.__path__ = []  # mark as package
    llm_mod = types.ModuleType("browser_use.llm")
    if browser is not None:
        bu_pkg.Browser = browser  # type: ignore[attr-defined]
    if agent is not None:
        bu_pkg.Agent = agent  # type: ignore[attr-defined]
    if chat_openai is not None:
        llm_mod.ChatOpenAI = chat_openai  # type: ignore[attr-defined]
    if chat_browser_use is not None:
        llm_mod.ChatBrowserUse = chat_browser_use  # type: ignore[attr-defined]
    bu_pkg.llm = llm_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", bu_pkg)
    monkeypatch.setitem(sys.modules, "browser_use.llm", llm_mod)


@pytest.mark.asyncio
async def test_run_browser_use_task_handles_missing_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_browser_use_task`` returns a clean failure on ImportError.

    Verifies the run wrapper does not propagate the exception when the
    package is missing — it surfaces as a structured ``success=False``
    result so the subagent can report it.
    """
    monkeypatch.setitem(__import__("sys").modules, "browser_use", None)
    # Bypass the prereq check so we reach the import inside _run_*
    # (the test exercises the inner import path explicitly).
    import contextlib

    # The prereq check would block us; override by going through the
    # public tool with API key set + package missing — but the public
    # tool runs the prereq first. So call _run_* directly with a stub
    # import that raises. Use a fake module via sys.modules that
    # raises ImportError on attribute access.
    class _RaisingModule:
        def __getattr__(self, name: str) -> None:
            raise ImportError("simulated missing browser-use")

    monkeypatch.setitem(__import__("sys").modules, "browser_use", _RaisingModule())
    # Don't pre-set telemetry — the tool uses setdefault, so we just
    # verify it ends up set to "false" after the call.
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)

    # Skip the prereq gate by patching it
    from core import browser_use_tool

    monkeypatch.setattr(browser_use_tool, "_check_prerequisites", lambda: None)
    result = await browser_use_tool._run_browser_use_task("visit example.com", 5)  # noqa: SLF001
    assert result.success is False
    assert result.error is not None
    # Telemetry should have been forced off even though we started with "true"
    assert os.environ.get("ANONYMIZED_TELEMETRY") == "false"
    with contextlib.suppress(KeyError):
        del os.environ["ANONYMIZED_TELEMETRY"]


def test_browser_use_task_input_schema_validates() -> None:
    """Input schema enforces max_steps bounds and accepts new args."""
    from pydantic import ValidationError

    from core.browser_use_tool import BrowserUseTaskInput

    with pytest.raises(ValidationError):
        BrowserUseTaskInput(task="x", max_steps=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BrowserUseTaskInput(task="x", max_steps=1000)  # type: ignore[call-arg]
    inp = BrowserUseTaskInput(task="x", max_steps=5)
    assert inp.max_steps == 5
    # Default max_steps and flash_mode when omitted
    inp_default = BrowserUseTaskInput(task="x")
    assert inp_default.max_steps == 15
    assert inp_default.flash_mode is True
    assert inp_default.output_schema is None
    # Structured output schema round-trips
    inp_schema = BrowserUseTaskInput(
        task="x", output_schema={"version": "the version", "status": "ok or err"}
    )
    assert inp_schema.output_schema == {"version": "the version", "status": "ok or err"}


def test_build_output_model_creates_str_fields() -> None:
    """``_build_output_model`` turns ``{name: description}`` into a Pydantic model."""
    model_cls = _build_output_model({"version": "the version", "status": "ok or error"})
    instance = model_cls(version="1.2.3", status="ok")
    assert instance.model_dump() == {"version": "1.2.3", "status": "ok"}


def test_build_output_model_rejects_bad_field_name() -> None:
    """Field names must be valid Python identifiers (browser-use echoes them as attrs)."""
    with pytest.raises(ValueError, match="not a valid Python identifier"):
        _build_output_model({"1bad-name": "nope"})


def test_build_browser_uses_cloud_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_build_browser`` instantiates Browser with use_cloud=True and the free-tier timeout.

    Stubs out the real Browser class with a recorder so we can assert
    the kwargs without importing playwright / making a network call.
    """
    calls: list[dict] = []

    class _FakeBrowser:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    import sys
    import types

    fake_mod = types.ModuleType("browser_use")
    fake_mod.Browser = _FakeBrowser  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", fake_mod)
    _build_browser()
    assert calls, "Browser() was not called"
    kwargs = calls[0]
    assert kwargs.get("use_cloud") is True
    assert kwargs.get("cloud_timeout") == 15
    assert kwargs.get("viewport") == {"width": 1280, "height": 800}


def test_build_browser_uses_local_chromium_with_stealth_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_browser`` switches to local Chromium + stealth args when browser_use_local=True.

    Verifies the user's no-cost escape hatch from the cloud browser's
    anti-bot limits: local Chromium is invoked with the stealth arg
    list, the configured sandbox flag, and a persistent user_data_dir
    when one is set.
    """
    calls: list[dict] = []

    class _FakeBrowser:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    import sys
    import types

    fake_mod = types.ModuleType("browser_use")
    fake_mod.Browser = _FakeBrowser  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", fake_mod)

    from core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BROWSER_USE_LOCAL", "true")
    monkeypatch.setenv("BROWSER_USE_CHROMIUM_SANDBOX", "false")
    monkeypatch.setenv("BROWSER_USE_USER_DATA_DIR", "/tmp/fake-profile")
    try:
        _build_browser()
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("BROWSER_USE_LOCAL", raising=False)
        monkeypatch.delenv("BROWSER_USE_CHROMIUM_SANDBOX", raising=False)
        monkeypatch.delenv("BROWSER_USE_USER_DATA_DIR", raising=False)

    assert calls, "Browser() was not called"
    kwargs = calls[0]
    assert kwargs.get("use_cloud") is False
    assert kwargs.get("headless") is True
    assert kwargs.get("chromium_sandbox") is False
    assert kwargs.get("user_data_dir") == "/tmp/fake-profile"
    # Stealth args present
    args = kwargs.get("args") or []
    assert "--disable-blink-features=AutomationControlled" in args
    assert "--no-first-run" in args
    assert "--disable-dev-shm-usage" in args


def test_build_browser_local_omits_user_data_dir_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BROWSER_USE_USER_DATA_DIR is unset, the local browser doesn't get a user_data_dir kwarg.

    The default behavior is a per-process temp dir; passing ``None``
    explicitly confuses some Chromium versions.
    """
    calls: list[dict] = []

    class _FakeBrowser:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    import sys
    import types

    fake_mod = types.ModuleType("browser_use")
    fake_mod.Browser = _FakeBrowser  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", fake_mod)

    from core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BROWSER_USE_LOCAL", "true")
    monkeypatch.delenv("BROWSER_USE_USER_DATA_DIR", raising=False)
    try:
        _build_browser()
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("BROWSER_USE_LOCAL", raising=False)

    assert "user_data_dir" not in calls[0]


def test_check_prerequisites_allows_local_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local Chromium + main LLM works WITHOUT BROWSER_USE_API_KEY — the common free path.

    This is the test that matters: a user with no paid account and no
    BROWSER_USE_API_KEY should still be able to wire the web-reviewer.
    """
    from core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BROWSER_USE_LOCAL", "true")
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.delenv("OSSIA_BROWSER_USE_API_KEY", raising=False)
    try:
        result = _check_prerequisites()
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("BROWSER_USE_LOCAL", raising=False)
    assert result is None, f"Expected no prereq error, got: {result}"


def test_check_prerequisites_still_requires_key_for_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud mode still needs BROWSER_USE_API_KEY."""
    from core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("BROWSER_USE_LOCAL", raising=False)
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.delenv("OSSIA_BROWSER_USE_API_KEY", raising=False)
    try:
        result = _check_prerequisites()
    finally:
        get_settings.cache_clear()
    assert result is not None
    assert "BROWSER_USE_API_KEY" in result
    # Should mention the local-mode escape hatch
    assert "BROWSER_USE_LOCAL" in result


def test_check_prerequisites_requires_key_for_browser_use_llm_even_when_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """local=True + llm=browser-use still needs BROWSER_USE_API_KEY (gateway is paid)."""
    from core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BROWSER_USE_LOCAL", "true")
    monkeypatch.setenv("BROWSER_USE_LLM", "browser-use")
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.delenv("OSSIA_BROWSER_USE_API_KEY", raising=False)
    try:
        result = _check_prerequisites()
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("BROWSER_USE_LOCAL", raising=False)
        monkeypatch.delenv("BROWSER_USE_LLM", raising=False)
    assert result is not None
    assert "BROWSER_USE_LLM=browser-use" in result or "browser-use gateway" in result


@pytest.mark.asyncio
async def test_run_browser_use_task_passes_real_browser_and_flash_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_browser_use_task`` wires the cloud browser, flash_mode, and output_model_schema.

    Stubs Agent + Browser to capture the Agent kwargs without running
    the real SDK. The LLM is stubbed at the factory boundary
    (``_build_llm``) so we don't have to mock a chat model. The test
    does NOT consume a free-tier task; it only verifies our wrapper
    plumbs the right values.
    """
    captured: dict[str, object] = {}

    class _FakeBrowser:
        def __init__(self, **kwargs: object) -> None:
            captured["browser_kwargs"] = kwargs

    sentinel_llm = object()  # any non-None marker

    def _fake_build_llm() -> object:
        captured["llm_built"] = True
        return sentinel_llm

    class _FakeHistory:
        def final_result(self) -> str:
            return "ok"

        def is_successful(self) -> bool:
            return True

        def urls(self) -> list[str]:
            return ["https://example.com"]

        def number_of_steps(self) -> int:
            return 1

        @property
        def structured_output(self) -> object:
            return None

    class _FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured["agent_kwargs"] = kwargs

        async def run(self) -> _FakeHistory:
            captured["ran"] = True
            return _FakeHistory()

    _install_fake_browser_use(monkeypatch, browser=_FakeBrowser, agent=_FakeAgent)

    from core import browser_use_tool

    monkeypatch.setattr(browser_use_tool, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(browser_use_tool, "_build_llm", _fake_build_llm)
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)

    await browser_use_tool._run_browser_use_task(  # noqa: SLF001
        task="check the page",
        max_steps=5,
        flash_mode=True,
        output_schema={"version": "the version"},
    )
    assert captured.get("ran") is True
    assert captured.get("llm_built") is True
    agent_kwargs = captured["agent_kwargs"]
    assert agent_kwargs["task"] == "check the page"  # type: ignore[index]
    assert agent_kwargs["max_steps"] == 5  # type: ignore[index]
    assert agent_kwargs["flash_mode"] is True  # type: ignore[index]
    assert agent_kwargs["llm"] is sentinel_llm  # type: ignore[index]
    # The output_model_schema is a Pydantic model with our field
    schema_model = agent_kwargs["output_model_schema"]  # type: ignore[index]
    instance = schema_model(version="1.0")
    assert instance.model_dump() == {"version": "1.0"}
    # The browser was constructed with cloud + viewport
    browser_kwargs = captured["browser_kwargs"]  # type: ignore[index]
    assert browser_kwargs["use_cloud"] is True  # type: ignore[index]
    assert browser_kwargs["cloud_timeout"] == 15  # type: ignore[index]


def test_build_llm_uses_main_chat_model_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_llm`` returns a browser-use ChatOpenAI configured for the main provider.

    For OpenRouter (the test's default) we expect the browser-use SDK's
    ChatOpenAI to be constructed with the openrouter base_url and the
    user's openrouter_api_key. We patch the SDK class to capture the
    kwargs without actually instantiating (which would require the SDK
    and may trigger network calls).
    """
    import sys
    import types

    captured: dict[str, object] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    fake_llm_pkg = types.ModuleType("browser_use.llm")
    fake_llm_pkg.ChatOpenAI = _FakeChatOpenAI  # type: ignore[attr-defined]
    fake_bu = types.ModuleType("browser_use")
    fake_bu.llm = fake_llm_pkg  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", fake_bu)
    monkeypatch.setitem(sys.modules, "browser_use.llm", fake_llm_pkg)

    from core import browser_use_tool
    from core.config import get_settings

    settings = get_settings()
    assert settings.browser_use_llm == "main"
    out = browser_use_tool._build_llm()  # noqa: SLF001
    assert isinstance(out, _FakeChatOpenAI)
    # OpenRouter is the test default — verify the base_url was passed
    assert captured.get("base_url") == "https://openrouter.ai/api/v1"
    assert captured.get("model") == settings.model


def test_build_llm_uses_chat_browser_use_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_llm`` returns ``ChatBrowserUse()`` when browser_use_llm='browser-use'."""
    from core import browser_use_tool

    sentinel = object()
    import sys
    import types

    fake_llm_pkg = types.ModuleType("browser_use.llm")
    fake_llm_pkg.ChatBrowserUse = lambda: sentinel  # type: ignore[attr-defined]
    fake_bu = types.ModuleType("browser_use")
    fake_bu.llm = fake_llm_pkg  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", fake_bu)
    monkeypatch.setitem(sys.modules, "browser_use.llm", fake_llm_pkg)
    monkeypatch.setenv("BROWSER_USE_LLM", "browser-use")
    # Settings are lru_cache'd — clear so the new env var takes effect
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        out = browser_use_tool._build_llm()  # noqa: SLF001
    finally:
        get_settings.cache_clear()
    assert out is sentinel


@pytest.mark.asyncio
async def test_run_browser_use_task_surfaces_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When output_schema is provided, the extracted dict is populated from structured_output."""
    from core import browser_use_tool

    class _FakeBrowser:
        def __init__(self, **kwargs: object) -> None:
            pass

    class _StructuredResult:
        def model_dump(self) -> dict[str, object]:
            return {"version": "1.2.3", "status": "ok"}

    class _FakeHistory:
        def final_result(self) -> str:
            return "1.2.3 ok"

        def is_successful(self) -> bool:
            return True

        def urls(self) -> list[str]:
            return ["https://example.com"]

        def number_of_steps(self) -> int:
            return 2

        @property
        def structured_output(self) -> object:
            return _StructuredResult()

    class _FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def run(self) -> _FakeHistory:
            return _FakeHistory()

    _install_fake_browser_use(monkeypatch, browser=_FakeBrowser, agent=_FakeAgent)
    # Bypass the LLM factory — we don't care which LLM, just that the
    # tool runs end-to-end and surfaces structured_output.
    monkeypatch.setattr(browser_use_tool, "_build_llm", lambda: object())
    monkeypatch.setattr(browser_use_tool, "_check_prerequisites", lambda: None)
    result = await browser_use_tool._run_browser_use_task(  # noqa: SLF001
        task="get the version",
        max_steps=5,
        output_schema={"version": "the version", "status": "ok or error"},
    )
    assert result.success is True
    assert result.extracted == {"version": "1.2.3", "status": "ok"}
    assert result.final_result == "1.2.3 ok"
    assert result.steps_taken == 2
