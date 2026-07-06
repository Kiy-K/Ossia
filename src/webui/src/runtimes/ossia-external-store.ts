/**
 * Ossia ExternalStoreRuntime adapter.
 *
 * Wraps the Ossia SSE streaming backend in an ``ExternalStoreAdapter``
 * so ``useExternalStoreRuntime`` can drive the assistant-ui message loop
 * while the adapter manages message history loading, streaming, and state.
 *
 * Responsibilities:
 *   - **History**: On mount, loads past thread messages from
 *     ``GET /v1/threads/{thread_id}/history`` so the UI resumes the
 *     conversation from the last checkpoint.
 *   - **Streaming**: On ``onNew``, POSTs to ``/v1/chat/stream`` and
 *     incrementally updates the message list as text and tool-call
 *     events arrive via SSE.
 *   - **Tool calls**: Maps SSE tool events to ``tool-call`` content parts
 *     so the registered Tool UI components render inline.
 *   - **Side channels**: Dispatches raw Ossia events to the side-channel
 *     store for the Subagent/Tool/ReAct panels.
 *
 * Architecture:
 *   - ``handleNew`` (onNew callback) creates the user message and an
 *     assistant placeholder, then delegates all SSE processing to
 *     ``streamResponse`` so there is no duplicated SSE logic.
 *   - ``handleReload`` reads the latest messages from a ref to avoid
 *     stale closures, trims back to the parent, then also calls
 *     ``streamResponse``.
 *   - ``streamResponse`` is the single function that owns the SSE fetch,
 *     event parsing, tool tracking, and incremental message updates.
 *     It accepts an optional ``existingAssistantId`` so ``handleNew``
 *     can reuse its placeholder ID.
 */

"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import {
  useExternalStoreRuntime,
  useAui,
  Tools,
  type AssistantRuntime,
  type ExternalStoreAdapter,
  type ThreadMessage,
  type ThreadMessageLike,
  type AppendMessage,
  fromThreadMessageLike,
  generateId,
} from "@assistant-ui/react";
import type { Config } from "../types";
import { parseSSEStream } from "../stream";
import { ossiaToolkit } from "../tools/ossia-toolkit";
import {
  clearError,
  clearInterrupts,
  dispatchSideEvent,
  reportError,
  resetSideChannel,
  sideChannelStore,
} from "../stores/sideChannel";

// ── Constants ───────────────────────────────────────────────────────────────

let _toolCallCounter = 0;
function nextToolCallId(): string {
  _toolCallCounter += 1;
  return `ossia-tool-${_toolCallCounter}`;
}

// ── Active tool call tracking ───────────────────────────────────────────────

interface ActiveToolCall {
  toolName: string;
  toolCallId: string;
  args: Record<string, unknown>;
  result?: unknown;
  isError?: boolean;
}

// ── Dispatch action type (shared between the hook and streamResponse) ───────

type OssiaAction =
  | { type: "set-messages"; messages: ThreadMessage[] }
  | { type: "set-running"; isRunning: boolean }
  | { type: "update-assistant"; updater: (messages: ThreadMessage[]) => ThreadMessage[] }
  | {
      type: "update-message";
      id: string;
      content: ThreadMessageLike["content"];
      status?:
        | { type: "running" }
        | { type: "complete" }
        | { type: "incomplete"; reason: "cancelled" | "length" | "content-filter" | "other" | "error" };
    };

// ── Helpers ─────────────────────────────────────────────────────────────────

function cleanText(raw: string): string {
  return /^<[\w.]+ object at 0x[0-9a-f]+>$/.test(raw)
    ? "[content available]"
    : raw;
}

/** Shape returned by ``GET /v1/threads/{id}/history``. */
interface ThreadHistoryResponse {
  thread_id: string;
  messages: Array<{
    role: string;
    content: string;
    tool_calls?: Array<{ id: string; name: string; args: Record<string, unknown> }>;
    tool_call_id?: string | null;
    name?: string | null;
  }>;
}

