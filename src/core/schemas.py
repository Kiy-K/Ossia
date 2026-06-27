"""Pydantic schemas for the unified Ossia HTTP API.

These are the wire-format types for /v1/* routes. Keeping them in a dedicated
module (rather than colocated with handlers) means the OpenAPI spec, the
typed Pydantic surface, and the contract tests all read from one place.

Design notes
------------
- ChatMessage.role uses a closed Literal set; LLM-specific roles are normalized
  to one of these by the handler before serialization.
- StreamEvent is intentionally untyped in `data` so the same envelope can carry
  every langchain `astream_events` v2 event kind; clients narrow by `event`.
- ErrorEnvelope is the single shape returned for every non-2xx response so
  clients can rely on a stable error contract.
- ThreadState.values is `dict[str, Any]` because LangGraph state is open-ended
  per-graph; we do not invent a tighter schema than the graph itself exposes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Artifact(BaseModel):
    """A multimodal artifact attached to a chat turn.

    Artifacts are normalized content blocks (images, documents, audio, video)
    that flow through the agent alongside the user's text message. The
    normalizer in ``api.py`` converts these to LangChain content blocks
    before passing them to the agent.

    One of ``data`` (base64) or ``url`` must be set, but not both.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["image", "document", "audio", "video"] = Field(
        description="Media type of the artifact."
    )
    mime_type: str = Field(description="MIME type, e.g. 'image/png' or 'application/pdf'.")
    data: str | None = Field(
        default=None,
        description="Base64-encoded content. Mutually exclusive with ``url``.",
    )
    url: str | None = Field(
        default=None,
        description="Public URL to the artifact. Mutually exclusive with ``data``.",
    )
    filename: str | None = Field(
        default=None,
        description="Original filename for display / TUI metadata.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata (size_bytes, dimensions, duration, etc.).",
    )


class ArtifactInfo(BaseModel):
    """Lightweight artifact metadata for TUI rendering and thread history.

    Does not carry the raw binary data — only metadata needed to render an
    artifact entry in the TUI (filename, type, analysis state) and to drive
    the analysis lifecycle.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique artifact identifier within the thread.")
    type: Literal["image", "document", "audio", "video"] = Field(description="Media type.")
    filename: str = Field(description="Original filename.")
    mime_type: str = Field(description="MIME type.")
    size_bytes: int | None = Field(default=None, description="File size in bytes.")
    analysis_state: Literal["pending", "analyzing", "completed", "failed"] = Field(
        default="pending",
        description="Current state of multimodal analysis.",
    )
    analysis_result: str | None = Field(
        default=None,
        description="Short text result from multimodal analysis, once completed.",
    )


class ChatRequest(BaseModel):
    """Inbound payload for a single chat turn."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, description="User message for this turn.")
    thread_id: str | None = Field(
        default=None,
        description="Optional thread id. Server scopes it to the authenticated caller.",
    )
    artifacts: list[Artifact] = Field(
        default_factory=list,
        description="Optional multimodal artifacts (images, documents, etc.).",
    )


class ToolCall(BaseModel):
    """A single tool invocation produced by the model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """One message in a thread, normalized for the wire."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "tool", "system"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    artifacts: list[ArtifactInfo] = Field(
        default_factory=list,
        description="Artifact metadata for multimodal content in this message.",
    )


class ChatResponse(BaseModel):
    """Response to a non-streaming chat request."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    messages: list[ChatMessage]


class StreamMessagePayload(BaseModel):
    """A token-level message from ``stream.messages``.

    Mirrors the v3 ``stream.messages`` projection: a token-by-token text
    stream tagged with the source role (``"ai"``, ``"tool"``) and the
    upstream message id.
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    text: str
    id: str | None = None


class StreamToolCallPayload(BaseModel):
    """A tool invocation observed in ``stream.tool_calls``.

    Mirrors the v3 projection. ``output_deltas`` is the per-token stream
    for streaming tool output (only when the tool streams); ``output``
    is the final result once ``completed`` is true.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    completed: bool = False
    error: str | None = None
    output_deltas: list[str] = Field(default_factory=list)


class StreamSubagentPayload(BaseModel):
    """A delegated subagent started or completed.

    Mirrors the v3 ``stream.subagents`` projection. ``path`` is the
    LangGraph namespace path; ``status`` is one of the lifecycle
    strings surfaced by the v3 stream.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    path: list[str] = Field(default_factory=list)


class StreamAsyncTaskPayload(BaseModel):
    """An async subagent task lifecycle event.

    Mirrors the ``async_tasks`` state channel updates. Emitted when an
    async task is started, updated, completes, fails, or is cancelled.
    The ``tasks`` field carries the full snapshot of tracked tasks after
    the event, so clients can render a task registry without tracking
    incremental diffs.
    """

    model_config = ConfigDict(extra="forbid")

    event: Literal[
        "async_task_started",
        "async_task_updated",
        "async_task_completed",
        "async_task_failed",
        "async_task_cancelled",
    ]
    task_id: str
    agent_name: str
    status: str
    tasks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Snapshot of all tracked tasks after the event.",
    )
    error: str | None = Field(
        default=None,
        description="Error detail when event is async_task_failed.",
    )


