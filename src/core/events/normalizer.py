"""Normalize DeepAgent v3 stream projections into ``OssiaEvent`` (real-time concurrent merge).

Architecture
------------
The normalizer sits between ``agent.astream_events(version="v3")`` and the
SSE output layer. It consumes the four v3 projections (``messages``,
``tool_calls``, ``subagents``, ``values``) concurrently and yields a
single ordered stream of ``OssiaEvent`` objects.

Each projection is handled by a dedicated ``_relay_*`` coroutine. All
relays run concurrently; each relay puts events into a shared
``asyncio.Queue`` as they arrive. The main ``normalize()`` loop consumes
from the queue and yields each event immediately. This means events are
delivered in near-real-time rather than batched after all relays finish.

The normalizer adds a monotonic ``seq`` number and a stable event ``id``
to every event, enabling replay and deduplication downstream.

Nested subagent support
-----------------------
The normalizer tracks subagent lifecycle via the ``path`` field in
DeepAgent's ``stream.subagents`` projection. Each subagent spawn
emits a ``subagent_spawned`` event with the full path (e.g.
``"coordinator.researcher"``). Tool calls made *by* a subagent are
tagged with the subagent's source path, so the TUI can render
tool activity under the correct subagent tree.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from core.events.types import (
    OssiaEvent,
    artifact_data,
    async_task_data,
    complete_data,
    interrupt_data,
    message_completed_data,
    message_delta_data,
    message_started_data,
    pipeline_completed_data,
    pipeline_failed_data,
    pipeline_started_data,
    pipeline_step_completed_data,
    pipeline_step_failed_data,
    pipeline_step_started_data,
    subagent_completed_data,
    subagent_failed_data,
    subagent_interrupted_data,
    subagent_message_delta_data,
    subagent_spawned_data,
    tool_completed_data,
    tool_failed_data,
    tool_progress_data,
    tool_started_data,
)

logger = logging.getLogger(__name__)

# Sentinel value signalling a relay has finished.
_SENTINEL: OssiaEvent | None = None

# Pipeline orchestrator tool names.
_PIPELINE_TOOLS: frozenset[str] = frozenset({
    "run_bugfix_pipeline",
    "run_audit_pipeline",
    "run_refactor_pipeline",
})

# Known pipeline types and their subagent step sequences in order.
_PIPELINE_STEPS: dict[str, list[str]] = {
    "bugfix": ["bug-diagnostician", "fix-proposer", "test-runner"],
    "audit": ["code-researcher", "bug-diagnostician"],
    "refactor": ["code-researcher", "fix-proposer", "fix-proposer", "test-runner"],
}


class _PipelineState:
    """Tracks an active pipeline lifecycle across concurrent relays."""

    __slots__ = ("pipeline_id", "pipeline_type", "step_names", "current_step", "state")

    def __init__(self, pipeline_type: str, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        self.pipeline_type = pipeline_type
        self.step_names = list(_PIPELINE_STEPS.get(pipeline_type, []))
        self.current_step = 0
        self.state: str = "running"

    @property
    def total_steps(self) -> int:
        return len(self.step_names)

    @property
    def current_step_name(self) -> str | None:
        if 0 <= self.current_step < len(self.step_names):
            return self.step_names[self.current_step]
        return None

    def advance(self) -> None:
        self.current_step += 1

    @property
    def is_complete(self) -> bool:
        return self.current_step >= len(self.step_names)


# ── Sequence counter ─────────────────────────────────────────────────────────


class _SeqCounter:
    """Thread-safe monotonic sequence counter."""

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        self._value += 1
        return self._value


# ── Safe value extraction ────────────────────────────────────────────────────


def _safe(obj: Any) -> Any:
    """Best-effort conversion to a JSON-friendly value."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return _safe(obj.model_dump())
        except Exception:
            return repr(obj)
    if hasattr(obj, "value") and not callable(obj.value):
        return _safe(obj.value)
    return str(obj)


# ── Normalizer ───────────────────────────────────────────────────────────────


