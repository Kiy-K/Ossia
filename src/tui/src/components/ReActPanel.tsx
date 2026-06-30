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
        <box height={1} flexDirection="row" width="100%">
          <text attributes={2}>{step.time} </text>
          <text attributes={1}>[Think] </text>
          <text>{truncate(step.content)}</text>
        </box>
      );
    case "action":
      return (
        <box height={1} flexDirection="row" width="100%">
          <text attributes={2}>{step.time} </text>
          <text attributes={1}>[Act]   </text>
          <text>{step.tool}  </text>
          <text attributes={2}>{truncate(JSON.stringify(step.input), 40)}</text>
        </box>
      );
    case "observation":
      return (
        <box height={1} flexDirection="row" width="100%">
          <text attributes={2}>{step.time} </text>
          <text attributes={1}>[Obs {step.success ? "✓" : "✗"}] </text>
          <text attributes={2}>
            {step.success
              ? truncate(
                  typeof step.output === "string"
                    ? step.output
                    : (() => { try { return JSON.stringify(step.output) ?? ""; } catch { return String(step.output); } })()
                )
              : truncate(step.error ?? "error")}
          </text>
        </box>
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
    <box flexDirection="column" width="100%">
      {/* Header row */}
      <box height={1} flexDirection="row" width="100%">
        <text attributes={2}>── ReAct </text>
        <text attributes={2}>
          ({total} step{total !== 1 ? "s" : ""}
          {total > MAX_STEPS ? `, showing last ${MAX_STEPS}` : ""})
        </text>
      </box>
      {/* Step rows */}
      {visible.map((step, i) => (
        <StepRow key={i} step={step} />
      ))}
    </box>
  );
}
