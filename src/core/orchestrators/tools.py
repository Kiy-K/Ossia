"""LangChain tool wrappers for orchestrator pipelines.

These tools expose the orchestrator pipeline JavaScript code to the Deep
Agent. When called, each tool returns a JavaScript code string that the
agent can execute via the interpreter's ``eval`` tool. This two-step
process works because ``task()`` is only available in the JavaScript
interpreter context, not in Python.
"""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class BugfixPipelineInput(BaseModel):
    """Input schema for the bugfix pipeline tool."""

    issue_description: str = Field(description="Description of the bug to investigate and fix.")
    repo: str = Field(default="", description="Owner/repo string, e.g. 'octocat/Hello-World'.")
    issue_number: int | None = Field(default=None, description="Issue or PR number, if known.")


class AuditPipelineInput(BaseModel):
    """Input schema for the audit pipeline tool."""

    target: str = Field(default=".", description="Directory or file path to audit.")
    focus: str = Field(default="general", description="Audit focus: security / performance / style / general.")


class RefactorPipelineInput(BaseModel):
    """Input schema for the refactor pipeline tool."""

    target: str = Field(description="Module or file to refactor.")
    goal: str = Field(description="Description of the desired refactoring.")


def _pipeline_result(name: str, js: str) -> dict:
    """Build the standard pipeline tool return dict.

    Args:
        name: Pipeline name (e.g. ``"bugfix"``, ``"audit"``, ``"refactor"``).
        js: JavaScript code string to execute via ``eval()``.

    Returns:
        Dict with ``status``, ``pipeline``, ``js_code``, and ``instruction``.
    """
    return {
        "status": "ready",
        "pipeline": name,
        "js_code": js,
        "instruction": (
            f"Run this JavaScript code via eval({{ code: js_code }}) "
            f"to execute the {name} pipeline in the interpreter."
        ),
    }


@tool(args_schema=BugfixPipelineInput)
def run_bugfix_pipeline(
    issue_description: str,
    repo: str = "",
    issue_number: int | None = None,
) -> dict:
    """Run the end-to-end automated bug-fix pipeline: diagnose → propose → test.

    Use this when a bug has been reported and you want a fully automated
    investigation and fix cycle. The pipeline delegates to subagents
    (bug-diagnostician → fix-proposer → test-runner) via the interpreter.
    For manual investigation, call subagents individually via the ``task`` tool.

    Returns JavaScript code that the agent should execute via ``eval()``
    in the interpreter.

    Args:
        issue_description: Description of the bug to investigate and fix.
        repo: Optional owner/repo string for context.
        issue_number: Optional issue or PR number.

    Returns:
        Dict with ``js_code`` (str) and ``instruction`` for the agent.
    """
    del repo, issue_number  # used for context but not in JS template yet
    from core.orchestrators.bugfix_pipeline import get_bugfix_pipeline_js
    js = get_bugfix_pipeline_js(issue_description)
    return _pipeline_result("bugfix", js)


@tool(args_schema=AuditPipelineInput)
def run_audit_pipeline(
    target: str = ".",
    focus: str = "general",
) -> dict:
    """Run a comprehensive code audit: research → findings → structured report.

    Use this for automated code quality reviews across a directory or
    file. Supports security, performance, style, and general audit foci.
    For a single-file code review without pipeline overhead, manually
    inspect the file with ``search_codebase`` and ``read_file`` instead.

    Returns JavaScript code that the agent should execute via ``eval()``
    in the interpreter.

    Args:
        target: Directory or file path to audit.
        focus: Audit focus: security / performance / style / general.

    Returns:
        Dict with ``js_code`` (str) and ``instruction`` for the agent.
    """
    from core.orchestrators.audit_pipeline import get_audit_pipeline_js
    js = get_audit_pipeline_js(target=target, focus=focus)
    return _pipeline_result("audit", js)


@tool(args_schema=RefactorPipelineInput)
def run_refactor_pipeline(
    target: str,
    goal: str,
) -> dict:
    """Run an automated code refactoring: research → plan → rewrite → validate.

    Use this when the code works but needs restructuring for clarity,
    performance, or maintainability. Describes the target and desired
    outcome; the pipeline orchestrates the change and runs tests to
    validate nothing is broken.

    Returns JavaScript code that the agent should execute via ``eval()``
    in the interpreter.

    Args:
        target: Module or file to refactor.
        goal: Description of the desired refactoring.

    Returns:
        Dict with ``js_code`` (str) and ``instruction`` for the agent.
    """
    from core.orchestrators.refactor_pipeline import get_refactor_pipeline_js
    js = get_refactor_pipeline_js(target=target, goal=goal)
    return _pipeline_result("refactor", js)
