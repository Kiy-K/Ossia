/**
 * StatusBar — thread ID and active work counts.
 *
 * Minimal format:
 * ────────────────────────
 * thread: abc123 | 2 agents | 1 tool
 * ────────────────────────
 */

import type { AppState } from "../types";

interface StatusBarProps {
  state: AppState;
}

/** Number of subagents currently in a running state. */
export function activeAgentCount(state: AppState): number {
  return Object.values(state.subagents).filter((s) => s.state === "running").length;
}

/** Number of tools currently in a running state. */
export function activeToolCount(state: AppState): number {
  return state.tools.filter((t) => t.state === "running").length;
}

/** Number of async tasks in a non-terminal state. */
export function activeAsyncTaskCount(state: AppState): number {
  return state.async_tasks.filter((t) =>
    ["running", "pending", "launched"].includes(t.status),
  ).length;
}

export function StatusBar({ state }: StatusBarProps) {
  const agents = activeAgentCount(state);
  const tools = activeToolCount(state);
  const asyncTasks = activeAsyncTaskCount(state);
  const threadDisplay = state.thread_id
    ? `thread: ${state.thread_id.slice(0, 8)}`
    : "disconnected";

  const parts: string[] = [threadDisplay];
  if (agents > 0) parts.push(`${agents} agent${agents !== 1 ? "s" : ""}`);
  if (tools > 0) parts.push(`${tools} tool${tools !== 1 ? "s" : ""}`);
  if (asyncTasks > 0) parts.push(`${asyncTasks} bg`);

  // Show run state with appropriate emphasis
  const stateTag =
    state.run_state === "error"
      ? `error${state.error ? `: ${state.error}` : ""}`
      : state.run_state === "interrupted"
        ? "interrupted"
        : state.run_state === "running"
          ? "running"
          : state.run_state === "completed"
            ? "done"
            : "idle";

  return (
    <box
      height={1}
      width="100%"
      flexDirection="row"
      justifyContent="space-between"
    >
      <text attributes={1}>
        {parts.join(" | ")}
      </text>
      <text
        attributes={
          state.run_state === "running" ? 1 : (state.run_state !== "error" ? 2 : undefined)
        }
      >
        {stateTag}
      </text>
    </box>
  );
}
