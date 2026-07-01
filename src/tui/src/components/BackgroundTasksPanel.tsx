/**
 * BackgroundTasksPanel — async subagent tasks.
 *
 * Shows active/recent async tasks.
 * Auto-collapses old successes.
 * Failures persist.
 *
 * Format:
 *   Background
 *   ────────────────────────
 *   [1] audit        running
 *   [2] tests        complete
 *   [3] indexer      failed
 */

import type { AppState, AsyncTaskState } from "../types";
import { Box, Text } from "./primitives";

interface BackgroundTasksPanelProps {
  state: AppState;
}

export function BackgroundTasksPanel({ state }: BackgroundTasksPanelProps) {
  if (state.async_tasks.length === 0) return null;

  // Show: running tasks, failed tasks, recently completed
  const runningTasks = state.async_tasks.filter((t) =>
    ["running", "pending", "launched"].includes(t.status),
  );
  const failedTasks = state.async_tasks.filter((t) => t.status === "failed");
  // Show last 3 completed
  const completedTasks = state.async_tasks
    .filter((t) => t.status === "completed")
    .slice(-3);

  const visible = [...runningTasks, ...failedTasks, ...completedTasks];
  if (visible.length === 0) return null;

  return (
    <Box flexDirection="column" width="100%">
      <Box height={1} flexDirection="row" width="100%">
        <Text attributes={1}>Background</Text>
        {runningTasks.length > 0 ? (
          <Text attributes={2}> {runningTasks.length} active</Text>
        ) : null}
      </Box>

      {visible.map((task: AsyncTaskState, i: number) => (
        <Box key={task.task_id} height={1} flexDirection="row" width="100%">
          <Text attributes={2}>{i + 1}.</Text>
          <Text> {task.agent_name}</Text>
          <Text attributes={2}>
            {task.status === "failed"
              ? ` failed${task.error ? `: ${task.error}` : ""}`
              : task.status === "completed"
                ? " complete"
                : " running"}
          </Text>
        </Box>
      ))}
    </Box>
  );
}
