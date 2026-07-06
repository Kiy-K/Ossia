/**
 * Side-channel event store for the Ossia Web UI.
 *
 * The Ossia SSE stream carries subagent lifecycle events, tool calls, and
 * ReAct reasoning steps alongside the assistant's text response.  This
 * store captures those "side-channel" events so the SubagentPanel,
 * ToolPanel, and ReActPanel can render them independently of assistant-ui's
 * message runtime.
 *
 * The store uses a plain publish/subscribe pattern so the OssiaAdapter
 * (which lives outside the React tree) can push events, while React
 * components subscribe via ``useSyncExternalStore``.
 */

import { useSyncExternalStore } from "react";
import type { SubagentState, ToolState, ReActStep, OssiaEvent } from "../types";

// ── State shape ─────────────────────────────────────────────────────────────

export interface SideChannelState {
  subagents: Record<string, SubagentState>;
  tools: ToolState[];
  react_steps: ReActStep[];
  run_state: "idle" | "running" | "completed" | "interrupted" | "error";
  thread_id: string;
  connected: boolean;
  /**
   * Pending human-review interrupts. Each entry is a raw action request
   * surfaced by the v3 stream. Cleared when the user resumes the thread
   * (the UI sends `POST /v1/threads/{id}/resume` and the run continues).
   */
  interrupts: Array<Record<string, unknown>>;
  /** Top-level error message surfaced by the SSE layer (network, non-OK). */
  error: string | null;
}

const INITIAL: SideChannelState = {
  subagents: {},
  tools: [],
  react_steps: [],
  run_state: "idle",
  thread_id: "",
  connected: false,
  interrupts: [],
  error: null,
};

// ── Minimal pub/sub store (no external deps) ────────────────────────────────

type Listener = () => void;

