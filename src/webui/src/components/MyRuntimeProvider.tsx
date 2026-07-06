/**
 * Ossia assistant-ui runtime provider.
 *
 * Wires three layers:
 *   1. ``useExternalStoreRuntime`` (single-thread) — drives the
 *      assistant-ui message loop while the adapter manages message
 *      history loading, SSE streaming, and state.
 *   2. ``Tools({ toolkit })`` — registers the 11-tool ``ossiaToolkit``
 *      so backend tool-call SSE events map to custom Tool UI parts.
 *   3. ``ControlsContext`` — exposes ``useOssiaControls().resume()``
 *      for HITL resume (the one piece of thread-level state the
 *      assistant-ui runtime does not expose itself).
 *
 * TODO: migrate to ``useRemoteThreadListRuntime`` for built-in
 * thread list management. The backend endpoints (``/v1/threads``
 * POST/PATCH/unarchive) and the ``ossiaThreadListAdapter`` are
 * already in place; the missing piece is a ``history`` adapter on
 * the inner ``ExternalStoreRuntime`` so the thread-list runtime can
 * trigger per-thread history loads. See ``threadList/ossiaAdapter.ts``.
 */

"use client";

import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  Tools,
  useAui,
} from "@assistant-ui/react";
import type { Config } from "../types";
import { useOssiaRuntime } from "../runtimes/ossia-external-store";
import { ossiaToolkit } from "../tools/ossia-toolkit";
import { resetSideChannel } from "../stores/sideChannel";

// ── Controls context ────────────────────────────────────────────────────────

export interface OssiaControls {
  /** Switch to a different thread (empty string = new chat). */
  switchThread: (threadId: string) => Promise<void>;
  /**
   * Resume a thread paused on a human-review interrupt. ``decisions``
   * follows the backend ``ResumeDecision`` shape: ``{type, message?,
   * edited_action?}`` where ``type`` is ``"approve" | "edit" | "reject"
   * | "respond"``.
   */
  resume: (decisions: Array<Record<string, unknown>>) => Promise<void>;
}

const ControlsContext = createContext<OssiaControls | null>(null);

/** Read the Ossia runtime controls (resume) from a child. */
export function useOssiaControls(): OssiaControls {
  const ctx = useContext(ControlsContext);
  if (!ctx) {
    throw new Error("useOssiaControls must be used inside <MyRuntimeProvider>");
  }
  return ctx;
}

// ── Provider component ──────────────────────────────────────────────────────

export function MyRuntimeProvider({
  config,
  children,
}: {
  config: Config;
  children: ReactNode;
}) {
  const { runtime, switchThread, resume } = useOssiaRuntime(config);
  const aui = useAui({ tools: Tools({ toolkit: ossiaToolkit }) });

  useEffect(() => {
    resetSideChannel();
  }, [config.apiUrl, config.apiKey]);

  const controls = useMemo<OssiaControls>(
    () => ({ switchThread, resume }),
    [switchThread, resume],
  );

  return (
    <ControlsContext.Provider value={controls}>
      <AssistantRuntimeProvider runtime={runtime} aui={aui}>
        {children}
      </AssistantRuntimeProvider>
    </ControlsContext.Provider>
  );
}
