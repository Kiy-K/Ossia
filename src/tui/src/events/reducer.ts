/**
 * Ossia TUI — Event reducer.
 *
 * Pure function: ``(state, event) -> state``. This is the single place
 * where application state is mutated. Components read state but never
 * write it directly.
 *
 * The reducer mirrors the Python ``reduce_event`` in
 * ``src/core/events/reducers.py`` but produces a flat renderable tree
 * optimised for the TUI, not the hierarchical agent state tree.
 */
import type { OssiaEvent } from "./types";
import type {
  AppState,
  InterruptState,
  SubagentState,
  ToolState,
  AsyncTaskState,
  ReActStep,
} from "../types";
/** Format the current local time as HH:MM. */
function formatTime(): string {
  const now = new Date();
  const hh = now.getHours().toString().padStart(2, "0");
  const mm = now.getMinutes().toString().padStart(2, "0");
  return `${hh}:${mm}`;
}
/** Truncate a string for timeline display. */
function truncate(s: string, max = 60): string {
  return s.length > max ? s.slice(0, max) + "..." : s;
}
/** Maximum number of ReAct steps retained in state to prevent unbounded growth. */
const MAX_REACT_STEPS = 100;

/** Return the initial (empty) application state. */
export function initialAppState(): AppState {
  return {
    connected: false,
    thread_id: "",
    run_state: "idle",
    error: null,
    messages: [],
    timeline: [],
    subagents: {},
    tools: [],
    async_tasks: [],
    interrupts: null,
    user_input: "",
    react_steps: [],
  };
}
/**
 * Reduce a single ``OssiaEvent`` into the application state.
 *
 * Returns a new state object (immutable update). The component tree
 * will re-render when React detects the reference change.
 */
