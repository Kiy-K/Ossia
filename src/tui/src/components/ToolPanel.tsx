/**
 * ToolPanel — active tool call display.
 *
 * Shows active tools only. Collapses completed tools into a count.
 * Failures surface prominently.
 *
 * Bad:
 *   ✓ tool1
 *   ✓ tool2
 *   ✓ tool3
 *
 * Good:
 *   3 tools completed
 *
 * Failure:
 *   tool failed: run_tests(timeout)
 */

import type { AppState, ToolState } from "../types";

interface ToolPanelProps {
  state: AppState;
}

export function ToolPanel({ state }: ToolPanelProps) {
  // Running & failed tools shown individually
  const activeTools = state.tools.filter(
    (t) => t.state === "running" || t.state === "failed",
  );
  const completedCount = state.tools.filter(
    (t) => t.state === "completed",
  ).length;

  if (activeTools.length === 0 && completedCount === 0) {
    return null;
  }

  return (
    <box flexDirection="column" width="100%">
      {/* Header */}
      <box height={1} flexDirection="row" width="100%">
        <text bold>Tools</text>
        {completedCount > 0 ? (
          <text dim> {completedCount} completed</text>
        ) : null}
      </box>

      {/* Active / failed tools */}
      {activeTools.map((tool: ToolState) => (
        <box key={tool.name} height={1} flexDirection="row" width="100%">
          <text dim>  </text>
          {tool.state === "running" ? (
            <text dim>... {tool.name}</text>
          ) : (
            <>
              <text bold>failed {tool.name}</text>
              <text dim>{tool.error ? ` (${tool.error})` : ""}</text>
            </>
          )}
        </box>
      ))}
    </box>
  );
}
