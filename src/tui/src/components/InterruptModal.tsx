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
import { Box, Text } from "./primitives";

interface InterruptModalProps {
  state: AppState;
}

export function InterruptModal({ state }: InterruptModalProps) {
  if (!state.interrupts) return null;

  const interruptList = state.interrupts.interrupts ?? [];

  return (
    <Box
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
      <Box
        position="absolute"
        left={0}
        top={0}
        width="100%"
        height="100%"
      />
      {/* Modal box */}
      <Box
        flexDirection="column"
        padding={1}
        width={60}
      >
        <Text attributes={1}>⏸ Interrupted</Text>
        <Box height={1} />
        <Text attributes={2}>The run requires intervention:</Text>
        <Box height={1} />
        {interruptList.length > 0
          ? interruptList.map((ir: Record<string, unknown>, i: number) => (
              <Box key={JSON.stringify(ir)} flexDirection="column" paddingLeft={1}>
                <Text attributes={2}>
                  {i + 1}. {JSON.stringify(ir).slice(0, 120)}
                </Text>
              </Box>
            ))
          : null}
        <Box height={1} />
        <Text attributes={2}>──────────────────────────────</Text>
        <Text attributes={2}>Use the API to resume or cancel</Text>
      </Box>
    </Box>
  );
}
