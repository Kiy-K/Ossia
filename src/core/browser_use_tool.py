"""browser-use wrapper tool for the web-reviewer subagent.

Drives a real Chromium browser via the browser-use SDK. Two modes:
  * Cloud (default for free-tier users with BROWSER_USE_API_KEY): the
    browser-use cloud browser. Anti-bot bypass is built in but only
    on the paid tier.
  * Local (``Settings.browser_use_local=True``): a local Chromium you
    install once via ``uvx browser-use install``. Free, works against
    sites the free-tier cloud browser can't reach. Sends a small set
    of stealth flags to reduce the obvious bot signals.

Free-tier defaults for both modes:
  * ``flash_mode=True`` — skips the agent's internal "thinking" /
    evaluation / next-goal fields. One LLM call per step instead of
    two. Recommended by the SDK docs.
  * Structured output: the subagent can pass ``output_schema`` as a
    ``{field_name: description}`` dict; the tool dynamically builds a
    Pydantic model and uses browser-use's ``output_model_schema`` so
    the LLM extracts into that shape on the final step.

Ponytail: single tool, single counter, single log line. The tool is the
only entry point — there is no separate factory or manager class.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)


class BrowserUseTaskInput(BaseModel):
    """Input schema for the ``browser_use_task`` tool."""

    task: str = Field(
        description=(
            "Natural-language description of what to do in the browser. "
            "Be specific: name the site, the action, and the data to extract. "
            "Example: 'Go to https://github.com/owner/repo, click the latest "
            "release, extract the version number and changelog bullets.'"
        ),
    )
    max_steps: int = Field(
        default=15,
        ge=1,
        le=100,
        description=(
            "Maximum number of browser-use agent steps. Keep low to stay "
            "within free-tier task budgets (one step ≈ one LLM call)."
        ),
    )
    flash_mode: bool = Field(
        default=True,
        description=(
            "Free-tier optimization: skip the agent's internal thinking/"
            "evaluation/next-goal fields and go straight from observation "
            "to action. Set to False for more deliberate navigation when "
            "the task is hard and you have tasks to spare."
        ),
    )
    output_schema: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional structured-output schema. Pass a dict of "
            "{field_name: description} and the agent will extract into "
            "that shape on the final step. Example: "
            "{'version': 'release version string', 'status': 'ok or error'}. "
            "Each value is the field's description; types default to str. "
            "Leave null for free-form text output."
        ),
    )


class BrowserUseTaskOutput(BaseModel):
    """Output schema for the ``browser_use_task`` tool."""

    success: bool = Field(description="True if the browser-use agent finished cleanly.")
    final_result: str = Field(
        default="",
        description="The final extracted content from the browser-use agent.",
    )
    extracted: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured fields extracted by the agent when ``output_schema`` "
            "was provided. Empty dict when no schema was supplied."
        ),
    )
    urls_visited: list[str] = Field(
        default_factory=list,
        description="URLs the agent navigated to.",
    )
    steps_taken: int = Field(default=0, description="Number of agent steps executed.")
    error: str | None = Field(
        default=None,
        description="Error message if the run failed.",
    )


class _InvocationCounter:
    """Thread-safe monotonic counter shared by every tool call.

    Ponytail: stdlib ``threading.Lock`` instead of a class — this exists
    only so the same counter survives multiple calls. If the tool ever
    runs in a multi-process deployment, swap for a Redis INCR; the
    ``increment()`` interface stays identical.
    """

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def increment(self) -> int:
        with self._lock:
            self._n += 1
            return self._n


_INVOCATIONS = _InvocationCounter()


def _check_prerequisites() -> str | None:
    """Return an error message if browser-use cannot run, else ``None``.

    Validation order:
      1. ``browser-use`` package is importable.
      2. Either ``BROWSER_USE_API_KEY`` is set (cloud / paid gateway) OR
         the local-mode path is viable (no API key needed for the
         browser; only the LLM matters, and ``browser_use_llm=main``
         uses the existing provider).

    The agent builder uses the same check to decide whether to wire
    the web-reviewer subagent at all.
    """
    from core.config import get_settings

    try:
        import browser_use  # noqa: F401
    except ImportError:
        return (
            "The `browser-use` package is not installed. Install it with "
            "`uv pip install -e '.[browseruse]'` to enable the web-reviewer."
        )

    settings = get_settings()
    needs_api_key = not settings.browser_use_local or settings.browser_use_llm == "browser-use"
    if needs_api_key:
        import os

        if not os.environ.get("BROWSER_USE_API_KEY") and not os.environ.get(
            "OSSIA_BROWSER_USE_API_KEY"
        ):
            if not settings.browser_use_local:
                return (
                    "BROWSER_USE_API_KEY is not set. Either set it in .env "
                    "(cloud browser path) or set BROWSER_USE_LOCAL=true to "
                    "use a local Chromium (no API key needed for the browser). "
                    "Get a key at https://cloud.browser-use.com/new-api-key."
                )
            # local=False would be caught above; this is the
            # local=True + llm=browser-use branch.
            return (
                "BROWSER_USE_LLM=browser-use requires BROWSER_USE_API_KEY "
                "(paid browser-use account). Set it in .env or switch to "
                "BROWSER_USE_LLM=main to use your main provider's key."
            )
    return None


def _build_output_model(schema: dict[str, str]) -> type[BaseModel]:
    """Build a Pydantic model from ``{field_name: description}``.

    All fields default to ``str`` — the SDK's ``output_model_schema`` is
    happy with string fields and the LLM can stringify numbers, booleans,
    and short lists. If we need typed fields later, accept a richer
    schema (e.g. ``{"field": ("int", "description")}``) and dispatch
    on the type.
    """
    fields: dict[str, tuple[Any, Any]] = {}
    for name, desc in schema.items():
        if not name.isidentifier():
            raise ValueError(f"output_schema field name {name!r} is not a valid Python identifier")
        fields[name] = (str, Field(description=desc))
    return create_model("BrowserUseStructuredOutput", **fields)  # type: ignore[call-overload,no-any-return]


# Conservative list of Chromium flags that reduce the obvious bot
# signals without breaking common sites. Adding a flag here is a
# promise that it doesn't degrade the user-visible experience.
_LOCAL_STEALTH_ARGS: list[str] = [
    # Hides the ``navigator.webdriver === true`` flag — the single
    # biggest tell that the browser is automated.
    "--disable-blink-features=AutomationControlled",
    # Don't run the first-run wizard, don't ask to be the default
    # browser — both surface dialogs that block scripted navigation.
    "--no-first-run",
    "--no-default-browser-check",
    # Useful in Docker / low-shm containers. Harmless elsewhere.
    "--disable-dev-shm-usage",
    # Don't preload the new tab page.
    "--disable-background-networking",
]


def _find_local_chromium() -> str | None:
    """Find the best local Chromium binary for browser-use.

    Lookup order (first hit wins):
      1. ``$PLAYWRIGHT_BROWSERS_PATH`` cache — the Chromium browser-use
         itself downloads via ``uvx browser-use install``.
      2. The default ``~/.cache/ms-playwright/`` cache — a Playwright
         install from another project. Clean binaries, no snap.
      3. The system ``chromium-browser`` / ``google-chrome`` on PATH.

    Returns the absolute path or ``None`` when nothing usable is found.
    The function never raises — failure to find a binary is a
    non-error; the caller reports a clear "run install_browser.py"
    message.
    """
    import os
    import shutil
    from pathlib import Path

    # 1. / 2. Playwright caches. Versions are paths like chromium-1228
    # / chromium_headless_shell-1228; pick the highest version.
    for cache_root in (
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        str(Path.home() / ".cache" / "ms-playwright"),
    ):
        if not cache_root:
            continue
        root = Path(cache_root)
        if not root.is_dir():
            continue
        candidates: list[tuple[tuple[int, ...], Path]] = []
        for sub in root.glob("chromium-*"):
            if not sub.is_dir():
                continue
            for binary in (
                sub / "chrome-linux" / "chrome",
                sub / "chrome-linux64" / "chrome",
            ):
                if binary.is_file() and os.access(binary, os.X_OK):
                    try:
                        version = tuple(int(p) for p in sub.name.split("-")[1].split("."))
                    except (ValueError, IndexError):
                        version = (0,)
                    candidates.append((version, binary))
        if candidates:
            candidates.sort()
            return str(candidates[-1][1])

    # 3. System PATH lookup. Snap chromium works but is slow; we still
    # accept it as a last resort and trust the user knows their env.
    for cmd in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        path = shutil.which(cmd)
        if path:
            return path
    return None


def _build_browser() -> Any:
    """Build a browser configured for the current ``Settings.browser_use_local``.

    * ``browser_use_local=True`` → local Chromium with stealth args
      and the user-configured sandbox / profile dir. No BROWSER_USE_API_KEY
      needed for the browser itself. We pin ``executable_path`` to
      the best local binary so the SDK doesn't fall back to the snap
      chromium (mount-namespace issues) on systems like Ubuntu.
    * ``browser_use_local=False`` → browser-use cloud browser, the
      default for users with a paid account. Requires BROWSER_USE_API_KEY.
    """
    from browser_use import Browser

    from core.config import get_settings

    settings = get_settings()
    if settings.browser_use_local:
        executable_path = _find_local_chromium()
        if executable_path is None:
            raise RuntimeError(
                "No local Chromium found. Run scripts/install_browser.py "
                "to download one (~200MB), or set BROWSER_USE_USER_DATA_DIR "
                "to a path that already contains a chrome binary."
            )
        kwargs: dict[str, Any] = {
            "use_cloud": False,
            "executable_path": executable_path,
            "headless": True,
            "viewport": {"width": 1280, "height": 800},
            "chromium_sandbox": settings.browser_use_chromium_sandbox,
            "args": list(_LOCAL_STEALTH_ARGS),
            # Skip browser-use's bundled extensions (uBlock Origin etc.)
            # on the first run — they expect the Playwright SDK to be
            # present, which we don't have. Disable explicitly; the
            # agent still works without them.
            "enable_default_extensions": False,
        }
        if settings.browser_use_user_data_dir:
            kwargs["user_data_dir"] = settings.browser_use_user_data_dir
        return Browser(**kwargs)

    # Cloud path. Type stubs lag the runtime; the SDK accepts all
    # three kwargs at runtime (see browser-use AGENTS.md "Cloud
    # Browser" section).
    return Browser(  # type: ignore[call-overload]
        use_cloud=True,
        cloud_timeout=15,
        viewport={"width": 1280, "height": 800},
    )


def _build_llm() -> Any:
    """Build the LLM the browser-use Agent will call.

    Defaults to the main agent's chat model via the browser-use SDK's
    own ``ChatOpenAI`` (with the user's provider base_url) — that's
    the only langchain-style chat model the browser-use Agent
    introspects correctly (it reads ``.provider``). For OpenAI-
    compatible providers (openrouter, fireworks, baseten) we pass the
    matching base_url.

    Override with ``Settings.browser_use_llm = \"browser-use\"`` (or
    env ``BROWSER_USE_LLM=browser-use``) to use the browser-use SDK's
    ``ChatBrowserUse`` — only works on a paid browser-use account
    (the free tier returns 403 from the LLM gateway).

    Ponytail: no helper class. If we ever need to support the
    non-OpenAI-compatible providers (anthropic, google), add a small
    dispatch table — same shape, three branches.
    """
    from core.config import Provider, get_settings

    settings = get_settings()
    if settings.browser_use_llm == "browser-use":
        from browser_use.llm import ChatBrowserUse

        return ChatBrowserUse()

    # OpenAI-compatible providers — browser-use's ChatOpenAI + the
    # provider's base_url + the provider's API key.
    openai_compat: dict[Provider, tuple[str, str | None]] = {
        Provider.OPENAI: ("", settings.openai_api_key),
        Provider.OPENROUTER: (
            "https://openrouter.ai/api/v1",
            settings.openrouter_api_key,
        ),
        Provider.FIREWORKS: (
            "https://api.fireworks.ai/inference/v1",
            settings.fireworks_api_key,
        ),
        Provider.BASETEN: (
            "https://inference.baseten.co/v1",
            settings.baseten_api_key,
        ),
    }
    if settings.provider in openai_compat:
        from browser_use.llm import ChatOpenAI  # type: ignore[import-not-found,unused-ignore]

        base_url, api_key = openai_compat[settings.provider]
        if not api_key:
            raise ValueError(f"API key for provider '{settings.provider}' is not configured.")
        return ChatOpenAI(
            model=settings.model,
            api_key=api_key,
            base_url=base_url or None,
        )

    # Anthropic / Google use their own browser-use chat classes.
    if settings.provider == Provider.ANTHROPIC:
        from browser_use.llm import ChatAnthropic

        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the anthropic provider.")
        return ChatAnthropic(model=settings.model, api_key=settings.anthropic_api_key)
    if settings.provider == Provider.GOOGLE:
        from browser_use.llm import ChatGoogle

        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required for the google provider.")
        return ChatGoogle(model=settings.model, api_key=settings.google_api_key)

    raise ValueError(
        f"Unsupported provider for browser-use: {settings.provider}. "
        "Set BROWSER_USE_LLM=browser-use to use the browser-use gateway "
        "(paid account required)."
    )


async def _run_browser_use_task(
    task: str,
    max_steps: int,
    flash_mode: bool = True,
    output_schema: dict[str, str] | None = None,
) -> BrowserUseTaskOutput:
    """Drive a browser-use Agent and translate the result to our schema.

    Imported lazily so the heavy browser-use deps (playwright etc.) are
    only loaded if the user actually calls the tool. We also disable
    browser-use's telemetry at runtime — opt-in by default per the SDK,
    and we don't want phone-home traffic in production.
    """
    import os

    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

    n = _INVOCATIONS.increment()
    logger.warning(
        "browser_use_task invocation #%d (max_steps=%d flash_mode=%s structured=%s task=%r)",
        n,
        max_steps,
        flash_mode,
        bool(output_schema),
        task[:120],
    )

    try:
        from browser_use import Agent

        output_model = _build_output_model(output_schema) if output_schema else None
        agent: Any = Agent(
            task=task,
            llm=_build_llm(),
            browser=_build_browser(),
            max_steps=max_steps,
            flash_mode=flash_mode,
            output_model_schema=output_model,
        )
        history = await agent.run()
    except Exception as exc:  # noqa: BLE001
        logger.exception("browser_use_task #%d failed", n)
        return BrowserUseTaskOutput(success=False, error=f"{type(exc).__name__}: {exc}")

    final = ""
    try:
        final = history.final_result() or ""
    except Exception:  # noqa: BLE001
        # final_result() can blow up if the run had no successful extraction
        # step; fall through with an empty string rather than mask the run.
        final = ""

    extracted: dict[str, Any] = {}
    if output_schema:
        try:
            structured = getattr(history, "structured_output", None)
            if structured is not None:
                if hasattr(structured, "model_dump"):
                    extracted = structured.model_dump()
                elif isinstance(structured, dict):
                    extracted = structured
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not extract structured output: %s", exc)

    return BrowserUseTaskOutput(
        success=bool(history.is_successful()) if history.is_successful() is not None else True,
        final_result=str(final),
        extracted=extracted,
        urls_visited=[u for u in (history.urls() if hasattr(history, "urls") else []) if u],
        steps_taken=history.number_of_steps() if hasattr(history, "number_of_steps") else 0,
    )


@tool(args_schema=BrowserUseTaskInput)
async def browser_use_task(
    task: str,
    max_steps: int = 15,
    flash_mode: bool = True,
    output_schema: dict[str, str] | None = None,
) -> BrowserUseTaskOutput:
    """Drive a real Chromium browser to complete a web task and return the extracted result.

    Use this when the main agent or another subagent needs to interact with a
    live website that cannot be reached with a plain HTTP fetch: SPAs that
    require JavaScript, pages behind login walls, sites with anti-bot
    protection, or tasks that require clicking / filling forms.

    The browser is the browser-use cloud browser (free-tier compatible) and
    the LLM is ``ChatBrowserUse`` (the model the SDK recommends). Each call
    costs one free-tier task, so be precise in the ``task`` description,
    keep ``max_steps`` small, and prefer ``flash_mode=True`` (the default).

    Pass ``output_schema`` as a ``{field_name: description}`` dict to get
    structured extraction on the final step — the result lands in the
    ``extracted`` field of the response.

    Returns:
        The final extracted content (string), the structured fields (when
        ``output_schema`` was provided), the list of URLs visited, and the
        number of steps taken. On failure, ``success=False`` and ``error``
        holds the exception message.
    """
    prereq_error = _check_prerequisites()
    if prereq_error is not None:
        return BrowserUseTaskOutput(success=False, error=prereq_error)
    return await _run_browser_use_task(task, max_steps, flash_mode, output_schema)


def get_browser_use_tool() -> Any:
    """Return the ``browser_use_task`` tool, or ``None`` if it is not usable.

    ``None`` lets the agent builder skip wiring the tool entirely (and
    skip building the web-reviewer subagent) when browser-use is not
    configured. The check is fast and side-effect-free — no browser
    launches, no network calls.
    """
    if _check_prerequisites() is not None:
        return None
    return browser_use_task
