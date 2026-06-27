/**
 * Ossia TUI — App state types.
 *
 * These are the renderable state types consumed by the OpenTUI React
 * components. State is produced exclusively by the reducer in
 * events/reducer.ts — components must never mutate state directly.
 */

/** A single chat message in the conversation. */
export interface ChatMessage {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  tool_calls?: Array<{ id: string; name: string; args: Record<string, unknown> }>;
}

/** One entry in the chronological timeline. */
export interface TimelineEntry {
  time: string;
  event: string;
  detail: string;
}

/** Lifecycle state of a subagent. */
export interface SubagentState {
  name: string;
  state: "idle" | "running" | "completed" | "error" | "interrupted";
  error?: string;
  messages: string[];
}

/** Lifecycle state of a tool call. */
export interface ToolState {
  name: string;
  state: "running" | "completed" | "failed";
  input?: Record<string, unknown>;
  output?: unknown;
  error?: string;
}

/** Lifecycle state of an async background task. */
export interface AsyncTaskState {
  task_id: string;
  agent_name: string;
  status: string;
  error?: string | null;
}

/** HITL interrupt payload. */
export interface InterruptState {
  interrupts: Array<Record<string, unknown>>;
}

/**
 * Top-level application state.
 *
 * Mutable only through the reducer. The component tree reads from
 * this single source of truth.
 */
export interface AppState {
  /** Whether the SSE connection to the backend is active. */
  connected: boolean;
  /** The scoped thread id. */
  thread_id: string;
  /** High-level run status. */
  run_state: "idle" | "running" | "completed" | "interrupted" | "error";
  /** Runtime error message, set when run_state === "error". */
  error: string | null;
  /** Accumulated chat messages. */
  messages: ChatMessage[];
  /** Chronological event log for the timeline panel. */
  timeline: TimelineEntry[];
  /** Active / completed subagents, keyed by name. */
  subagents: Record<string, SubagentState>;
  /** Active / completed tool calls in emit order. */
  tools: ToolState[];
  /** Async background tasks. */
  async_tasks: AsyncTaskState[];
  /** Active HITL interrupt, or null. */
  interrupts: InterruptState | null;
  /** Current input bar draft text. */
  user_input: string;
}
