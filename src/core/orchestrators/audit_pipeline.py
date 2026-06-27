"""Code audit pipeline: research → findings → report.

Provides a JavaScript code template for the interpreter's ``eval`` tool.
Uses the built-in ``task()`` global for subagent dispatch with structured
``responseSchema`` objects.

Pipeline stages:
  1. code-researcher — explore target area
  2. bug-diagnostician — identify issues

Each stage returns a typed JavaScript object via ``responseSchema``,
so no ``JSON.parse`` is needed.
"""

# JSON Schema for research output (code-researcher stage)
_RESEARCH_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Relevant file paths discovered.",
        },
        "architecture": {
            "type": "string",
            "description": "High-level architecture summary.",
        },
        "areas_needing_attention": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Areas that likely need review.",
        },
    },
    "required": ["files"],
}

# JSON Schema for audit findings (bug-diagnostician stage)
_AUDIT_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "category": {"type": "string", "enum": ["security", "performance", "style", "bug", "docs"]},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                    "message": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["file_path", "category", "severity", "message"],
            },
        },
        "summary": {"type": "string", "description": "High-level audit summary."},
    },
    "required": ["findings"],
}

AUDIT_PIPELINE_JS = r"""
const target = `TARGET`;
const focus = `FOCUS`;

// Stage 1: Research the target area
const research = await task({
  description: `Explore the codebase at '${target}' and map its structure.\n` +
    `Focus on: ${focus}.\n` +
    `List key files, modules, entry points. Highlight areas needing attention.`,
  subagentType: "code-researcher",
  responseSchema: RESEARCH_RESULT_SCHEMA
});

// Stage 2: Diagnose issues in the target area
const findings = await task({
  description: `Review this codebase area for issues.\n` +
    `Target: ${target}\n` +
    `Focus: ${focus}\n` +
    `Research context: ${JSON.stringify(research)}\n` +
    `For each issue report: file path, category (security/performance/style/bug/docs),\n` +
    `severity, message, and suggestion.`,
  subagentType: "bug-diagnostician",
  responseSchema: AUDIT_FINDING_SCHEMA
});

// Return structured result
{
  status: "completed",
  findings: findings.findings,
  research_context: research,
  summary: findings.summary || `Audited ${target} (focus: ${focus})`
};
"""


def get_audit_pipeline_js(target: str = ".", focus: str = "general") -> str:
    """Get the audit pipeline JavaScript code with parameters filled in.

    Args:
        target: Directory or file path to audit.
        focus: Audit focus area.

    Returns:
        JavaScript code string ready for ``eval()``, with parameters
        interpolated and schema constants prepended.
    """
    from core.orchestrators.schemas import serialize_schema_js
    escaped_target = target.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    escaped_focus = focus.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    js = AUDIT_PIPELINE_JS
    js = js.replace("TARGET", escaped_target)
    js = js.replace("FOCUS", escaped_focus)
    return (
        f"const RESEARCH_RESULT_SCHEMA = {serialize_schema_js(_RESEARCH_RESULT_SCHEMA)};\n"
        f"const AUDIT_FINDING_SCHEMA = {serialize_schema_js(_AUDIT_FINDING_SCHEMA)};\n\n"
        f"{js}"
    )

