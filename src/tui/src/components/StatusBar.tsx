/**
 * StatusBar — thread ID and active work counts.
 *
 * Minimal format:
 * ────────────────────────
 * thread: abc123 | 2 agents | 1 tool
 * ────────────────────────
 */

import type { AppState } from "../types";
import { activeAgentCount, activeToolCount, activeAsyncTaskCount } from "./statusBar.helpers";
import { Box, Text } from "./primitives";

interface StatusBarProps {
  state: AppState;
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
    <Box
      height={1}
      width="100%"
      flexDirection="row"
      justifyContent="space-between"
    >
      <Text attributes={1}>
        {parts.join(" | ")}
      </Text>
      <Text
        attributes={
          state.run_state === "running" ? 1 : (state.run_state !== "error" ? 2 : undefined)
        }
      >
        {stateTag}
      </Text>
    </Box>
  );
}
