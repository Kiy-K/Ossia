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

interface TimelinePanelProps {
  entries: TimelineEntry[];
  height: number;
}

export function TimelinePanel({ entries, height }: TimelinePanelProps) {
  return (
    <scrollbox
      flexDirection="column"
      flexGrow={1}
      width="100%"
    >
      {entries.length === 0 ? (
        <text dim>Waiting for input...</text>
      ) : (
        entries.map((entry, i) => (
          <box key={i} height={1} flexDirection="row" width="100%">
            <text dim>{entry.time}</text>
            <text> </text>
            <text bold={entry.event.startsWith("tool")}>
              {entry.event}
            </text>
            {entry.detail ? (
              <>
                <text dim> </text>
                <text dim>{entry.detail}</text>
              </>
            ) : null}
          </box>
        ))
      )}
    </scrollbox>
  );
}