class StreamValuePayload(BaseModel):
    """A state snapshot from ``stream.values``."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class StreamInterruptPayload(BaseModel):
    """A run pause. ``interrupts`` is the list of action requests the
    reviewer must decide on.
    """

    model_config = ConfigDict(extra="forbid")

    interrupts: list[dict[str, Any]] = Field(default_factory=list)


class StreamArtifactPayload(BaseModel):
    """An artifact lifecycle event in the /v1/chat/stream event stream.

    Emitted when an artifact is received, being parsed, or has completed
    multimodal analysis. The TUI uses ``analysis_state`` and ``event``
    to drive artifact lifecycle display (attachments list, spinner, result).
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(description="Unique artifact identifier.")
    type: Literal["image", "document", "audio", "video"] = Field(description="Media type.")
    filename: str = Field(description="Original filename.")
    event: Literal[
        "artifact_received",
        "image_parsed",
        "multimodal_analysis_started",
        "multimodal_analysis_completed",
    ] = Field(description="Lifecycle event type.")
    analysis_state: Literal["pending", "analyzing", "completed", "failed"] = Field(
        description="Current state of multimodal analysis."
    )
    summary: str | None = Field(
        default=None,
        description="Analysis result summary, set when event is multimodal_analysis_completed.",
    )


class StreamPipelinePayload(BaseModel):
    """A pipeline lifecycle event in the /v1/chat/stream event stream.

    Emitted when an orchestrator pipeline (bugfix, audit, refactor) starts,
    advances through a step, completes, or fails. The TUI can use these
    events to render expandable pipeline trees under the coordinator.

    Pipeline steps follow a deterministic flow:
      - Bugfix:  bug-diagnostician → fix-proposer → test-runner
      - Audit:   code-researcher → bug-diagnostician
      - Refactor: code-researcher → fix-proposer → fix-proposer → test-runner

    Each step event carries the step's ``name`` and ``status`` so clients
    can render a progress indicator. Completed steps include a ``result``
    summary; failed steps include an ``error`` detail.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(description="Unique identifier for this pipeline run.")
    pipeline_type: Literal["bugfix", "audit", "refactor"] = Field(description="Which pipeline is running.")
    event: Literal[
        "pipeline_started",
        "pipeline_step_started",
        "pipeline_step_completed",
        "pipeline_step_failed",
        "pipeline_completed",
    ] = Field(description="Pipeline lifecycle event type.")
    step_name: str | None = Field(
        default=None,
        description="Step subagent name (e.g. 'bug-diagnostician'). Set on step events.",
    )
    step_index: int | None = Field(
        default=None,
        description="Zero-based step index within the pipeline. Set on step events.",
    )
    total_steps: int = Field(
        default=0,
        description="Total number of steps in this pipeline.",
    )
    status: Literal["running", "completed", "failed"] = Field(
        description="Current status of the pipeline or step.",
    )
    result: str | None = Field(
        default=None,
        description="Short result summary for completed steps/pipelines.",
    )
    error: str | None = Field(
        default=None,
        description="Error detail when event is pipeline_step_failed.",
    )


class StreamCompletePayload(BaseModel):
    """Sent exactly once, as the final event on the stream.

    ``output`` is the final state; ``interrupted`` is true when the run
    paused on a human-review interrupt and the client should call
    ``POST /v1/threads/{id}/resume`` to continue.
    """

    model_config = ConfigDict(extra="forbid")

    output: dict[str, Any] = Field(default_factory=dict)
    interrupted: bool = False


class StreamProtocolPayload(BaseModel):
    """Escape hatch: a raw v3 protocol event, surfaced verbatim.

    Most clients should not need this; it exists for clients that want
    full fidelity (e.g. namespace-aware multi-agent UI) without
    re-implementing the v3 mux.
    """

    model_config = ConfigDict(extra="forbid")

    method: str
    namespace: list[str] = Field(default_factory=list)
    data: Any = None


class StreamEvent(BaseModel):
    """One Server-Sent Event payload on ``/v1/chat/stream``.

    The SSE ``event:`` field is the value of ``kind``; the ``data:`` field
    is the JSON-serialized envelope. Clients can subscribe to a specific
    kind via ``EventSource.addEventListener(kind, ...)`` and ignore the
    rest.

    Kind reference:
        - ``message``:    ``data`` is a :class:`StreamMessagePayload`.
        - ``tool_call``:  ``data`` is a :class:`StreamToolCallPayload`.
        - ``subagent``:   ``data`` is a :class:`StreamSubagentPayload`.
        - ``value``:      ``data`` is a :class:`StreamValuePayload`.
        - ``artifact``:   ``data`` is a :class:`StreamArtifactPayload`.
        - ``interrupt``:  ``data`` is a :class:`StreamInterruptPayload`.
        - ``complete``:   ``data`` is a :class:`StreamCompletePayload`.
          Sent exactly once, as the final event.
        - ``protocol``:   ``data`` is a :class:`StreamProtocolPayload`.
          Raw v3 protocol event; escape hatch for full fidelity.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "message", "tool_call", "subagent", "value", "artifact", "interrupt", "complete", "protocol",
        "async_task", "pipeline",
    ]
    seq: int | None = None
    data: dict[str, Any]


