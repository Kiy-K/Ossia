/**
 * Ossia TUI — Main application component.
 *
 * Layout:
 *   App
 *   ├── StatusBar
 *   ├── TimelinePanel (flex grow, scrollable)
 *   ├── SubagentPanel (collapsible, shown when active)
 *   ├── ToolPanel (shown when active)
 *   ├── ReActPanel (shown when agent has started reasoning)
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
import { useCallback, useEffect, useRef, useState } from "react";
import { useOnResize } from "@opentui/react";
import { reduceEvent, initialAppState } from "./events/reducer";
import { sendMessage } from "./events/stream";
import { readActiveSession, writeActiveSession, clearActiveSession } from "./session";
import type { AppState } from "./types";
import { BackgroundTasksPanel } from "./components/BackgroundTasksPanel";
import { Box } from "./components/primitives";
import { InputBar } from "./components/InputBar";
import { InterruptModal } from "./components/InterruptModal";
import { ReActPanel } from "./components/ReActPanel";
import { StatusBar } from "./components/StatusBar";
import { SubagentPanel } from "./components/SubagentPanel";
import { TimelinePanel } from "./components/TimelinePanel";
import { ToolPanel } from "./components/ToolPanel";
/** Default connection parameters (overridable via env). */
const API_URL = process.env.OSSIA_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.OSSIA_API_KEY ?? "dev";
/** Base height reserved for always-visible panels (StatusBar + InputBar + margins). */
const BASE_PANEL_HEIGHT = 6;
/** Extra rows added when ReActPanel is visible (header + up to 3 step rows). */
const REACT_PANEL_HEIGHT = 4;

/**
 * Calculate the height available for the TimelinePanel given the terminal
 * height and the number of ReAct steps accumulated so far.
 *
 * When the ReAct panel is visible (1+ steps), an extra REACT_PANEL_HEIGHT
 * rows are reserved. Otherwise only the fixed BASE_PANEL_HEIGHT is reserved.
 */
export function computeTimelineHeight(termHeight: number, reactStepCount: number): number {
  const reactPanelVisible = reactStepCount > 0;
  const bottomPanelHeight = BASE_PANEL_HEIGHT + (reactPanelVisible ? REACT_PANEL_HEIGHT : 0);
  return termHeight - bottomPanelHeight;
}

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

  // ── Session initialisation from cache ────────────────────────────────
  const [sessionTopic, setSessionTopic] = useState<string>("default");
  const [isNewSession, setIsNewSession] = useState<boolean>(false);

  useEffect(() => {
    readActiveSession().then((cached) => {
      if (cached) {
        threadIdRef.current = cached.session_id;
        setSessionTopic(cached.topic);
        setState((prev) => ({
          ...prev,
          thread_id: cached.session_id,
        }));
      }
    });
  }, []);

  // ── Handle New Chat ──────────────────────────────────────────────────
  const handleNewChat = useCallback(() => {
    // Cancel any running request
    abortRef.current?.abort();
    threadIdRef.current = "";
    setSessionTopic("default");
    setIsNewSession(true);
    setState(initialAppState());
    // Clear the cache so next launch starts fresh
    clearActiveSession();
  }, []);

  // ── Send message ─────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (message: string) => {
      // Cancel any previous run
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const abortController = new AbortController();
      abortRef.current = abortController;

      // Determine thread ID / session flags
      const existingThreadId = threadIdRef.current;
      const willNewSession = isNewSession;

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
            threadId: willNewSession ? undefined : (existingThreadId || undefined),
            sessionTopic: sessionTopic !== "default" ? sessionTopic : undefined,
            newSession: willNewSession,
          },
          abortController.signal,
        );
        // Reset the new-session flag now that we've sent the request
        setIsNewSession(false);

        for await (const event of stream) {
          if (abortController.signal.aborted) break;
          // Store thread_id from the first event that carries it
          if (event.thread_id && !threadIdRef.current) {
            threadIdRef.current = event.thread_id;
            // Persist to .kilocode/active_session.json
            void writeActiveSession({
              session_id: event.thread_id,
              topic: sessionTopic,
              project_context: "",
              created_at: new Date().toISOString(),
              is_random: willNewSession,
            });
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
    [sessionTopic, isNewSession], // Deps needed to read current values at call time
  );
  // Height available for the timeline — expand only when ReActPanel is visible
  const timelineHeight = computeTimelineHeight(termHeight, state.react_steps?.length ?? 0);
  return (
    <Box
      flexDirection="column"
      width="100%"
      height="100%"
      alignItems="stretch"
    >
      {/* StatusBar — always visible */}
      <StatusBar state={state} />
      {/* TimelinePanel — takes remaining vertical space */}
      <Box flexGrow={1} width="100%">
        <TimelinePanel
          entries={state.timeline}
          height={timelineHeight}
        />
      </Box>
      {/* SubagentPanel — shown when subagents are active */}
      <SubagentPanel state={state} />
      {/* ToolPanel — shown when tools are active */}
      <ToolPanel state={state} />
      {/* ReActPanel — shown when the agent has started reasoning */}
      <ReActPanel state={state} />
      {/* BackgroundTasksPanel — shown when async tasks exist */}
      <BackgroundTasksPanel state={state} />
      {/* InterruptModal — overlays everything when active */}
      <InterruptModal state={state} />
      {/* InputBar — always visible at bottom */}
      <InputBar
        onSubmit={handleSubmit}
        disabled={state.run_state === "running"}
        sessionTopic={sessionTopic}
      />
    </Box>
  );
}
