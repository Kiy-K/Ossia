"""Bugfix pipeline: diagnose → propose → test.

This module provides a JavaScript code template that the interpreter's
``eval`` tool can execute. The JavaScript code uses the built-in
``task()`` global (available when ``subagents=True``) to call subagents.

The JavaScript template is exported as ``BUGFIX_PIPELINE_JS``. The agent
can run it via ``eval({ code: BUGFIX_PIPELINE_JS })`` after filling in
the ``ISSUE_DESCRIPTION`` placeholder.

Pipeline stages:
  1. bug-diagnostician — investigate and diagnose
  2. fix-proposer — propose a code change
  3. test-runner — validate the fix

Each stage passes a ``responseSchema`` to ``task()`` so the result is a
structured JavaScript object. Schemas are generated at call time from the
Pydantic models in :mod:`core.orchestrators.schemas` via
:func:`~core.orchestrators.schemas.pydantic_to_js_response_schema`.
"""

BUGFIX_PIPELINE_JS = r"""
const issue = `ISSUE_DESCRIPTION`;

// Stage 1: Diagnose the bug
const diagnosis = await task({
  description: `Investigate this bug and produce a structured diagnosis.\n` +
    `Description: ${issue}\n` +
    `Use search_codebase and search_knowledge_base to find relevant code.\n` +
    `Form a hypothesis and the smallest possible reproduction.`,
  subagentType: "bug-diagnostician",
  responseSchema: BUG_REPORT_SCHEMA
});

// Stage 2: Propose a fix based on the diagnosis
const patch = await task({
  description: `Propose a fix for this diagnosis.\n` +
    `Diagnosis: ${JSON.stringify(diagnosis)}\n` +
    `Draft a minimal change that resolves the root cause.\n` +
    `Include before/after code snippets.`,
  subagentType: "fix-proposer",
  responseSchema: PATCH_PROPOSAL_SCHEMA
});

// Stage 3: Run tests to validate the proposed fix
const testResult = await task({
  description: `Run tests to validate the proposed fix.\n` +
    `Fix: ${JSON.stringify(patch)}\n` +
    `Run the relevant test suite and report pass/fail results.`,
  subagentType: "test-runner",
  responseSchema: TEST_RESULT_SCHEMA
});

// Return structured result
testResult.passed
  ? { status: "passed", diagnosis, patch, test_result: testResult }
  : { status: "failed", diagnosis, patch, test_result: testResult };
"""


def get_bugfix_pipeline_js(issue_description: str) -> str:
    """Get the bugfix pipeline JavaScript code with the issue filled in.

    Args:
        issue_description: The bug description to investigate.

    Returns:
        JavaScript code string ready for ``eval()``, with the issue
        description interpolated and schema constants prepended.
    """
    from core.orchestrators.schemas import (
        BugReport, PatchProposal, TestResult,
        pydantic_to_js_response_schema, serialize_schema_js,
    )
    escaped = issue_description.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    js = BUGFIX_PIPELINE_JS.replace("ISSUE_DESCRIPTION", escaped)
    return (
        f"const BUG_REPORT_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(BugReport))};\n"
        f"const PATCH_PROPOSAL_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(PatchProposal))};\n"
        f"const TEST_RESULT_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(TestResult))};\n\n"
        f"{js}"
    )

