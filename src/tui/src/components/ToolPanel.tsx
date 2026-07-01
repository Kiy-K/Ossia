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
import { Box, Text } from "./primitives";

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
    <Box flexDirection="column" width="100%">
      {/* Header */}
      <Box height={1} flexDirection="row" width="100%">
        <Text attributes={1}>Tools</Text>
        {completedCount > 0 ? (
          <Text attributes={2}> {completedCount} completed</Text>
        ) : null}
      </Box>

      {/* Active / failed tools */}
      {activeTools.map((tool: ToolState) => (
        <Box key={tool.name} height={1} flexDirection="row" width="100%">
          <Text attributes={2}>  </Text>
          {tool.state === "running" ? (
            <Text attributes={2}>... {tool.name}</Text>
          ) : (
            <>
              <Text attributes={1}>failed {tool.name}</Text>
              <Text attributes={2}>{tool.error ? ` (${tool.error})` : ""}</Text>
            </>
          )}
        </Box>
      ))}
    </Box>
  );
}