export function reduceEvent(state: AppState, event: OssiaEvent): AppState {
  const time = formatTime();
  // Always forward thread_id from the event envelope into state
  const withThreadId = (s: AppState): AppState =>
    event.thread_id && !s.thread_id ? { ...s, thread_id: event.thread_id } : s;
  // Apply withThreadId at the top so every event type gets it
  state = withThreadId(state);
  switch (event.type) {
    // ── Messages ──────────────────────────────────────────────────────
    case "message_started": {
      return {
        ...state,
        connected: true,
        run_state: "running",
        timeline: [
          ...state.timeline,
          { time, event: "Assistant", detail: truncate(String(event.data.text ?? "")) },
        ],
      };
    }
    case "message_delta": {
      // Accumulate in the last timeline entry if it's still "Assistant"
      const timeline = [...state.timeline];
      const last = timeline.at(-1);
      if (last && last.event === "Assistant") {
        timeline[timeline.length - 1] = {
          ...last,
          detail: truncate(String(event.data.text ?? ""), 60),
        };
      }
      return { ...state, connected: true, timeline };
    }
    case "message_completed": {
      const role = String(event.data.role ?? "assistant");
      const text = String(event.data.text ?? "");
      const safeRole = role === "ai" ? "assistant" : (role as "user" | "assistant" | "tool" | "system");
      // Filter out Python object repr strings that leak from the backend
      // when the v3 stream's message text is an AsyncProjection that didn't
      // get properly resolved (e.g. "<module.ClassName object at 0x...>").
      const isPythonRepr = /^<[\w.]+ object at 0x[0-9a-f]+>$/.test(text);
      const cleanText = isPythonRepr ? "[content available]" : text;

      // ReAct: assistant messages are "thought" steps in the reasoning loop.
      // Only assistant-role messages represent reasoning; skip user/tool/system.
      const reactSteps: ReActStep[] = [...(state.react_steps ?? [])];
      if (safeRole === "assistant" && cleanText) {
        reactSteps.push({ kind: "thought", content: cleanText, time });
        if (reactSteps.length > MAX_REACT_STEPS) reactSteps.shift();
      }

      return {
        ...state,
        connected: true,
        messages: [...state.messages, { role: safeRole, content: cleanText }],
        timeline: [
          ...state.timeline,
          { time, event: "Message", detail: truncate(cleanText, 60) },
        ],
        react_steps: reactSteps,
      };
    }
    // ── Subagents ─────────────────────────────────────────────────────
    case "subagent_spawned": {
      const name = String(event.data.name ?? "unknown");
      const sub: SubagentState = { name, state: "running", messages: [] };
      return {
        ...state,
        connected: true,
        subagents: { ...state.subagents, [name]: sub },
        timeline: [...state.timeline, { time, event: `spawn ${name}`, detail: "" }],
      };
    }
    case "subagent_completed": {
      const name = String(event.data.name ?? "");
      const existing = state.subagents[name];
      if (!existing) return state;
      return {
        ...state,
        subagents: {
          ...state.subagents,
          [name]: { ...existing, state: "completed" },
        },
        timeline: [...state.timeline, { time, event: `done ${name}`, detail: "" }],
      };
    }
    case "subagent_failed": {
      const name = String(event.data.name ?? "");
      const err = String(event.data.error ?? "unknown error");
      const existing = state.subagents[name];
      if (!existing) return state;
      return {
        ...state,
        subagents: {
          ...state.subagents,
          [name]: { ...existing, state: "error", error: err },
        },
        timeline: [...state.timeline, { time, event: `failed ${name}`, detail: err }],
      };
    }
    case "subagent_interrupted": {
      const name = String(event.data.name ?? "unknown");
      const existing = state.subagents[name];
      return {
        ...state,
        subagents: existing
          ? { ...state.subagents, [name]: { ...existing, state: "interrupted" } }
          : state.subagents,
        timeline: [...state.timeline, { time, event: "interrupted", detail: name }],
      };
    }
    // ── Tool calls ────────────────────────────────────────────────────
    case "tool_started": {
      const toolName = String(event.data.name ?? "unknown");
      const toolInput = (event.data.input as Record<string, unknown>) ?? {};
      const tool: ToolState = { name: toolName, state: "running", input: toolInput };
      // ReAct: tool invocation = Action step
      const actionStep: ReActStep = { kind: "action", tool: toolName, input: toolInput, time };
      return {
        ...state,
        connected: true,
        tools: [...state.tools, tool],
        timeline: [
          ...state.timeline,
          { time, event: `tool ${toolName}`, detail: truncate(JSON.stringify(toolInput), 50) },
        ],
        react_steps: [...(state.react_steps ?? []), actionStep].slice(-MAX_REACT_STEPS),
      };
    }
    case "tool_completed": {
      const toolName = String(event.data.name ?? "");
      // ReAct: tool result = successful Observation step
      const obsStep: ReActStep = {
        kind: "observation",
        tool: toolName,
        output: event.data.output,
        success: true,
        time,
      };
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.name === toolName && t.state === "running"
            ? { ...t, state: "completed", output: event.data.output }
            : t,
        ),
        timeline: [...state.timeline, { time, event: `done ${toolName}`, detail: "" }],
        react_steps: [...(state.react_steps ?? []), obsStep].slice(-MAX_REACT_STEPS),
      };
    }
    case "tool_failed": {
      const toolName = String(event.data.name ?? "");
      const err = String(event.data.error ?? "unknown error");
      // ReAct: tool failure = failed Observation step
      const obsStep: ReActStep = {
        kind: "observation",
        tool: toolName,
        output: null,
        success: false,
        error: err,
        time,
      };
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.name === toolName && t.state === "running"
            ? { ...t, state: "failed", error: err }
            : t,
        ),
        timeline: [...state.timeline, { time, event: `failed ${toolName}`, detail: err }],
        react_steps: [...(state.react_steps ?? []), obsStep].slice(-MAX_REACT_STEPS),
      };
    }
    // ── Pipelines ─────────────────────────────────────────────────────
    case "pipeline_started": {
      const pipelineType = String(event.data.pipeline_type ?? "pipeline");
      const totalSteps = Number(event.data.total_steps ?? 0);
      return {
        ...state,
        timeline: [...state.timeline, { time, event: `pipeline ${pipelineType}`, detail: `${totalSteps} steps` }],
      };
    }
    case "pipeline_step_started": {
      const stepName = String(event.data.step_name ?? "");
      const sub: SubagentState = { name: stepName, state: "running", messages: [] };
      return {
        ...state,
        subagents: { ...state.subagents, [stepName]: sub },
        timeline: [...state.timeline, { time, event: `  step ${stepName}`, detail: "" }],
      };
    }
    case "pipeline_step_completed": {
      const stepName = String(event.data.step_name ?? "");
      const existing = state.subagents[stepName];
      return {
        ...state,
        subagents: existing
          ? { ...state.subagents, [stepName]: { ...existing, state: "completed" } }
          : state.subagents,
        timeline: [...state.timeline, { time, event: `  done ${stepName}`, detail: "" }],
      };
    }
    case "pipeline_step_failed": {
      const stepName = String(event.data.step_name ?? "");
      const err = String(event.data.error ?? "unknown error");
      const existing = state.subagents[stepName];
      return {
        ...state,
        subagents: existing
          ? { ...state.subagents, [stepName]: { ...existing, state: "error", error: err } }
          : state.subagents,
        timeline: [...state.timeline, { time, event: `  failed ${stepName}`, detail: err }],
      };
    }
    case "pipeline_completed": {
      return {
        ...state,
        timeline: [...state.timeline, { time, event: "pipeline done", detail: "" }],
      };
    }
    case "pipeline_failed": {
      const err = String(event.data.error ?? "unknown error");
      return {
        ...state,
        timeline: [...state.timeline, { time, event: "pipeline failed", detail: err }],
      };
    }
    // ── Async tasks ───────────────────────────────────────────────────
    case "async_task_started": {
      const taskId = String(event.data.task_id ?? "");
      const agentName = String(event.data.agent_name ?? "");
      const status = String(event.data.status ?? "running");
      const task: AsyncTaskState = { task_id: taskId, agent_name: agentName, status };
      return {
        ...state,
        async_tasks: [...state.async_tasks, task],
        timeline: [...state.timeline, { time, event: `async ${agentName}`, detail: status }],
      };
    }
    case "async_task_updated": {
      const taskId = String(event.data.task_id ?? "");
      return {
        ...state,
        async_tasks: state.async_tasks.map((t) =>
          t.task_id === taskId ? { ...t, status: String(event.data.status ?? t.status) } : t,
        ),
      };
    }
    case "async_task_completed": {
      const taskId = String(event.data.task_id ?? "");
      return {
        ...state,
        async_tasks: state.async_tasks.map((t) =>
          t.task_id === taskId ? { ...t, status: "completed" } : t,
        ),
        timeline: [...state.timeline, { time, event: "async done", detail: "" }],
      };
    }
    case "async_task_failed": {
      const taskId = String(event.data.task_id ?? "");
      const err = event.data.error ? String(event.data.error) : "unknown error";
      return {
        ...state,
        async_tasks: state.async_tasks.map((t) =>
          t.task_id === taskId ? { ...t, status: "failed", error: err } : t,
        ),
        timeline: [...state.timeline, { time, event: "async failed", detail: err }],
      };
    }
    case "async_task_cancelled": {
      const taskId = String(event.data.task_id ?? "");
      return {
        ...state,
        async_tasks: state.async_tasks.map((t) =>
          t.task_id === taskId ? { ...t, status: "cancelled" } : t,
        ),
      };
    }
    // ── Interrupt / Error / Complete ──────────────────────────────────
    case "interrupt": {
      return {
        ...state,
        run_state: "interrupted",
        interrupts: event.data as unknown as InterruptState,
        timeline: [...state.timeline, { time, event: "interrupted", detail: "awaiting input" }],
      };
    }
    case "error": {
      const errMsg = String(event.data.error ?? "unknown error");
      return {
        ...state,
        run_state: "error",
        error: errMsg,
        timeline: [...state.timeline, { time, event: "Error", detail: errMsg }],
      };
    }
    case "complete": {
      const interrupted = Boolean(event.data.interrupted);
      return {
        ...state,
        run_state: interrupted ? "interrupted" : ("completed" as const),
        timeline: [
          ...state.timeline,
          { time, event: interrupted ? "paused" : "done", detail: "" },
        ],
      };
    }
    // ── Artifact events (pass-through, no UI yet) ────────────────
    case "artifact_received":
    case "artifact_processed":
    case "image_analysis_started":
    case "image_analysis_completed":
      return state;
    default:
      return state;
  }
}
