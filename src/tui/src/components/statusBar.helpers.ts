import type { AppState } from "../types";

export function activeAgentCount(state: AppState): number {
  return Object.values(state.subagents).filter((s) => s.state === "running").length;
}

export function activeToolCount(state: AppState): number {
  return state.tools.filter((t) => t.state === "running").length;
}

export function activeAsyncTaskCount(state: AppState): number {
  return state.async_tasks.filter((t) =>
    ["running", "pending", "launched"].includes(t.status),
  ).length;
}