class ResumeDecision(BaseModel):
    """A single reviewer's decision on a pending action request.

    Mirrors the DeepAgents HITL ``Command(resume={"decisions": [...]})`` shape;
    one decision per ``action_request`` surfaced via ``result.interrupts``.

    Decision types:
        - ``approve``: execute the tool call as-is.
        - ``edit``:    replace the action with ``edited_action`` (must include
          ``name`` and ``args``) and execute that instead.
        - ``reject``:  do not execute; the optional ``message`` is forwarded
          to the agent as feedback.
        - ``respond``: do not execute; the optional ``message`` is returned
          to the agent as a successful tool result (use only when the tool
          is intentionally a placeholder for human input).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["approve", "edit", "reject", "respond"]
    edited_action: dict[str, Any] | None = Field(
        default=None,
        description="Required when type='edit'; the replacement action (name, args).",
    )
    message: str | None = Field(
        default=None,
        description=(
            "Optional reviewer message. For type='reject' it is added to the "
            "conversation as feedback. For type='respond' it is returned as the "
            "tool result. Ignored for type='approve' and (when omitted) for "
            "type='edit'."
        ),
    )


class ResumeRequest(BaseModel):
    """Inbound payload for resuming a thread paused on a human-review interrupt.

    Maps to ``agent.invoke(Command(resume={"decisions": [...]}), config, version="v2")``.
    Decisions are applied to the pending ``action_requests`` in the order
    they were surfaced by the run that produced the interrupt.
    """

    model_config = ConfigDict(extra="forbid")

    decisions: list[ResumeDecision] = Field(
        min_length=1,
        description="One decision per pending action request, in the order surfaced.",
    )


class ResumeResponse(BaseModel):
    """Result of resuming a thread. May itself contain fresh interrupts."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    messages: list[ChatMessage]
    interrupted: bool = Field(
        default=False,
        description="True when the resumed run paused on a new interrupt.",
    )
    feedback: str | None = Field(
        default=None,
        description="Optional reviewer feedback forwarded to the agent on reject."
    )


class ThreadEventsResponse(BaseModel):
    """A thread's buffered normalized event stream."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized OssiaEvent dicts in emit order.",
    )
    count: int = Field(description="Number of events in the response.")


class ThreadHistoryResponse(BaseModel):
    """A thread's full message history."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    messages: list[ChatMessage]


class ThreadStateResponse(BaseModel):
    """A thread's current graph state (values + next nodes)."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    values: dict[str, Any]
    next: list[str]
    config: dict[str, Any] = Field(default_factory=dict)


class ToolInfo(BaseModel):
    """A tool the running agent has loaded."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    source: Literal["core", "mcp"]
    server: str | None = Field(
        default=None,
        description="MCP server name, set only when source='mcp'.",
    )


class ToolListResponse(BaseModel):
    """Response payload for GET /v1/tools."""

    model_config = ConfigDict(extra="forbid")

    tools: list[ToolInfo]


class HealthResponse(BaseModel):
    """Response payload for GET /health."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class CheckResult(BaseModel):
    """One named check inside an audit section."""

    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    detail: str | None = None


class AuditSection(BaseModel):
    """A grouped batch of checks (memory / process / fix-verifications / runtime / langsmith)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    checks: list[CheckResult]
    ok: bool


class AuditReport(BaseModel):
    """Top-level audit report."""

    model_config = ConfigDict(extra="forbid")

    sections: list[AuditSection]
    ok: bool


class EvalQueryResult(BaseModel):
    """One graded query in an eval report."""

    model_config = ConfigDict(extra="forbid")

    id: str
    expected_intent: str
    routed_intents: list[str] = Field(default_factory=list)
    intent_match: bool = False
    passed: bool
    missing_terms: list[str] = Field(default_factory=list)
    answer_preview: str = ""


class EvalRequest(BaseModel):
    """Inbound payload for POST /v1/eval.

    The golden dataset is hardcoded to ``tests/golden_dataset.json``
    — no user-controlled path reaches the filesystem. Only the pass
    rate threshold is configurable from the client.
    """

    model_config = ConfigDict(extra="forbid")

    min_pass_rate: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Minimum pass rate (0.0-1.0) below which the report is marked not ok.",
    )


class EvalReport(BaseModel):
    """Top-level eval report."""

    model_config = ConfigDict(extra="forbid")

    queries: list[EvalQueryResult]
    pass_rate: float
    threshold: float
    ok: bool
    skipped: bool = Field(
        default=False,
        description=(
            "True when the eval did not run (e.g. provider API key missing). "
            "Distinct from ok=false, which means the run completed below the "
            "pass-rate threshold."
        ),
    )
    skip_reason: str | None = Field(
        default=None,
        description="Human-readable reason for skipping, set when skipped=true.",
    )


class ErrorBody(BaseModel):
    """Stable error envelope returned for every non-2xx response."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    """Wrapper so clients can match on `body.error.code` rather than `detail`."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