/** Convert a backend ``ChatMessage`` into a ``ThreadMessageLike`` for injection. */
function chatMessageToMessageLike(
  msg: ThreadHistoryResponse["messages"][number],
): ThreadMessageLike | null {
  if (msg.role === "user") {
    return {
      role: "user",
      content: [{ type: "text" as const, text: msg.content || "" }],
      metadata: { custom: {} },
    };
  }
  // Skip tool messages — the backend serializes them as raw Python repr
  // (e.g. ``query='...' results=[...]``) which would render as garbage
  // text. Tool cards are reconstructed from the surrounding assistant
  // message's ``tool_calls`` instead.
  if (msg.role === "tool") {
    return null;
  }
  const parts: ThreadMessageLike["content"] = [];
  if (msg.content) {
    parts.push({ type: "text" as const, text: msg.content });
  }
  for (const tc of msg.tool_calls ?? []) {
    parts.push({
      type: "tool-call" as const,
      toolCallId: tc.id || nextToolCallId(),
      toolName: tc.name,
      args: tc.args,
      argsText: JSON.stringify(tc.args),
    });
  }
  return {
    role: "assistant",
    content: parts,
    metadata: { custom: {} },
  };
}

/** Fetch thread history from the backend. */
async function fetchThreadHistory(
  apiUrl: string,
  apiKey: string,
  threadId: string,
): Promise<ThreadMessage[]> {
  try {
    const resp = await fetch(`${apiUrl}/v1/threads/${encodeURIComponent(threadId)}/history`, {
      headers: { "X-API-Key": apiKey },
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) return [];
    const data = (await resp.json()) as ThreadHistoryResponse;
    return data.messages
      .map((m) => chatMessageToMessageLike(m))
      .filter((like): like is ThreadMessageLike => like !== null)
      .map((like) => fromThreadMessageLike(like, generateId()));
  } catch {
    return [];
  }
}

// ── Hook ────────────────────────────────────────────────────────────────────

interface OssiaExternalStoreState {
  messages: ThreadMessage[];
  isRunning: boolean;
}

/**
 * Creates an ``ExternalStoreAdapter`` backed by the Ossia SSE backend.
 *
 * The adapter is reactive: every call to ``setMessages`` or dispatching
 * ``set-running`` / ``update-assistant`` triggers a React re-render,
 * which causes ``useExternalStoreRuntime`` to ``setAdapter`` with the
 * latest state.
 */
export function useOssiaExternalStore(config: Config, initialThreadId: string = "") {
  const configRef = useRef(config);
  configRef.current = config;

  // ── Reactive state ──────────────────────────────────────────────────
  const [state, dispatch] = useReducer(
    (prev: OssiaExternalStoreState, action: OssiaAction): OssiaExternalStoreState => {
      switch (action.type) {
        case "set-messages":
          return { ...prev, messages: action.messages };
        case "set-running":
          return { ...prev, isRunning: action.isRunning };
        case "update-assistant":
          return { ...prev, messages: action.updater(prev.messages) };
        case "update-message":
          return {
            ...prev,
            messages: prev.messages.map((m) =>
              m.id === action.id
                ? fromThreadMessageLike(
                    { role: m.role, content: action.content, metadata: { custom: {} } },
                    m.id,
                    { type: action.status ?? "complete" },
                  )
                : m,
            ),
          };
      }
    },
    { messages: [], isRunning: false },
  );

  // Keep a ref to the latest messages so handleReload never has a stale
  // closure (the ref is updated on every render via the expression below).
  const messagesRef = useRef(state.messages);
  messagesRef.current = state.messages;

  // Abort controller for the current run
  const abortRef = useRef<AbortController | null>(null);

  // Track whether history has been loaded for the current thread
  // and the last-seen thread_id so we can react to switches.
  const historyLoadedRef = useRef<string | null>(null);

  // ── Thread ID tracking ──────────────────────────────────────────────
  const threadIdRef = useRef<string>(initialThreadId);
  threadIdRef.current = initialThreadId;

  // ── Thread switching (called by App / sidebar) ──────────────────────
  // Aborts any in-flight request, clears messages, updates ``thread_id``,
  // and loads the target thread's history.
  const switchThread = useCallback(async (threadId: string) => {
    abortRef.current?.abort();
    abortRef.current = null;
    threadIdRef.current = threadId;
    historyLoadedRef.current = null;
    dispatch({ type: "set-messages", messages: [] });
    sideChannelStore.setState((prev) => ({
      ...prev,
      thread_id: threadId,
      run_state: "idle" as const,
      interrupts: [],
      error: null,
    }));
    if (threadId) {
      const { apiUrl, apiKey } = configRef.current;
      const history = await fetchThreadHistory(apiUrl, apiKey, threadId);
      if (history.length > 0) {
        dispatch({ type: "set-messages", messages: history });
      }
    }
  }, []);

  // ── Resume a paused interrupt (HITL) ────────────────────────────────
  // POSTs the user's decisions to ``/v1/threads/{id}/resume`` and ingests
  // the resulting message list. ``decisions`` is a list of
  // ``{type, message?, edited_action?}`` matching :class:`ResumeDecision`
  // in the backend schema. An empty list with type="approve" is a no-op
  // for tools that should run as-is.
  const resume = useCallback(
    async (decisions: Array<Record<string, unknown>>) => {
      const { apiUrl, apiKey } = configRef.current;
      const threadId = threadIdRef.current;
      if (!threadId) {
        reportError("Cannot resume: no active thread.");
        return;
      }

      const abortController = new AbortController();
      abortRef.current = abortController;
      dispatch({ type: "set-running", isRunning: true });
      clearInterrupts();

      try {
        const resp = await fetch(
          `${apiUrl}/v1/threads/${encodeURIComponent(threadId)}/resume`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-API-Key": apiKey,
            },
            body: JSON.stringify({ decisions }),
            signal: abortController.signal,
          },
        );
        if (!resp.ok) {
          const text = await resp.text();
          reportError(`Resume failed (${resp.status}): ${text}`);
          return;
        }
        const data = (await resp.json()) as {
          thread_id: string;
          messages: Array<{
            role: string;
            content: string;
            tool_calls?: Array<{ id: string; name: string; args: Record<string, unknown> }>;
          }>;
        };
        // Re-stream the resumed messages by appending them to the chat.
        // The resume response is a flat list (not a stream), so we apply
        // the same history→messageLike mapping used by fetchThreadHistory.
        // Skip user messages (already in the thread) and tool messages
        // (the backend serializes them as raw Python repr which would
        // render as garbage text).
        //
        // Also drop the trailing empty assistant placeholder that
        // ``handleNew`` added when the original run started — its job
        // was to short-circuit the runtime's optimistic-assistant
        // creation during the first stream; the resumed content
        // replaces it.
        clearError();
        const baseMessages = (() => {
          const msgs = messagesRef.current;
          if (msgs.at(-1)?.role === "assistant" && msgs.at(-1)?.content.length === 0) {
            return msgs.slice(0, -1);
          }
          return msgs;
        })();
        const resumed: ThreadMessage[] = [];
        for (const m of data.messages) {
          if (m.role === "user" || m.role === "tool") continue;
          const parts: ThreadMessageLike["content"] = [];
          if (m.content) parts.push({ type: "text" as const, text: m.content });
          for (const tc of m.tool_calls ?? []) {
            parts.push({
              type: "tool-call" as const,
              toolCallId: tc.id || nextToolCallId(),
              toolName: tc.name,
              args: tc.args,
              argsText: JSON.stringify(tc.args),
            });
          }
          const msgId = generateId();
          resumed.push(
            fromThreadMessageLike(
              { role: "assistant", content: parts, metadata: { custom: {} } },
              msgId,
              { type: "complete" as const },
            ),
          );
        }
        if (resumed.length > 0) {
          dispatch({ type: "set-messages", messages: [...baseMessages, ...resumed] });
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          reportError(`Resume error: ${(err as Error).message}`);
        }
      } finally {
        dispatch({ type: "set-running", isRunning: false });
        abortRef.current = null;
      }
    },
    [],
  );
  // Register the per-thread resume callback so the provider-level
  // ``useOssiaControls().resume()`` can find us by remoteId. (Kept
  // for the future thread-list migration; currently a no-op since
  // ``MyRuntimeProvider`` calls ``resume`` directly.)
  useEffect(() => {
    if (!initialThreadId) return;
    return registerResume(initialThreadId, resume);
    // resume is stable across renders (useCallback with [] deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialThreadId]);

  // ── onNew callback ──────────────────────────────────────────────────
  const handleNew = useCallback(async (appendMessage: AppendMessage) => {
    const { apiUrl, apiKey } = configRef.current;
    const userText = extractTextFromAppendMessage(appendMessage);
    if (!userText) return;

    const abortController = new AbortController();
    abortRef.current = abortController;

    clearError();
    dispatch({ type: "set-running", isRunning: true });

    // 1. Add the user message
    const userId = generateId();
    const userMsg = fromThreadMessageLike(
      { role: "user", content: [{ type: "text" as const, text: userText }], metadata: { custom: {} } },
      userId,
    );

    // 2. Prepend an empty assistant placeholder with status "running".
    //    The runtime skips its own optimistic-assistant creation when
    //    the last message in our adapter is already an assistant, so
    //    this placeholder becomes the head and is what the UI renders
    //    while the streamResponse loop mutates it in place. See
    //    streamResponse docstring for the full reasoning.
    const placeholderId = generateId();
    const placeholderMsg = fromThreadMessageLike(
      { role: "assistant", content: [], metadata: { custom: {} } },
      placeholderId,
      { type: "running" as const },
    );
    dispatch({
      type: "update-assistant",
      updater: (prev) => [...prev, userMsg, placeholderMsg],
    });

    try {
      await streamResponse(
        apiUrl,
        apiKey,
        abortController,
        dispatch,
        userText,
        placeholderId,
        threadIdRef.current,
      );
    } finally {
      dispatch({ type: "set-running", isRunning: false });
      abortRef.current = null;
    }
  }, []);

  // ── onReload callback ───────────────────────────────────────────────
  const handleReload = useCallback(async (parentId: string | null) => {
    const { apiUrl, apiKey } = configRef.current;

    const abortController = new AbortController();
    abortRef.current = abortController;
    dispatch({ type: "set-running", isRunning: true });

    // Read latest messages from ref (no stale closure)
    const msgs = messagesRef.current;
    const parentIdx = parentId ? msgs.findIndex((m) => m.id === parentId) : -1;
    const userMsg = msgs.slice(parentIdx + 1).find((m) => m.role === "user");
    const userText =
      userMsg?.content
        .filter((p): p is { type: "text"; text: string } => p.type === "text")
        .map((p) => p.text)
        .join("\n") ?? "";

    if (!userText) {
      dispatch({ type: "set-running", isRunning: false });
      return;
    }

    // Remove messages after the parent and re-append a fresh assistant
    // placeholder (status: running). Same reasoning as handleNew: the
    // placeholder short-circuits the runtime's own optimistic message.
    const trimmed = parentId ? msgs.slice(0, parentIdx + 1) : [];
    const placeholderId = generateId();
    const placeholderMsg = fromThreadMessageLike(
      { role: "assistant", content: [], metadata: { custom: {} } },
      placeholderId,
      { type: "running" as const },
    );
    dispatch({ type: "set-messages", messages: [...trimmed, placeholderMsg] });

    await streamResponse(
      apiUrl,
      apiKey,
      abortController,
      dispatch,
      userText,
      placeholderId,
      threadIdRef.current,
    );

    dispatch({ type: "set-running", isRunning: false });
    abortRef.current = null;
  }, []); // messagesRef replaces the stale closure dependency

  // ── onCancel callback ───────────────────────────────────────────────
  const handleCancel = useCallback(async () => {
    abortRef.current?.abort();
    // Mark any in-flight assistant placeholder as incomplete so the UI
    // stops showing the running indicator and renders whatever was
    // streamed so far as a final-but-cancelled message.
    const last = messagesRef.current.at(-1);
    if (last?.role === "assistant") {
      dispatch({
        type: "update-message",
        id: last.id,
        content: last.content,
        status: { type: "incomplete", reason: "cancelled" },
      });
    }
    dispatch({ type: "set-running", isRunning: false });
    abortRef.current = null;
  }, []);

  // ── Build the adapter ───────────────────────────────────────────────
  // No useMemo: a new adapter object is created each render so the
  // runtime's `setAdapter` effect always sees a new reference. This
  // matches the external-store contract: the runtime compares by ref
  // and short-circuits when the adapter is the same, so we must
  // always pass a fresh object when our state changes.
  const adapter: ExternalStoreAdapter = {
    messages: state.messages,
    isRunning: state.isRunning,
    // Merge incoming messages with our state, preferring the message
    // already in our state when both share an id. The runtime's
    // ``cancelRun`` schedules an unconditional ``updateMessages`` resync
    // via setTimeout(0) that captures a stale snapshot of the
    // repository before our in-flight dispatches propagate; without
    // this merge, that resync would clobber a freshly-applied
    // ``incomplete`` status on a cancelled assistant placeholder.
    setMessages: (msgs: readonly ThreadMessage[]) => {
      const current = messagesRef.current;
      const byId = new Map(current.map((m) => [m.id, m]));
      const merged: ThreadMessage[] = msgs.map((m) => byId.get(m.id) ?? m);
      // Any new ids (e.g. an optimistic head inserted by the runtime
      // for a non-ExternalStore branch) get appended at the end so we
      // don't silently drop them.
      for (const m of current) {
        if (!msgs.some((incoming) => incoming.id === m.id)) {
          merged.push(m);
        }
      }
      dispatch({ type: "set-messages", messages: merged });
    },
    onNew: handleNew,
    onReload: handleReload,
    onCancel: handleCancel,
  };

  return {
    adapter,
    switchThread,
    resume,
  };
}

// ── Module-level resume dispatcher ──────────────────────────────────────────
// The ``resume`` function lives on the per-thread inner runtime created by
// the thread-list binder, but ``useOssiaControls().resume()`` is called
// from ``InterruptPrompt`` at the provider level. The active thread's
// inner runtime registers its ``resume`` here; the provider's wrapper
// looks it up by current remoteId so only the active thread resumes.

type ResumeFn = (decisions: Array<Record<string, unknown>>) => Promise<void>;
let _activeResume: { threadId: string; fn: ResumeFn } | null = null;

export function registerResume(threadId: string, fn: ResumeFn): () => void {
  _activeResume = { threadId, fn };
  return () => {
    if (_activeResume?.threadId === threadId) _activeResume = null;
  };
}

export function dispatchResume(
  threadId: string,
  decisions: Array<Record<string, unknown>>,
): Promise<void> {
  if (_activeResume && _activeResume.threadId === threadId) {
    return _activeResume.fn(decisions);
  }
  return Promise.resolve();
}

// ── Shared streaming logic ────────────────────────────────────────────────
// The single function that owns the fetch, SSE parsing, tool tracking,
// and message updates. Both ``handleNew`` and ``handleReload`` call this.

/**
 * Stream a response from the Ossia backend, updating ``placeholderId`` in
 * place as text and tool events arrive.
 *
 * Why the placeholder pattern: v0.14's ExternalStoreRuntime creates an
 * extra optimistic empty assistant message whenever ``isRunning=true`` and
 * the last message is not an assistant. That optimistic becomes the
 * thread head and is what the UI renders, so any in-place update on a
 * different message in the same array is invisible. Prepending a
 * dedicated placeholder (the last message, role=assistant) before the
 * stream starts short-circuits the optimistic logic — the runtime sees
 * "last message is assistant" and skips the optimistic, leaving our
 * placeholder as the head. ``addOrUpdateMessage`` then propagates
 * content changes through the repository's ``_messages.dirty()`` path,
 * so the live text streams into the UI.
 */
async function streamResponse(
  apiUrl: string,
  apiKey: string,
  abortController: AbortController,
  dispatch: React.Dispatch<OssiaAction>,
  userText: string,
  placeholderId: string,
  threadId: string,
): Promise<void> {
  // Fetch the SSE stream
  let response: Response;
  try {
    response = await fetch(`${apiUrl}/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
      body: JSON.stringify({
        message: userText,
        thread_id: threadId || undefined,
      }),
      signal: abortController.signal,
    });
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      reportError(`Connection error: ${(err as Error).message}`);
    }
    return;
  }
  if (!response.ok) {
    let detail = "";
    try {
      detail = await response.text();
    } catch {
      // ignore body read errors
    }
    reportError(
      `Chat failed (${response.status}${response.statusText ? ` ${response.statusText}` : ""})${detail ? `: ${detail.slice(0, 200)}` : ""}`,
    );
    return;
  }

  const activeTools = new Map<string, ActiveToolCall>();
  const lastToolStarted = new Map<string, string>();
  let fullText = "";
  // Track whether the last seen event type was a final/complete signal;
  // used to decide when to flip the placeholder to ``status: complete``.
  let streamDone = false;

  /**
   * Rebuild the placeholder's content array from the current text and
   * tracked tool calls, then dispatch an in-place update. The text
   * content (if any) is followed by one tool-call part per active
   * tool call in the order they were started.
   */
  const updatePlaceholder = (
    status:
      | { type: "running" }
      | { type: "complete" }
      | { type: "incomplete"; reason: "cancelled" | "length" | "content-filter" | "other" | "error" },
  ) => {
    const parts: ThreadMessageLike["content"] = [];
    if (fullText) parts.push({ type: "text" as const, text: fullText });
    for (const tool of activeTools.values()) {
      parts.push({
        type: "tool-call" as const,
        toolCallId: tool.toolCallId,
        toolName: tool.toolName,
        args: tool.args,
        argsText: JSON.stringify(tool.args),
        ...(tool.result !== undefined && { result: tool.result }),
        ...(tool.isError && { isError: true }),
      });
    }
    dispatch({
      type: "update-message",
      id: placeholderId,
      content: parts,
      status,
    });
  };

  try {
    for await (const event of parseSSEStream(response)) {
      dispatchSideEvent(event);

      // Tool lifecycle (side-channel tracking only — tool cards are
      // rendered from the side channel, not from this message).
      if (event.type === "tool_started") {
        const toolName = String(event.data.name ?? "unknown");
        const toolInput = (event.data.input as Record<string, unknown>) ?? {};
        const toolCallId = nextToolCallId();
        activeTools.set(toolCallId, { toolName, toolCallId, args: toolInput });
        lastToolStarted.set(toolName, toolCallId);
        updatePlaceholder({ type: "running" });
      } else if (event.type === "tool_completed") {
        const toolName = String(event.data.name ?? "");
        const output = event.data.output;
        const id = lastToolStarted.get(toolName);
        if (id && activeTools.has(id)) {
          activeTools.set(id, { ...activeTools.get(id)!, result: output });
        }
        updatePlaceholder({ type: "running" });
      } else if (event.type === "tool_failed") {
        const toolName = String(event.data.name ?? "");
        const error = String(event.data.error ?? "unknown error");
        const id = lastToolStarted.get(toolName);
        if (id && activeTools.has(id)) {
          activeTools.set(id, { ...activeTools.get(id)!, isError: true, result: error });
        }
        updatePlaceholder({ type: "running" });
      }

      // Capture the running text (latest is authoritative).
      if (
        event.type === "message_started" ||
        event.type === "message_delta" ||
        event.type === "message_completed"
      ) {
        const raw = String(event.data.text ?? "");
        const cleaned = cleanText(raw);
        if (cleaned) fullText = cleaned;
        updatePlaceholder({ type: "running" });
      }

      // ``complete`` is the terminal event: the graph finished, no more
      // events will arrive. Flip the placeholder to complete so the UI
      // shows the final state.
      if (event.type === "complete") {
        streamDone = true;
      }
    }
  } catch {
    // Stream errors are non-fatal — we still emit whatever we have.
  }

  // Final state: complete if the graph finished cleanly, incomplete
  // otherwise (cancelled, errored, or connection dropped).
  updatePlaceholder(
    streamDone ? { type: "complete" } : { type: "incomplete", reason: "cancelled" },
  );
}

/** Extract text from an AppendMessage. */
function extractTextFromAppendMessage(msg: AppendMessage): string {
  const parts = msg.content;
  if (typeof parts === "string") return parts;
  for (const p of parts) {
    if (p.type === "text") return p.text;
  }
  return "";
}

// ── Provider component helpers ──────────────────────────────────────────────

/**
 * Per-thread inner runtime factory for the RemoteThreadListRuntime.
 *
 * The thread list runtime renders a fresh ``_RuntimeBinder`` for each
 * alive thread; this hook is the ``runtimeHook`` it calls. The binder
 * renders inside ``ThreadListItemRuntimeProvider`` so ``useAui()`` here
 * returns the current thread-list-item state. We pass its ``remoteId``
 * (or ``externalId`` for not-yet-initialized threads) to the inner
 * ExternalStore adapter so history loading and SSE streaming target the
 * correct thread.
 */
/**
 * Per-thread inner runtime factory (kept for future use with
 * `useRemoteThreadListRuntime`; not wired in `MyRuntimeProvider` yet
 * because the thread-list runtime needs a `history` adapter that our
 * `ExternalStore` doesn't expose — see TODO in MyRuntimeProvider).
 */
export function useOssiaInnerRuntime(config: Config): AssistantRuntime {
  const { adapter } = useOssiaExternalStore(config);
  return useExternalStoreRuntime(adapter);
}

/**
 * One-shot runtime creation used by the legacy single-thread path. Kept
 * for compatibility with callers that still need a bare ExternalStore
 * runtime; ``MyRuntimeProvider`` now prefers ``useRemoteThreadListRuntime``
 * + ``useOssiaInnerRuntime``.
 */
export function useOssiaRuntime(config: Config) {
  const { adapter, switchThread, resume } = useOssiaExternalStore(config);
  const runtime = useExternalStoreRuntime(adapter);

  useEffect(() => {
    resetSideChannel();
  }, [config.apiUrl, config.apiKey]);

  return { runtime, switchThread, resume };
}
