"""Structured output schemas for programmatic subagent orchestrator pipelines.

Each orchestrator module defines a deterministic pipeline that chains
subagent calls via the interpreter's ``task()`` global, passing
structured data between stages.

Schemas here define the input/output shapes for each pipeline stage.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Bug Diagnosis ────────────────────────────────────────────────────────────

class BugReport(BaseModel):
    """Structured output from the bug-diagnostician subagent."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Concise bug title.")
    summary: str = Field(description="One-sentence summary of the bug.")
    root_cause: str = Field(description="Likely root cause (1-2 sentences).")
    reproduction_steps: list[str] = Field(
        default_factory=list,
        description="Numbered reproduction steps.",
    )
    affected_files: list[str] = Field(
        default_factory=list,
        description="Paths to affected source files.",
    )
    severity: str = Field(
        default="medium",
        description="Severity: critical / high / medium / low.",
    )


class PatchProposal(BaseModel):
    """Structured output from the fix-proposer subagent."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="One-line description of the proposed fix.")
    file_path: str = Field(description="Target file to modify.")
    before: str = Field(default="", description="Original code snippet.")
    after: str = Field(default="", description="Replacement code snippet.")
    risk_notes: str = Field(default="", description="Things to double-check.")


class TestResult(BaseModel):
    """Structured output from the test-runner subagent."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(description="Whether all tests passed.")
    total: int = Field(default=0, description="Total test count.")
    passed_count: int = Field(default=0, description="Passed test count.")
    failures: list[str] = Field(
        default_factory=list,
        description="Names of failing tests.",
    )
    output: str = Field(default="", description="Captured test runner output.")


# ── Code Audit ───────────────────────────────────────────────────────────────

class AuditFinding(BaseModel):
    """One finding from a code audit pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Affected file path.")
    line: int | None = Field(default=None, description="Line number, if relevant.")
    category: str = Field(description="Category: security / performance / style / bug / docs.")
    severity: str = Field(description="critical / high / medium / low / info.")
    message: str = Field(description="Human-readable finding description.")
    suggestion: str = Field(default="", description="Suggested remediation.")


class AuditReport(BaseModel):
    """Structured output from an audit pipeline."""

    model_config = ConfigDict(extra="forbid")

    findings: list[AuditFinding] = Field(default_factory=list)
    summary: str = Field(default="", description="High-level audit summary.")
    passed: bool = Field(default=True, description="Whether the audit passed.")


# ── Refactoring ──────────────────────────────────────────────────────────────

class RefactorPlan(BaseModel):
    """Structured output from the refactor-planner stage."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="Module or file to refactor.")
    motivation: str = Field(description="Why this refactoring is needed.")
    changes: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of planned changes with file and description.",
    )
    estimated_impact: str = Field(
        default="low",
        description="low / medium / high.",
    )


class ValidationResult(BaseModel):
    """Structured output from a validation stage."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(description="Whether validation passed.")
    issues: list[str] = Field(default_factory=list, description="Validation issues found.")
    recommendations: list[str] = Field(default_factory=list, description="Recommended fixes.")


# ── Refactor Research ────────────────────────────────────────────────────────

class RefactorResearchResult(BaseModel):
    """Structured output from the code-researcher subagent (refactor pipeline)."""

    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(description="Affected file paths.")
    dependencies: list[str] = Field(
        default_factory=list,
        description="Key dependencies between modules.",
    )
    simplification_opportunities: list[str] = Field(
        default_factory=list,
        description="Areas where simplification is possible.",
    )


# ── Audit Research ───────────────────────────────────────────────────────────

class AuditResearchResult(BaseModel):
    """Structured output from the code-researcher subagent (audit pipeline)."""

    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(description="Relevant file paths discovered.")
    architecture: str = Field(default="", description="High-level architecture summary.")
    areas_needing_attention: list[str] = Field(
        default_factory=list,
        description="Areas that likely need review.",
    )


# ── Patch Set ────────────────────────────────────────────────────────────────

class Patch(BaseModel):
    """A single code patch within a ``PatchSet``."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Target file to modify.")
    before: str = Field(default="", description="Original code snippet.")
    after: str = Field(default="", description="Replacement code snippet.")
    description: str = Field(default="", description="What this patch does.")


class PatchSet(BaseModel):
    """Structured output from the fix-proposer subagent (patch-writer stage)."""

    model_config = ConfigDict(extra="forbid")

    patches: list[Patch] = Field(description="List of code patches to apply.")


# ── Schema serialization helpers ─────────────────────────────────────────────


def pydantic_to_js_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model class to a JSON Schema dict suitable for
    ``task()`` ``responseSchema`` in the JavaScript interpreter.

    Uses Pydantic's ``model_json_schema()`` with ``ref_template`` to inline
    all ``$ref`` references (avoiding ``$defs``), since the ``AutoStrategy``
    response format parser expects a self-contained schema dict.

    Removes top-level ``title`` and ``description`` to keep the schema
    lightweight.

    Args:
        model: A Pydantic ``BaseModel`` subclass.

    Returns:
        A JSON Schema dict (e.g. ``{"type": "object", "properties": ...}``).
    """
    schema = model.model_json_schema(
        by_alias=True,
        ref_template="/definitions/{model}",
        # ``model_json_schema`` with ``ref_template`` still generates
        # ``$defs`` for complex nested types. We traverse and flatten
        # ``$ref`` references into their definitions.
    )
    schema.pop("title", None)
    schema.pop("description", None)
    # Resolve ``$ref`` references inline by traversing the schema tree
    # and replacing ``{"$ref": "/definitions/Foo"}`` with the actual
    # definition from ``$defs``. This gives AutoStrategy a fully
    # self-contained schema.
    definitions = schema.pop("$defs", {})
    if definitions:
        _resolve_refs_in_place(schema, definitions)
    return schema


def _resolve_refs_in_place(node: Any, definitions: dict[str, Any]) -> None:
    """Recursively resolve ``$ref`` references in a JSON Schema tree.

    Mutates ``node`` in place. For each ``{"$ref": "/definitions/Name"}``
    found, replaces it with the corresponding definition dict from
    ``definitions``, then recursively resolves any refs within that
    definition.

    Args:
        node: A JSON Schema node (dict, list, or primitive).
        definitions: The ``$defs`` dict from the parent schema.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref and isinstance(ref, str) and ref.startswith("/definitions/"):
            name = ref.split("/")[-1]
            resolved = definitions.get(name)
            if resolved is not None:
                # Deep copy to avoid mutating the shared definition
                import copy as _copy_module
                replacement = _copy_module.deepcopy(resolved)
                # Remove ref, merge resolved definition into node
                del node["$ref"]
                for k, v in replacement.items():
                    node[k] = v
                # Recursively resolve refs in the merged content
                _resolve_refs_in_place(node, definitions)
                return
        for value in node.values():
            _resolve_refs_in_place(value, definitions)
    elif isinstance(node, list):
        for item in node:
            _resolve_refs_in_place(item, definitions)


def serialize_schema_js(schema: dict[str, Any]) -> str:
    """Serialize a JSON Schema dict to a compact JavaScript object literal.

    The result can be inlined into generated JavaScript code as a const
    definition (e.g. ``const SCHEMA = <result>;``).

    Args:
        schema: A JSON Schema dict.

    Returns:
        Compact JSON string (no whitespace) for embedding in JS source.
    """
    return json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
