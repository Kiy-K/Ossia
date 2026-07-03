import type { OssiaEvent, AppState, ReActStep, SubagentState, ToolState, PipelineState, PipelineStepState } from "./types";

function now(): string {
  return new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function initialAppState(): AppState {
  return {
    connected: false,
    thread_id: "",
    run_state: "idle",
    error: null,
    messages: [],
    streamingMessage: "",
    subagents: {},
    tools: [],
    pipelines: {},
    async_tasks: [],
    interrupts: null,
    react_steps: [],
    user_input: "",
  };
}

export function reduceEvent(state: AppState, event: OssiaEvent): AppState {
  const time = now();
  state = event.thread_id && !state.thread_id ? { ...state, thread_id: event.thread_id } : state;

  switch (event.type) {
    // ── Messages ────────────────────────────────────────────────────
    case "message_started": {
      const text = String(event.data.text ?? "");
      return {
        ...state,
        connected: true,
        run_state: "running",
        streamingMessage: text,
        messages: [...state.messages, { role: "assistant", content: text }],
      };
    }
    case "message_delta": {
      const text = String(event.data.text ?? "");
      const msgs = [...state.messages];
      if (msgs.length > 0 && msgs[msgs.length - 1].role === "assistant") {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], content: text };
      }
      return { ...state, connected: true, streamingMessage: text, messages: msgs };
    }
    case "message_completed": {
      const text = String(event.data.text ?? "");
      const role = String(event.data.role ?? "assistant") === "ai" ? "assistant" : (String(event.data.role ?? "assistant") as "user" | "assistant" | "tool" | "system");
      // Filter Python object repr leaks
      const cleanText = /^<[\w.]+ object at 0x[0-9a-f]+>$/.test(text) ? "[content available]" : text;
      const msgs = [...state.messages];
      if (msgs.length > 0 && msgs[msgs.length - 1].role === "assistant") {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], content: cleanText };
      } else {
        msgs.push({ role, content: cleanText });
      }
      const reactSteps: ReActStep[] = [...state.react_steps];
      if (cleanText) {
        reactSteps.push({ kind: "thought", content: cleanText, time });
      }
      return { ...state, connected: true, streamingMessage: "", messages: msgs, react_steps: reactSteps };
    }

    // ── Subagents ───────────────────────────────────────────────────
    case "subagent_spawned": {
      const name = String(event.data.name ?? "unknown");
      const sub: SubagentState = { name, state: "running", startedAt: Date.now() };
      return {
        ...state,
        connected: true,
        subagents: { ...state.subagents, [name]: sub },
      };
    }
    case "subagent_completed": {
      const name = String(event.data.name ?? "");
      const existing = state.subagents[name];
      if (!existing) return state;
      return {
        ...state,
        subagents: { ...state.subagents, [name]: { ...existing, state: "completed" } },
      };
    }
    case "subagent_failed": {
      const name = String(event.data.name ?? "");
      const err = String(event.data.error ?? "unknown error");
      const existing = state.subagents[name];
      if (!existing) return state;
      return {
        ...state,
        subagents: { ...state.subagents, [name]: { ...existing, state: "error", error: err } },
      };
    }
    case "subagent_interrupted": {
      const name = String(event.data.name ?? "");
      const existing = state.subagents[name];
      return {
        ...state,
        subagents: existing
          ? { ...state.subagents, [name]: { ...existing, state: "interrupted" } }
          : state.subagents,
      };
    }

    // ── Tool calls ──────────────────────────────────────────────────
    case "tool_started": {
      const toolName = String(event.data.name ?? "unknown");
      const toolInput = (event.data.input as Record<string, unknown>) ?? {};
      const tool: ToolState = { name: toolName, state: "running", input: toolInput, startedAt: Date.now() };
      const actionStep: ReActStep = { kind: "action", tool: toolName, input: toolInput, time };
      return {
        ...state,
        connected: true,
        tools: [...state.tools, tool],
        react_steps: [...state.react_steps, actionStep],
      };
    }
    case "tool_completed": {
      const toolName = String(event.data.name ?? "");
      const obsStep: ReActStep = { kind: "observation", tool: toolName, output: event.data.output, success: true, time };
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.name === toolName && t.state === "running"
            ? { ...t, state: "completed", output: event.data.output }
            : t,
        ),
        react_steps: [...state.react_steps, obsStep],
      };
    }
    case "tool_failed": {
      const toolName = String(event.data.name ?? "");
      const err = String(event.data.error ?? "unknown error");
      const obsStep: ReActStep = { kind: "observation", tool: toolName, output: null, success: false, error: err, time };
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.name === toolName && t.state === "running"
            ? { ...t, state: "failed", error: err }
            : t,
        ),
        react_steps: [...state.react_steps, obsStep],
      };
    }

    // ── Pipelines ───────────────────────────────────────────────────
    case "pipeline_started": {
      const pipelineType = String(event.data.pipeline_type ?? "pipeline");
      const pipelineId = String(event.data.pipeline_id ?? "");
      const totalSteps = Number(event.data.total_steps ?? 0);
      const pipeline: PipelineState = {
        pipeline_type: pipelineType,
        pipeline_id: pipelineId,
        total_steps: totalSteps,
        current_step: 0,
        state: "running",
        steps: [],
      };
      return {
        ...state,
        pipelines: { ...state.pipelines, [pipelineId]: pipeline },
      };
    }
    case "pipeline_step_started": {
      const pipelineId = String(event.data.pipeline_id ?? "");
      const stepName = String(event.data.step_name ?? "");
      const stepIndex = Number(event.data.step_index ?? 0);
      const existing = state.pipelines[pipelineId];
      if (!existing) return state;
      const step: PipelineStepState = { name: stepName, state: "running", index: stepIndex };
      return {
        ...state,
        pipelines: {
          ...state.pipelines,
          [pipelineId]: {
            ...existing,
            current_step: stepIndex,
            steps: [...existing.steps, step],
          },
        },
      };
    }
    case "pipeline_step_completed": {
      const pipelineId = String(event.data.pipeline_id ?? "");
      const stepName = String(event.data.step_name ?? "");
      const existing = state.pipelines[pipelineId];
      if (!existing) return state;
      return {
        ...state,
        pipelines: {
          ...state.pipelines,
          [pipelineId]: {
            ...existing,
            steps: existing.steps.map((s) =>
              s.name === stepName && s.state === "running"
                ? { ...s, state: "completed" }
                : s,
            ),
          },
        },
      };
    }
    case "pipeline_step_failed": {
      const pipelineId = String(event.data.pipeline_id ?? "");
      const stepName = String(event.data.step_name ?? "");
      const err = String(event.data.error ?? "unknown error");
      const existing = state.pipelines[pipelineId];
      if (!existing) return state;
      return {
        ...state,
        pipelines: {
          ...state.pipelines,
          [pipelineId]: {
            ...existing,
            steps: existing.steps.map((s) =>
              s.name === stepName && s.state === "running"
                ? { ...s, state: "failed", error: err }
                : s,
            ),
          },
        },
      };
    }
    case "pipeline_completed": {
      const pipelineId = String(event.data.pipeline_id ?? "");
      const existing = state.pipelines[pipelineId];
      if (!existing) return state;
      return {
        ...state,
        pipelines: {
          ...state.pipelines,
          [pipelineId]: { ...existing, state: "completed" },
        },
      };
    }
    case "pipeline_failed": {
      const pipelineId = String(event.data.pipeline_id ?? "");
      const existing = state.pipelines[pipelineId];
      if (!existing) return state;
      return {
        ...state,
        pipelines: {
          ...state.pipelines,
          [pipelineId]: { ...existing, state: "failed" },
        },
      };
    }

    // ── Async tasks ─────────────────────────────────────────────────
    case "async_task_started":
    case "async_task_updated":
    case "async_task_completed":
    case "async_task_failed":
    case "async_task_cancelled": {
      const taskId = String(event.data.task_id ?? "");
      const agentName = String(event.data.agent_name ?? "");
      const status = String(event.data.status ?? "unknown");
      if (event.type === "async_task_started") {
        return {
          ...state,
          async_tasks: [...state.async_tasks, { task_id: taskId, agent_name: agentName, status }],
        };
      }
      return {
        ...state,
        async_tasks: state.async_tasks.map((t) =>
          t.task_id === taskId
            ? { ...t, status, error: event.data.error ? String(event.data.error) : t.error }
            : t,
        ),
      };
    }

    // ── Interrupt / Complete ────────────────────────────────────────
    case "interrupt": {
      return {
        ...state,
        run_state: "interrupted",
        interrupts: event.data as { interrupts: Array<Record<string, unknown>> },
      };
    }
    case "error": {
      return {
        ...state,
        run_state: "error",
        error: String(event.data.error ?? "unknown error"),
      };
    }
    case "complete": {
      const interrupted = Boolean(event.data.interrupted);
      return {
        ...state,
        run_state: interrupted ? "interrupted" : "completed",
        streamingMessage: "",
      };
    }

    // ── Artifact events (basic pass-through) ────────────────────
    case "artifact_received":
    case "artifact_processed":
    case "image_analysis_started":
    case "image_analysis_completed":
      return { ...state, connected: true };

    default:
      return state;
  }
}
