/**
 * Ossia TUI — Event type definitions.
 *
 * These TypeScript types mirror the backend ``OssiaEvent`` schema from
 * ``src/core/events/types.py``. The SSE stream from /v1/chat/stream
 * delivers these as JSON payloads in the ``data:`` field.
 *
 * Every event has:
 *  - ``id``: globally unique UUID hex
 *  - ``seq``: monotonically increasing sequence number
 *  - ``timestamp``: ISO-8601 UTC
 *  - ``type``: event type discriminator
 *  - ``source``: dot-separated emitter path
 *  - ``thread_id``: scoped thread id
 *  - ``data``: type-specific payload
 */

/** Raw event envelope from the SSE stream. */
export interface OssiaEvent {
  id: string;
  seq: number;
  timestamp: string;
  type: string;
  source: string;
  thread_id: string;
  data: Record<string, unknown>;
}

// ── Per-type data payload shapes ───────────────────────────────────────────

export interface MessageData {
  role: string;
  text: string;
  id?: string | null;
}

export interface SubagentSpawnedData {
  name: string;
  path: string[];
}

export interface SubagentCompletedData {
  name: string;
  result?: string | null;
  path: string[];
}

export interface SubagentFailedData {
  name: string;
  error: string;
  path: string[];
}

export interface SubagentInterruptedData {
  name: string;
  path: string[];
}

export interface SubagentMessageDeltaData {
  name: string;
  text: string;
  path: string[];
}

export interface ToolStartedData {
  name: string;
  input: Record<string, unknown>;
  source: string;
}

export interface ToolProgressData {
  name: string;
  output_delta: string | null;
  source: string;
}

export interface ToolCompletedData {
  name: string;
  output: unknown;
  source: string;
}

export interface ToolFailedData {
  name: string;
  error: string;
  source: string;
}

export interface PipelineStartedData {
  pipeline_type: string;
  total_steps: number;
  pipeline_id: string;
}

export interface PipelineStepData {
  pipeline_id: string;
  step_name: string;
  step_index: number;
  total_steps: number;
  error?: string;
  result?: string | null;
}

export interface AsyncTaskData {
  event: string;
  task_id: string;
  agent_name: string;
  status: string;
  tasks?: Array<Record<string, unknown>>;
  error?: string | null;
}

export interface InterruptData {
  interrupts: Array<Record<string, unknown>>;
}

export interface CompleteData {
  output: Record<string, unknown>;
  interrupted: boolean;
}
