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
    <box flexDirection="column" width="100%">
      {/* Header */}
      <box height={1} flexDirection="row" width="100%">
        <text attributes={1}>Subagents</text>
        {failedCount > 0 ? <text> failed:{failedCount}</text> : null}
        {completedCount > 0 ? <text attributes={2}> ({completedCount} done)</text> : null}
      </box>

      {/* Active agents */}
      {activeAgents.map((agent: SubagentState) => {
        const isExpanded = expanded[agent.name] ?? false;
        return (
          <box key={agent.name} flexDirection="column" width="100%">
            <              box
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
              <text attributes={2}>
                {isExpanded ? "v" : ">"}
              </text>
              <text> </text>
              <text
                attributes={agent.state === "error" ? 1 : 2}
              >
                {agent.name}
              </text>
              <text> </text>
              <text
                attributes={agent.state === "running" ? 2 : undefined}
              >
                {agent.state === "error"
                  ? "failed"
                  : agent.state === "interrupted"
                    ? "interrupted"
                    : "running"}
              </text>
            </box>
            {/* Expanded: show scoped messages */}
            {isExpanded && agent.messages.length > 0 ? (
              <box paddingLeft={2} flexDirection="column">
                {agent.messages.map((msg, i) => (
                  <text key={i} attributes={2}>
                    {msg}
                  </text>
                ))}
              </box>
            ) : null}
            {isExpanded && agent.error ? (
              <box paddingLeft={2}>
                <text attributes={2}>error: {agent.error}</text>
              </box>
            ) : null}
          </box>
        );
      })}
    </box>
  );
}
