/**
 * Ossia TUI — Main application component.
 *
 * Layout:
 *   App
 *   ├── StatusBar
 *   ├── TimelinePanel (flex grow, scrollable)
 *   ├── SubagentPanel (collapsible, shown when active)
 *   ├── ToolPanel (shown when active)
 *   ├── BackgroundTasksPanel (shown when active)
 *   ├── InterruptModal (overlay, shown on interrupt)
 *   └── InputBar (fixed at bottom)
 *
 * Data flow:
 *   SSE stream → parseSSEStream → reduceEvent → AppState → render
 *
 * Components are pure render-only. State mutations happen exclusively
 * in the reducer. Event parsing lives in events/ modules.
 */

import { useCallback, useRef, useState } from "react";

import { useOnResize } from "@opentui/react";

import { reduceEvent, initialAppState } from "./events/reducer";
import { sendMessage } from "./events/stream";
import type { AppState } from "./types";

import { BackgroundTasksPanel } from "./components/BackgroundTasksPanel";
import { InputBar } from "./components/InputBar";
import { InterruptModal } from "./components/InterruptModal";
import { StatusBar } from "./components/StatusBar";
import { SubagentPanel } from "./components/SubagentPanel";
import { TimelinePanel } from "./components/TimelinePanel";
import { ToolPanel } from "./components/ToolPanel";

/** Default connection parameters (overridable via env). */
const API_URL = process.env.OSSIA_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.OSSIA_API_KEY ?? "dev";

/**
 * Fixed-height panels: StatusBar (1) + 3 optional panels (~5 max).
 * This buffer ensures the timeline doesn't overlap with bottom panels.
 */
const BOTTOM_PANEL_HEIGHT = 6;

export function App() {
  const [state, setState] = useState<AppState>(initialAppState);
  const [termHeight, setTermHeight] = useState(24);

  // Ref for thread_id to avoid stale-closure bugs in the async loop
  const threadIdRef = useRef<string>("");

  // Abort controller for the current stream
  const abortRef = useRef<AbortController | null>(null);

  // Track terminal resize events via OpenTUI's built-in hook
  useOnResize((_width: number, height: number) => {
    setTermHeight(Math.max(height, 12));
  });

  const handleSubmit = useCallback(
    async (message: string) => {
      // Cancel any previous run
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const abortController = new AbortController();
      abortRef.current = abortController;

      // Reset state for new run, preserving thread_id
      const existingThreadId = threadIdRef.current;
      setState((prev) => ({
        ...initialAppState(),
        thread_id: existingThreadId || prev.thread_id,
        run_state: "running",
        user_input: message,
      }));

      try {
        const stream = sendMessage(
          message,
          {
            apiUrl: API_URL,
            apiKey: API_KEY,
            threadId: existingThreadId || undefined,
          },
          abortController.signal,
        );

        for await (const event of stream) {
          if (abortController.signal.aborted) break;

          // Store thread_id from the first event that carries it
          if (event.thread_id && !threadIdRef.current) {
            threadIdRef.current = event.thread_id;
          }

          setState((prev) => {
            try {
              return reduceEvent(prev, event);
            } catch {
              return prev;
            }
          });
        }
      } catch (error) {
        if (abortController.signal.aborted) return;
        const msg = error instanceof Error ? error.message : "Connection failed";
        setState((prev) => ({
          ...prev,
          run_state: "error",
          error: msg,
          timeline: [
            ...prev.timeline,
            { time: "--", event: "Error", detail: msg },
          ],
        }));
      }
    },
    [], // No dependencies — threadIdRef handles closure issues
  );

  // Height available for the timeline
  const timelineHeight = termHeight - BOTTOM_PANEL_HEIGHT;

  return (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      alignItems="stretch"
    >
      {/* StatusBar — always visible */}
      <StatusBar state={state} />

      {/* TimelinePanel — takes remaining vertical space */}
      <box flexGrow={1} width="100%">
        <TimelinePanel
          entries={state.timeline}
          height={timelineHeight}
        />
      </box>

      {/* SubagentPanel — shown when subagents are active */}
      <SubagentPanel state={state} />

      {/* ToolPanel — shown when tools are active */}
      <ToolPanel state={state} />

      {/* BackgroundTasksPanel — shown when async tasks exist */}
      <BackgroundTasksPanel state={state} />

      {/* InterruptModal — overlays everything when active */}
      <InterruptModal state={state} />

      {/* InputBar — always visible at bottom */}
      <InputBar
        onSubmit={handleSubmit}
        disabled={state.run_state === "running"}
      />
    </box>
  );
}
