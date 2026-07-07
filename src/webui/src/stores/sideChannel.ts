/**
 * Slim side channel — only run_state and interrupts.
 *
 * The v3 projector handles per-channel data: messages flow through
 * assistant-ui's message runtime, tool_calls through tool-call parts,
 * subagents through content metadata. This store tracks only
 * run lifecycle state needed by InterruptPrompt.
 */

import { useSyncExternalStore } from "react";

// ── State shape ─────────────────────────────────────────────────────────────

export interface SideChannelState {
  run_state: "idle" | "running" | "completed" | "interrupted" | "error";
  interrupts: Array<Record<string, unknown>>;
  error: string | null;
  thread_id: string;
  connected: boolean;
}

const INITIAL: SideChannelState = {
  run_state: "idle",
  interrupts: [],
  error: null,
  thread_id: "",
  connected: false,
};

// ── Minimal pub/sub store ───────────────────────────────────────────────────

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

// ── React hook ──────────────────────────────────────────────────────────────

export function useSideChannel(): SideChannelState {
  return useSyncExternalStore(
    sideChannelStore.subscribe,
    sideChannelStore.getState,
  );
}

// ── Actions ─────────────────────────────────────────────────────────────────

export function resetSideChannel(): void {
  sideChannelStore.reset();
}

export function clearInterrupts(): void {
  sideChannelStore.setState((prev) => ({ ...prev, interrupts: [] }));
}

export function reportError(message: string): void {
  sideChannelStore.setState((prev) => ({
    ...prev,
    run_state: "error" as const,
    error: message,
  }));
}

export function clearError(): void {
  sideChannelStore.setState((prev) => ({ ...prev, error: null }));
}
