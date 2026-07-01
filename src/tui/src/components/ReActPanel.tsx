/**
 * ReActPanel — Visualises the agent's Thought → Action → Observation loop.
 *
 * Hidden until the first ReAct step arrives (returns null when empty).
 * Shows the most recent MAX_STEPS steps so the panel stays compact.
 *
 * Step labels:
 *   [Think]  — assistant reasoning (from message_completed)
 *   [Act]    — tool invocation    (from tool_started)
 *   [Obs ✓]  — successful result  (from tool_completed)
 *   [Obs ✗]  — failed result      (from tool_failed)
 *
 * Follows the same conditional-render pattern as SubagentPanel and
 * ToolPanel — no permanent screen space is reserved when idle.
 */
import type { AppState, ReActStep } from "../types";
import { Box, Text } from "./primitives";

/** Maximum number of steps to show at once. Keeps the panel compact. */
const MAX_STEPS = 3;

interface ReActPanelProps {
  state: AppState;
}

function truncate(s: string, max = 50): string {
  const clean = s.replace(/[\n\r\t]/g, " ").replace(/\s+/g, " ").trim();
  return clean.length > max ? clean.slice(0, max) + "…" : clean;
}

/** Renders a single labeled step row. */
function StepRow({ step }: { step: ReActStep }) {
  switch (step.kind) {
    case "thought":
      return (
        <Box height={1} flexDirection="row" width="100%">
          <Text attributes={2}>{step.time} </Text>
          <Text attributes={1}>[Think] </Text>
          <Text>{truncate(step.content)}</Text>
        </Box>
      );
    case "action":
      return (
        <Box height={1} flexDirection="row" width="100%">
          <Text attributes={2}>{step.time} </Text>
          <Text attributes={1}>[Act]   </Text>
          <Text>{step.tool}  </Text>
          <Text attributes={2}>{truncate(JSON.stringify(step.input), 40)}</Text>
        </Box>
      );
    case "observation":
      return (
        <Box height={1} flexDirection="row" width="100%">
          <Text attributes={2}>{step.time} </Text>
          <Text attributes={1}>[Obs {step.success ? "✓" : "✗"}] </Text>
          <Text attributes={2}>
            {step.success
              ? truncate(
                  typeof step.output === "string"
                    ? step.output
                    : (() => { try { return JSON.stringify(step.output) ?? ""; } catch { return String(step.output); } })()
                )
              : truncate(step.error ?? "error")}
          </Text>
        </Box>
      );
  }
}

/**
 * ReActPanel component.
 *
 * Returns null (renders nothing) when there are no steps yet, so it
 * doesn't consume screen space on idle or during the first message.
 */
export function ReActPanel({ state }: ReActPanelProps) {
  const steps = state.react_steps ?? [];
  if (steps.length === 0) return null;

  const visible = steps.slice(-MAX_STEPS);
  const total = steps.length;

  return (
    <Box flexDirection="column" width="100%">
      {/* Header row */}
      <Box height={1} flexDirection="row" width="100%">
        <Text attributes={2}>── ReAct </Text>
        <Text attributes={2}>
          ({total} step{total !== 1 ? "s" : ""}
          {total > MAX_STEPS ? `, showing last ${MAX_STEPS}` : ""})
        </Text>
      </Box>
      {/* Step rows */}
      {visible.map((step) => (
        <StepRow key={`${step.time}-${step.kind}`} step={step} />
      ))}
    </Box>
  );
}
