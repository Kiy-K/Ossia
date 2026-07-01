/**
 * SubagentPanel — active subagent lifecycle display.
 *
 * Collapsed by default. Shows only active subagents (running / error).
 * Completed subagents are collapsed.
 * Failures persist.
 * Expandable to show scoped messages when focused.
 */

import { useState } from "react";
import type { AppState, SubagentState } from "../types";
import { Box, Text } from "./primitives";

interface SubagentPanelProps {
  state: AppState;
}

export function SubagentPanel({ state }: SubagentPanelProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Show running or error subagents; completed ones collapse away
  const activeAgents = Object.values(state.subagents).filter(
    (s) => s.state === "running" || s.state === "error" || s.state === "interrupted",
  );
  const completedCount = Object.values(state.subagents).filter(
    (s) => s.state === "completed",
  ).length;
  const failedCount = Object.values(state.subagents).filter(
    (s) => s.state === "error",
  ).length;

  if (activeAgents.length === 0 && completedCount === 0) {
    return null;
  }

  return (
    <Box flexDirection="column" width="100%">
      {/* Header */}
      <Box height={1} flexDirection="row" width="100%">
        <Text attributes={1}>Subagents</Text>
        {failedCount > 0 ? <Text> failed:{failedCount}</Text> : null}
        {completedCount > 0 ? <Text attributes={2}> ({completedCount} done)</Text> : null}
      </Box>

      {/* Active agents */}
      {activeAgents.map((agent: SubagentState) => {
        const isExpanded = expanded[agent.name] ?? false;
        return (
          <Box key={agent.name} flexDirection="column" width="100%">
            <Box
              height={1}
              flexDirection="row"
              width="100%"
              // @ts-expect-error OpenTUI handles onClick at runtime
              onClick={() =>
                setExpanded((prev) => ({
                  ...prev,
                  [agent.name]: !prev[agent.name],
                }))
              }
            >
              <Text attributes={2}>
                {isExpanded ? "v" : ">"}
              </Text>
              <Text> </Text>
              <Text
                attributes={agent.state === "error" ? 1 : 2}
              >
                {agent.name}
              </Text>
              <Text> </Text>
              <Text
                attributes={agent.state === "running" ? 2 : undefined}
              >
                {agent.state === "error"
                  ? "failed"
                  : agent.state === "interrupted"
                    ? "interrupted"
                    : "running"}
              </Text>
            </Box>
            {/* Expanded: show scoped messages */}
            {isExpanded && agent.messages.length > 0 ? (
              <Box paddingLeft={2} flexDirection="column">
                {agent.messages.map((msg, i) => (
                  <Text key={i} attributes={2}>
                    {msg}
                  </Text>
                ))}
              </Box>
            ) : null}
            {isExpanded && agent.error ? (
              <Box paddingLeft={2}>
                <Text attributes={2}>error: {agent.error}</Text>
              </Box>
            ) : null}
          </Box>
        );
      })}
    </Box>
  );
}
