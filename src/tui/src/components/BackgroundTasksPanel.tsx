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
    <box flexDirection="column" width="100%">
      <box height={1} flexDirection="row" width="100%">
        <text bold>Background</text>
        {runningTasks.length > 0 ? (
          <text dim> {runningTasks.length} active</text>
        ) : null}
      </box>

      {visible.map((task: AsyncTaskState, i: number) => (
        <box key={task.task_id} height={1} flexDirection="row" width="100%">
          <text dim>{i + 1}.</text>
          <text> {task.agent_name}</text>
          <text dim>
            {task.status === "failed"
              ? ` failed${task.error ? `: ${task.error}` : ""}`
              : task.status === "completed"
                ? " complete"
                : " running"}
          </text>
        </box>
      ))}
    </box>
  );
}
