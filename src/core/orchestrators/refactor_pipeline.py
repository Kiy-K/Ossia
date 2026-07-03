"""Refactor pipeline: research → plan → write → validate.

Provides a JavaScript code template for the interpreter's ``eval`` tool.
Uses the built-in ``task()`` global for subagent dispatch with structured
``responseSchema`` objects.

Pipeline stages:
  1. code-researcher — map the target area
  2. fix-proposer (as planner) — create refactoring plan
  3. fix-proposer (as patch-writer) — write code changes
  4. test-runner — validate the refactoring

Each stage passes a ``responseSchema`` to ``task()`` so the result is a
structured JavaScript object. All schemas are generated from the Pydantic
models in :mod:`core.orchestrators.schemas`.
"""

REFACTOR_PIPELINE_JS = r"""
const target = `TARGET`;
const goal = `GOAL`;

// Stage 1: Research the target area
const research = await task({
  description: `Research the codebase for refactoring.\n` +
    `Target: ${target}\n` +
    `Goal: ${goal}\n` +
    `Map the structure, identify dependencies, note simplifying patterns.\n` +
    `List affected files and their roles.`,
  subagentType: "code-researcher",
  responseSchema: RESEARCH_RESULT_SCHEMA
});

// Stage 2: Create refactoring plan
const plan = await task({
  description: `Create a refactoring plan.\n` +
    `Target: ${target}\n` +
    `Goal: ${goal}\n` +
    `Research: ${JSON.stringify(research)}\n` +
    `Output: target, motivation, list of specific changes, estimated impact.`,
  subagentType: "fix-proposer",
  responseSchema: REFACTOR_PLAN_SCHEMA
});

// Stage 3: Write code patches
const patches = await task({
  description: `Write code changes for this refactoring.\n` +
    `Target: ${target}\n` +
    `Goal: ${goal}\n` +
    `Plan: ${JSON.stringify(plan)}\n` +
    `For each change: file path, original code, replacement code.`,
  subagentType: "fix-proposer",
  responseSchema: PATCH_SET_SCHEMA
});

// Stage 4: Validate the refactoring with tests
const validation = await task({
  description: `Run tests to validate the refactoring.\n` +
    `Target: ${target}\n` +
    `Goal: ${goal}\n` +
    `Report: pass/fail, total tests, failures, output.`,
  subagentType: "test-runner",
  responseSchema: VALIDATION_SCHEMA
});

// Return structured result
{
  status: validation.passed ? "completed" : "failed",
  plan,
  patches: patches.patches,
  validation
};
"""


def get_refactor_pipeline_js(target: str, goal: str) -> str:
    """Get the refactor pipeline JavaScript code with parameters filled in.

    Args:
        target: Module or file to refactor.
        goal: Description of the desired refactoring.

    Returns:
        JavaScript code string ready for ``eval()``, with parameters
        interpolated and schema constants prepended.
    """
    from core.orchestrators.schemas import (
        PatchSet,
        RefactorPlan,
        RefactorResearchResult,
        ValidationResult,
        pydantic_to_js_response_schema,
        serialize_schema_js,
    )

    escaped_target = target.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    escaped_goal = goal.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    js = REFACTOR_PIPELINE_JS
    js = js.replace("TARGET", escaped_target)
    js = js.replace("GOAL", escaped_goal)
    return (
        f"const RESEARCH_RESULT_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(RefactorResearchResult))};\n"
        f"const REFACTOR_PLAN_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(RefactorPlan))};\n"
        f"const PATCH_SET_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(PatchSet))};\n"
        f"const VALIDATION_SCHEMA = {serialize_schema_js(pydantic_to_js_response_schema(ValidationResult))};\n\n"
        f"{js}"
    )
