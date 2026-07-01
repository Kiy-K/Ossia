/**
 * TimelinePanel — chronological event log.
 *
 * Primary operator awareness surface. Shows a compact, scrollable log
 * of events in the format:
 *   12:04 Planning
 *   12:05 Spawned researcher
 *   12:06 Tool: search_codebase
 */

import type { TimelineEntry } from "../types";
import { Box, ScrollBox, Text } from "./primitives";

interface TimelinePanelProps {
  entries: TimelineEntry[];
  height: number;
}

export function TimelinePanel({ entries, height }: TimelinePanelProps) {
  return (
    <ScrollBox
      flexDirection="column"
      flexGrow={1}
      width="100%"
    >
      {entries.length === 0 ? (
        <Text attributes={2}>Waiting for input...</Text>
      ) : (
        entries.map((entry) => (
          <Box key={`${entry.time}-${entry.event}-${entry.detail}`} height={1} flexDirection="row" width="100%">
            <Text attributes={2}>{entry.time}</Text>
            <Text> </Text>
            <Text attributes={entry.event.startsWith("tool") ? 1 : undefined}>
              {entry.event}
            </Text>
            {entry.detail ? (
              <>
                <Text attributes={2}> </Text>
                <Text attributes={2}>{entry.detail}</Text>
              </>
            ) : null}
          </Box>
        ))
      )}
    </ScrollBox>
  );
}