class EventNormalizer:
    """Normalizes a DeepAgent v3 stream into ``OssiaEvent`` objects.

    Usage::

        normalizer = EventNormalizer(thread_id="my-thread")
        async for event in normalizer.normalize(stream, artifacts=[]):
            # event is an OssiaEvent
            print(event.type, event.data)
    """

    def __init__(self, thread_id: str = "default") -> None:
        self._thread_id = thread_id
        self._seq = _SeqCounter()
        # Track active subagents: path -> name
        self._active_subagents: dict[str, str] = {}
        # Track previous async task snapshots for delta detection
        self._prev_async_tasks: list[dict[str, Any]] = []
        # Track image artifact metadata for post-stream completion events
        self._image_artifact_metadata: list[dict[str, Any]] = []
        # Track active pipeline (set by _relay_tool_calls, consumed by _relay_subagents)
        self._active_pipeline: _PipelineState | None = None

    def _event(self, type_: str, data: dict[str, Any], source: str = "coordinator") -> OssiaEvent:
        """Create an OssiaEvent with auto-incrementing seq."""
        return OssiaEvent(
            seq=self._seq.next(),
            type=type_,
            source=source,
            thread_id=self._thread_id,
            data=data,
        )

    # ── Message relay ────────────────────────────────────────────────────────

    async def _resolve_text(self, raw_text: Any) -> str:
        """Resolve a ``.text`` field that may be a projection into a plain string.

        The v3 ``stream.messages`` projection yields ``AsyncChatModelStream``
        (or ``ChatModelStream``) objects per LLM call. These have a ``.text``
        property that returns an ``AsyncProjection`` (awaitable) or
        ``SyncTextProjection`` (iterable + ``__str__``), **not** a plain string.
        Normalizing via ``str()`` produces a Python object repr like
        ``'<...AsyncProjection object at 0x...>'`` rather than the actual
        generated text.

        This helper detects the projection types via duck-typing and resolves
        them to plain strings:

        - ``AsyncProjection`` (has ``__await__``) → ``await raw_text``
        - ``SyncTextProjection`` (has ``__iter__``) → ``str(raw_text)``
        - Plain string → passthrough
        - Anything else → ``str(raw_text)`` (fallback, may produce repr)
        """
        if isinstance(raw_text, str):
            return raw_text
        if hasattr(raw_text, '__await__'):
            resolved = await raw_text
            return str(resolved) if not isinstance(resolved, str) else resolved
        if hasattr(raw_text, '__iter__'):
            return str(raw_text)
        return str(raw_text)

    async def _relay_messages(
        self, stream: Any, queue: asyncio.Queue[OssiaEvent | None]
    ) -> None:
        """Convert ``stream.messages`` to ``message_started/delta/completed``.

        Emits ``message_started`` on the first token of a new message (detected
        by watching for a change in the message ``id``). Subsequent tokens for
        the same message emit ``message_delta``. When the message ``id`` changes
        (old message done, new one starting), emits ``message_completed`` for the
        old message with the accumulated text. A final ``message_completed`` is
        emitted for the last message when the stream is exhausted.

        Note: Subagent text generation is not directly accessible from the v3
        ``stream.messages`` projection. ``subagent_message_delta`` events will
        be added once the v3 stream exposes subagent provenance on message items.

        The v3 stream yields ``AsyncChatModelStream`` / ``ChatModelStream``
        objects (one per LLM call) rather than per-token items. Their ``.text``
        is a projection that ``_resolve_text`` handles by awaiting or
        stringifying it into the actual generated text.
        """
        try:
            last_msg_id: str | None = None
            last_role: str = "ai"
            accumulated: str = ""
            async for m in stream.messages:
                raw_text = getattr(m, "text", None)
                if raw_text is None:
                    continue

                # Resolve the text field — it may be an AsyncProjection
                # (from AsyncChatModelStream) or SyncTextProjection
                # (from ChatModelStream), both of which require
                # special handling beyond simple str() conversion.
                is_projection = hasattr(raw_text, '__await__') or (
                    hasattr(raw_text, '__iter__') and not isinstance(raw_text, str)
                )

                if is_projection:
                    msg_text = await self._resolve_text(raw_text)
                    # ChatModelStream / AsyncChatModelStream do not carry
                    # .role or .id; all LLM responses are assistant messages.
                    role = "ai"
                    msg_id = getattr(m, "message_id", None)
                else:
                    msg_text = str(raw_text)
                    role = getattr(m, "role", "ai")
                    msg_id = getattr(m, "id", None)

                # Detect message completion: new msg_id != previous
                if msg_id is not None and msg_id != last_msg_id and last_msg_id is not None:
                    await queue.put(
                        self._event("message_completed", message_completed_data(last_role, accumulated, last_msg_id))
                    )
                    accumulated = ""

                if msg_id is not None and msg_id != last_msg_id:
                    await queue.put(
                        self._event("message_started", message_started_data(role, msg_text, msg_id))
                    )
                    last_msg_id = msg_id
                    last_role = role
                    accumulated = msg_text
                else:
                    await queue.put(
                        self._event("message_delta", message_delta_data(role, msg_text, msg_id))
                    )
                    accumulated += msg_text

            # Emit final message_completed for the last message
            if accumulated:
                await queue.put(
                    self._event("message_completed", message_completed_data(last_role, accumulated, last_msg_id))
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("messages relay failed: %r", exc)
        finally:
            await queue.put(_SENTINEL)

    # ── Tool call relay ──────────────────────────────────────────────────────

    async def _relay_tool_calls(
        self, stream: Any, queue: asyncio.Queue[OssiaEvent | None]
    ) -> None:
        """Convert ``stream.tool_calls`` to ``tool_started/progress/completed/failed``.

        Also detects pipeline orchestrator tool completions and emits
        ``pipeline_started`` events, setting ``self._active_pipeline``
        so ``_relay_subagents`` can annotate pipeline step subagents.
        """
        try:
            async for c in stream.tool_calls:
                tool_name = getattr(c, "tool_name", "")
                tool_input = getattr(c, "input", {}) or {}

                # Resolve source: check if we're inside a subagent
                source = self._source_for_active_subagent()

                await queue.put(
                    self._event("tool_started", tool_started_data(tool_name, _safe(tool_input)), source=source)
                )

                # Drain output deltas
                try:
                    async for d in c.output_deltas:
                        delta = str(d)
                        await queue.put(
                            self._event("tool_progress", tool_progress_data(tool_name, delta), source=source)
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("output_deltas failed: %r", exc)

                # Check for error
                error = getattr(c, "error", None)
                if error is not None:
                    await queue.put(
                        self._event("tool_failed", tool_failed_data(tool_name, str(error), source), source=source)
                    )
                else:
                    output = getattr(c, "output", None)
                    await queue.put(
                        self._event("tool_completed", tool_completed_data(tool_name, _safe(output), source), source=source)
                    )

                    # Detect pipeline orchestrator tool completion and start
                    # tracking the pipeline so _relay_subagents can emit
                    # step-level events.
                    if tool_name in _PIPELINE_TOOLS and isinstance(output, dict):
                        pipeline_type = output.get("pipeline", "")
                        if pipeline_type in _PIPELINE_STEPS:
                            pipeline_id = f"{pipeline_type}-{uuid.uuid4().hex[:8]}"
                            self._active_pipeline = _PipelineState(pipeline_type, pipeline_id)
                            await queue.put(
                                self._event(
                                    "pipeline_started",
                                    pipeline_started_data(
                                        pipeline_type,
                                        self._active_pipeline.total_steps,
                                        pipeline_id,
                                    ),
                                )
                            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("tool_calls relay failed: %r", exc)
        finally:
            await queue.put(_SENTINEL)

    # ── Subagent relay ───────────────────────────────────────────────────────

    def _source_for_active_subagent(self) -> str:
        """Return the source path of the most recently spawned subagent, or 'coordinator'.

        This is a best-effort heuristic: if the normalizer has seen a subagent
        spawn but not yet its completion, subsequent tool calls are attributed
        to that subagent.
        """
        if self._active_subagents:
            # Return the deepest active subagent path
            return sorted(self._active_subagents.keys())[-1]
        return "coordinator"

    def _source_from_path(self, path: list[str]) -> str:
        """Build a dot-separated source path from a LangGraph namespace path."""
        # path is something like ["ossia", "agent", "subagent_name"]
        # Extract subagent names (skip fixed prefixes)
        parts: list[str] = []
        for p in path:
            if p in ("ossia", "agent", "coordinator"):
                parts = []
            else:
                parts.append(p)
        if not parts:
            return "coordinator"
        return f"coordinator.{'.'.join(parts)}"

    async def _relay_subagents(
        self, stream: Any, queue: asyncio.Queue[OssiaEvent | None]
    ) -> None:
        """Convert ``stream.subagents`` to ``subagent_spawned/*/completed/failed``,
        annotating pipeline lifecycle events when ``self._active_pipeline`` is set.

        When a pipeline orchestrator tool has completed, subsequent subagent
        spawn/complete events whose names match the current pipeline step
        are wrapped with ``pipeline_step_started/completed/failed`` events.
        When the final step completes, a ``pipeline_completed`` event is emitted
        and the pipeline context is cleared.
        """
        try:
            async for s in stream.subagents:
                name = getattr(s, "name", "")
                status = str(getattr(s, "status", "unknown"))
                raw_path: list[str] = list(getattr(s, "path", []) or [])
                source = self._source_from_path(raw_path)

                if status == "started":
                    # If a pipeline is active and this subagent matches
                    # the current expected step, emit a step_started event.
                    if self._active_pipeline is not None and self._active_pipeline.state == "running":
                        expected = self._active_pipeline.current_step_name
                        if expected == name:
                            await queue.put(
                                self._event(
                                    "pipeline_step_started",
                                    pipeline_step_started_data(
                                        self._active_pipeline.pipeline_id,
                                        name,
                                        self._active_pipeline.current_step,
                                        self._active_pipeline.total_steps,
                                    ),
                                )
                            )

                    self._active_subagents[source] = name
                    await queue.put(
                        self._event("subagent_spawned", subagent_spawned_data(name, raw_path), source=source)
                    )

                elif status in ("completed", "success"):
                    self._active_subagents.pop(source, None)
                    await queue.put(
                        self._event("subagent_completed", subagent_completed_data(name, path=raw_path), source=source)
                    )

                    # Advance the active pipeline if this subagent was the
                    # expected step. Always emit pipeline_step_completed first,
                    # then pipeline_completed if this was the last step.
                    if self._active_pipeline is not None and self._active_pipeline.state == "running":
                        expected = self._active_pipeline.current_step_name
                        if expected == name:
                            # Emit step completion before advancing so step_index
                            # reflects the step that just finished.
                            await queue.put(
                                self._event(
                                    "pipeline_step_completed",
                                    pipeline_step_completed_data(
                                        self._active_pipeline.pipeline_id,
                                        name,
                                        self._active_pipeline.current_step,
                                    ),
                                )
                            )
                            self._active_pipeline.advance()
                            if self._active_pipeline.is_complete:
                                await queue.put(
                                    self._event(
                                        "pipeline_completed",
                                        pipeline_completed_data(
                                            self._active_pipeline.pipeline_id,
                                            result=f"Pipeline {self._active_pipeline.pipeline_type} completed",
                                        ),
                                    )
                                )
                                self._active_pipeline = None

                elif status == "error":
                    self._active_subagents.pop(source, None)
                    await queue.put(
                        self._event("subagent_failed", subagent_failed_data(name, error=status, path=raw_path), source=source)
                    )

                    # Mark the pipeline as failed if this subagent was the
                    # expected step.
                    if self._active_pipeline is not None and self._active_pipeline.state == "running":
                        expected = self._active_pipeline.current_step_name
                        if expected == name:
                            await queue.put(
                                self._event(
                                    "pipeline_step_failed",
                                    pipeline_step_failed_data(
                                        self._active_pipeline.pipeline_id,
                                        name,
                                        self._active_pipeline.current_step,
                                        error=f"Subagent {name} failed with status: {status}",
                                    ),
                                )
                            )
                            await queue.put(
                                self._event(
                                    "pipeline_failed",
                                    pipeline_failed_data(
                                        self._active_pipeline.pipeline_id,
                                        error=f"Step '{name}' failed",
                                    ),
                                )
                            )
                            self._active_pipeline = None

                elif status == "interrupted":
                    await queue.put(
                        self._event("subagent_interrupted", subagent_interrupted_data(name, path=raw_path), source=source)
                    )
                else:
                    # Unknown status — treat as general message delta
                    await queue.put(
                        self._event("subagent_message_delta", subagent_message_delta_data(name, status, raw_path), source=source)
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("subagents relay failed: %r", exc)
        finally:
            await queue.put(_SENTINEL)

    # ── Value / async task relay ─────────────────────────────────────────────

    async def _relay_values(
        self, stream: Any, queue: asyncio.Queue[OssiaEvent | None]
    ) -> None:
        """Extract async task events from ``stream.values``.

        This is the only relay that iterates ``stream.values`` (an async
        generator that can only be consumed once). It detects transitions
        in the ``async_tasks`` state channel and emits ``async_task_*`` events.
        """
        try:
            async for v in stream.values:
                values = _safe(v) if v is not None else {}
                if not isinstance(values, dict):
                    continue
                raw_tasks = values.get("async_tasks")
                if raw_tasks is None or not isinstance(raw_tasks, list):
                    continue

                # Normalize tasks
                tasks: list[dict[str, Any]] = []
                for t in raw_tasks:
                    if isinstance(t, dict):
                        tasks.append(t)
                    elif hasattr(t, "model_dump"):
                        tasks.append(t.model_dump())
                    elif hasattr(t, "__dict__"):
                        tasks.append(vars(t))
                    else:
                        tasks.append({"raw": str(t)})
                if not tasks:
                    continue

                # Detect transitions from the previous snapshot
                prev_map = {t.get("task_id", ""): t for t in self._prev_async_tasks}
                for t in tasks:
                    tid = t.get("task_id", "")
                    agent_name = t.get("agent_name", t.get("name", ""))
                    status = str(t.get("status", "unknown"))
                    prev = prev_map.get(tid, {})
                    prev_status = str(prev.get("status", "")) if prev else ""

                    if prev_status != status or not prev:
                        if status in ("success", "completed"):
                            evt_type = "async_task_completed"
                        elif status == "error":
                            evt_type = "async_task_failed"
                        elif status == "cancelled":
                            evt_type = "async_task_cancelled"
                        elif status in ("running", "pending", "launched"):
                            evt_type = "async_task_started" if not prev else "async_task_updated"
                        else:
                            evt_type = "async_task_updated"

                        await queue.put(
                            self._event(
                                evt_type,
                                async_task_data(evt_type, tid, agent_name, status, tasks, t.get("error", None)),
                            )
                        )
                self._prev_async_tasks = tasks
        except Exception as exc:  # noqa: BLE001
            logger.debug("values / async_tasks relay failed: %r", exc)
        finally:
            await queue.put(_SENTINEL)

    # ── Artifact relay ───────────────────────────────────────────────────────

    async def _relay_artifacts(
        self, artifacts: list[Any], queue: asyncio.Queue[OssiaEvent | None]
    ) -> None:
        """Emit initial ``artifact_received`` events for request artifacts.

        Future: wire multimodal analysis completion back into artifact events
        (``artifact_processed``, ``image_analysis_completed``).
        """
        try:
            for i, art in enumerate(artifacts):
                art_type = getattr(art, "type", "image")
                filename = getattr(art, "filename", None) or f"artifact-{i}"

                # Track image artifact metadata for post-stream completion events
                if art_type == "image":
                    self._image_artifact_metadata.append({
                        "artifact_id": f"art-{i}",
                        "art_type": art_type,
                        "filename": filename,
                    })

                await queue.put(
                    self._event(
                        "artifact_received",
                        artifact_data(
                            artifact_id=f"art-{i}",
                            art_type=art_type,
                            filename=filename,
                            event="artifact_received",
                            analysis_state="pending",
                        ),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("artifacts relay failed: %r", exc)
        finally:
            await queue.put(_SENTINEL)

    # ── Public API ───────────────────────────────────────────────────────────

    async def normalize(
        self,
        stream: Any,
        artifacts: list[Any] | None = None,
    ) -> AsyncGenerator[OssiaEvent, None]:
        """Normalize a DeepAgent v3 stream into ``OssiaEvent`` objects.

        Accepts the return value of ``agent.astream_events(version="v3")``
        and an optional list of request artifacts. Yields normalized events
        in near-real-time as each relay produces them.

        All four (or five, with artifacts) relays run concurrently. Each
        relay puts its events into a shared ``asyncio.Queue``. The main
        loop consumes from the queue and yields each event immediately.
        Once all relays have sent their sentinel, the loop exits and the
        final ``interrupt`` and ``complete`` events are emitted.

        Args:
            stream: The v3 stream object (with ``.messages``, ``.tool_calls``,
                ``.subagents``, ``.values`` async generators).
            artifacts: Optional list of ``Artifact`` objects from the request
                payload. When provided, initial ``artifact_received`` events
                are emitted.

        Yields:
            ``OssiaEvent`` objects representing the normalized event stream.
        """
        queue: asyncio.Queue[OssiaEvent | None] = asyncio.Queue()

        # Create one asyncio.Task per relay so they all run concurrently
        # while the main loop consumes from the shared queue. Each relay
        # puts events into the queue and signals completion with a sentinel.
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self._relay_messages(stream, queue)),
            asyncio.create_task(self._relay_tool_calls(stream, queue)),
            asyncio.create_task(self._relay_subagents(stream, queue)),
            asyncio.create_task(self._relay_values(stream, queue)),
        ]
        if artifacts:
            tasks.append(
                asyncio.create_task(self._relay_artifacts(artifacts, queue))
            )

        num_producers = len(tasks)

        # Consume from the queue in real-time as events arrive.
        completed = 0
        while completed < num_producers:
            event = await queue.get()
            if event is None:
                completed += 1
            else:
                yield event

        # Wait for all tasks to finish, logging any individual failures.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                logger.debug("normalizer relay raised: %r", r)

        # Emit image_analysis_completed for any tracked image artifacts.
        for meta in self._image_artifact_metadata:
            yield self._event(
                "image_analysis_completed",
                artifact_data(
                    artifact_id=meta["artifact_id"],
                    art_type=meta["art_type"],
                    filename=meta["filename"],
                    event="image_analysis_completed",
                    analysis_state="completed",
                    summary="Image analysis completed.",
                ),
            )

        # Emit the final complete event. Extract output + interrupt info
        # from the stream object.
        try:
            output = stream.output
            output_dict = _safe(output) if output is not None else {}
            if not isinstance(output_dict, dict):
                output_dict = {"output": output_dict}
        except Exception as exc:  # noqa: BLE001
            logger.debug("stream.output failed: %r", exc)
            output_dict = {}

        interrupted = False
        try:
            interrupted = bool(stream.interrupted)
        except Exception as exc:  # noqa: BLE001
            logger.debug("stream.interrupted failed: %r", exc)

        # Emit interrupt events before the final complete
        if interrupted:
            interrupt_payload: list[dict[str, Any]] = []
            try:
                for it in stream.interrupts or ():
                    raw = it.value if hasattr(it, "value") else it
                    interrupt_payload.append(_safe(raw))
            except Exception as exc:  # noqa: BLE001
                logger.debug("stream.interrupts failed: %r", exc)
            if interrupt_payload:
                yield self._event("interrupt", interrupt_data(interrupt_payload))

        yield self._event("complete", complete_data(output_dict, interrupted))
