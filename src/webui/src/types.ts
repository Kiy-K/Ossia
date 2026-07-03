/** Raw event envelope from the SSE stream. Mirrors src/core/events/types.py */
export interface OssiaEvent {
  id: string;
  seq: number;
  timestamp: string;
  type: string;
  source: string;
  thread_id: string;
  data: Record<string, unknown>;
}

// ── Per-type data payloads ─────────────────────────────────────────────────

export interface MessageData {
  role: string;
  text: string;
  id?: string | null;
}

export interface SubagentData {
  name: string;
  status?: string;
  path?: string[];
  result?: string | null;
  error?: string;
  text?: string;
}

export interface ToolData {
  name: string;
  input?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  output_delta?: string | null;
  source?: string;
}

export interface PipelineData {
  pipeline_id: string;
  pipeline_type: string;
  total_steps: number;
  step_name?: string;
  step_index?: number;
  status?: string;
  result?: string | null;
  error?: string;
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

// ── Application state types ────────────────────────────────────────────────

export type MessageRole = "user" | "assistant" | "tool" | "system";

export interface ChatMessage {
  role: MessageRole;
  content: string;
}

export interface SubagentState {
  name: string;
  state: "running" | "completed" | "error" | "interrupted";
  error?: string;
  startedAt: number;
}

export interface ToolState {
  name: string;
  state: "running" | "completed" | "failed";
  input: Record<string, unknown>;
  output?: unknown;
  error?: string;
  startedAt: number;
}

export interface PipelineState {
  pipeline_type: string;
  pipeline_id: string;
  total_steps: number;
  current_step: number;
  state: "running" | "completed" | "failed";
  steps: PipelineStepState[];
}

export interface PipelineStepState {
  name: string;
  state: "running" | "completed" | "failed";
  index: number;
  error?: string;
}

export interface AsyncTaskState {
  task_id: string;
  agent_name: string;
  status: string;
  error?: string;
}

export interface ReActStep {
  kind: "thought" | "action" | "observation";
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  output?: unknown;
  success?: boolean;
  error?: string;
  time: string;
}

export interface AppState {
  connected: boolean;
  thread_id: string;
  run_state: "idle" | "running" | "completed" | "interrupted" | "error";
  error: string | null;
  messages: ChatMessage[];
  streamingMessage: string;
  subagents: Record<string, SubagentState>;
  tools: ToolState[];
  pipelines: Record<string, PipelineState>;
  async_tasks: AsyncTaskState[];
  interrupts: InterruptData | null;
  react_steps: ReActStep[];
  user_input: string;
}

export interface Config {
  apiUrl: string;
  apiKey: string;
}
