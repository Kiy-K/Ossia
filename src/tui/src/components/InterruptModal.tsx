/**
 * InterruptModal — blocking overlay for human-in-the-loop interrupts.
 *
 * Displayed only when:
 *  - approval needed
 *  - missing input
 *  - user intervention required
 *
 * Must block visually. Uses absolute positioning to overlay the entire
 * renderable area.
 */

import type { AppState } from "../types";

interface InterruptModalProps {
  state: AppState;
}

export function InterruptModal({ state }: InterruptModalProps) {
  if (!state.interrupts) return null;

  const interruptList = state.interrupts.interrupts ?? [];

  return (
    <box
      position="absolute"
      left={0}
      top={0}
      width="100%"
      height="100%"
      flexDirection="column"
      justifyContent="center"
      alignItems="center"
    >
      {/* Scrim background */}
      <box
        position="absolute"
        left={0}
        top={0}
        width="100%"
        height="100%"
      />
      {/* Modal box */}
      <box
        flexDirection="column"
        padding={1}
        width={60}
      >
        <text bold>⏸ Interrupted</text>
        <box height={1} />
        <text dim>The run requires intervention:</text>
        <box height={1} />
        {interruptList.length > 0
          ? interruptList.map((ir: Record<string, unknown>, i: number) => (
              <box key={i} flexDirection="column" paddingLeft={1}>
                <text dim>
                  {i + 1}. {JSON.stringify(ir).slice(0, 120)}
                </text>
              </box>
            ))
          : null}
        <box height={1} />
        <text dim>──────────────────────────────</text>
        <text dim>Use the API to resume or cancel</text>
      </box>
    </box>
  );
}