function createStore<T>(initial: T) {
  let state = initial;
  const listeners = new Set<Listener>();

  return {
    getState: (): T => state,
    setState: (fn: (prev: T) => T) => {
      state = fn(state);
      listeners.forEach((l) => l());
    },
    subscribe: (listener: Listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    reset: () => {
      state = initial;
      listeners.forEach((l) => l());
    },
  };
}

export const sideChannelStore = createStore<SideChannelState>(INITIAL);

// ── React hook ───────────────────────────────────────────────────────────────

/** Subscribe to the side channel store from any React component. */
export function useSideChannel(): SideChannelState {
  return useSyncExternalStore(
    sideChannelStore.subscribe,
    sideChannelStore.getState,
  );
}

// ── Event dispatcher (called by the OssiaAdapter) ───────────────────────────

const now = () =>
  new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

/** Strip Python object repr leaks from text. */
function cleanText(raw: string): string {
  return /^<[\w.]+ object at 0x[0-9a-f]+>$/.test(raw)
    ? "[content available]"
    : raw;
}

/** Dispatch a raw SSE event into the side channel store. */
export function dispatchSideEvent(event: OssiaEvent): void {
  const time = now();

  sideChannelStore.setState((prev) => {
    // ── Thread ID (capture on first event) ──────────────────────────
    let next = event.thread_id && !prev.thread_id
      ? { ...prev, thread_id: event.thread_id, connected: true }
      : { ...prev, connected: true };

    switch (event.type) {
      // ── Run state ────────────────────────────────────────────────
      case "message_started":
        next = { ...next, run_state: "running" as const };
        break;

      case "message_completed": {
        next = { ...next, run_state: "running" as const };
        // Add a ReAct thought step for the completed assistant message
        const raw = String(event.data.text ?? "");
        const cleaned = cleanText(raw);
        if (cleaned) {
          const thoughtStep: ReActStep = {
            kind: "thought",
            content: cleaned,
            time,
          };
          return {
            ...next,
            react_steps: [...next.react_steps, thoughtStep],
          };
        }
        break;
      }

      case "complete": {
        const interrupted = Boolean(event.data.interrupted);
        next = {
          ...next,
          run_state: interrupted
            ? ("interrupted" as const)
            : ("completed" as const),
          // If the run finished without an interrupt, clear any stale
          // pending decisions (e.g. from a previously-resumed run).
          interrupts: interrupted ? next.interrupts : [],
        };
        break;
      }
      case "error":
        next = { ...next, run_state: "error" as const, interrupts: [] };
        break;
      case "interrupt": {
        // Each entry in the SSE payload is { action_requests, review_configs }.
        // Flatten to the list of action requests so the UI can render one
        // card per pending approval.
        const incoming = Array.isArray(event.data.interrupts)
          ? (event.data.interrupts as Array<Record<string, unknown>>)
          : [];
        const actionRequests = incoming.flatMap((entry) => {
          const ar = entry.action_requests;
          return Array.isArray(ar) ? ar : [];
        });
        next = {
          ...next,
          run_state: "interrupted" as const,
          interrupts: actionRequests,
        };
        break;
      }

      // ── Subagents ────────────────────────────────────────────────
      case "subagent_spawned": {
        const name = String(event.data.name ?? "unknown");
        return {
          ...next,
          subagents: {
            ...next.subagents,
            [name]: {
              name,
              state: "running" as const,
              startedAt: Date.now(),
            },
          },
        };
      }
      case "subagent_completed": {
        const name = String(event.data.name ?? "");
        const existing = next.subagents[name];
        if (!existing) return next;
        return {
          ...next,
          subagents: {
            ...next.subagents,
            [name]: { ...existing, state: "completed" as const },
          },
        };
      }
      case "subagent_failed": {
        const name = String(event.data.name ?? "");
        const err = String(event.data.error ?? "unknown error");
        const existing = next.subagents[name];
        if (!existing) return next;
        return {
          ...next,
          subagents: {
            ...next.subagents,
            [name]: { ...existing, state: "error" as const, error: err },
          },
        };
      }
      case "subagent_interrupted": {
        const name = String(event.data.name ?? "");
        const existing = next.subagents[name];
        if (!existing) return next;
        return {
          ...next,
          subagents: {
            ...next.subagents,
            [name]: { ...existing, state: "interrupted" as const },
          },
        };
      }

      // ── Tool calls ────────────────────────────────────────────────
      case "tool_started": {
        const toolName = String(event.data.name ?? "unknown");
        const toolInput =
          (event.data.input as Record<string, unknown>) ?? {};
        const tool: ToolState = {
          name: toolName,
          state: "running" as const,
          input: toolInput,
          startedAt: Date.now(),
        };
        const actionStep: ReActStep = {
          kind: "action" as const,
          tool: toolName,
          input: toolInput,
          time,
        };
        return {
          ...next,
          tools: [...next.tools, tool],
          react_steps: [...next.react_steps, actionStep],
        };
      }
      case "tool_completed": {
        const toolName = String(event.data.name ?? "");
        const obsStep: ReActStep = {
          kind: "observation" as const,
          tool: toolName,
          output: event.data.output,
          success: true,
          time,
        };
        return {
          ...next,
          tools: next.tools.map((t) =>
            t.name === toolName && t.state === "running"
              ? { ...t, state: "completed" as const, output: event.data.output }
              : t,
          ),
          react_steps: [...next.react_steps, obsStep],
        };
      }
      case "tool_failed": {
        const toolName = String(event.data.name ?? "");
        const err = String(event.data.error ?? "unknown error");
        const obsStep: ReActStep = {
          kind: "observation" as const,
          tool: toolName,
          output: null,
          success: false,
          error: err,
          time,
        };
        return {
          ...next,
          tools: next.tools.map((t) =>
            t.name === toolName && t.state === "running"
              ? { ...t, state: "failed" as const, error: err }
              : t,
          ),
          react_steps: [...next.react_steps, obsStep],
        };
      }

      // ── Pipelines & Async tasks (future) ──────────────────────────
      case "pipeline_started":
      case "pipeline_step_started":
      case "pipeline_step_completed":
      case "pipeline_step_failed":
      case "pipeline_completed":
      case "pipeline_failed":
      case "async_task_started":
      case "async_task_updated":
      case "async_task_completed":
      case "async_task_failed":
      case "async_task_cancelled":
        return next;

      default:
        return next;
    }

    return next;
  });
}

/** Reset the store to initial state (e.g. on new chat or config change). */
export function resetSideChannel(): void {
  sideChannelStore.reset();
}

/** Clear pending interrupts (called after the user resumes). */
export function clearInterrupts(): void {
  sideChannelStore.setState((prev) => ({ ...prev, interrupts: [] }));
}

/** Surface a top-level error from the SSE layer (non-OK response, network). */
export function reportError(message: string): void {
  sideChannelStore.setState((prev) => ({
    ...prev,
    run_state: "error" as const,
    error: message,
  }));
}

/** Clear the top-level error (e.g. on next user send). */
export function clearError(): void {
  sideChannelStore.setState((prev) => ({ ...prev, error: null }));
}
