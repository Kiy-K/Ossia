/**
 * Per-channel v3 stream accumulator.
 *
 * Each channel subscriber transforms only its own projection's data.
 * No subscriber touches data it doesn't render. Batched via
 * ``queueMicrotask`` so all parts arriving in the same microtask
 * burst produce one dispatch regardless of token rate.
 */

import type { ThreadMessageLike } from "@assistant-ui/react";

export interface StreamChunk {
  channel: string;
  data: Record<string, unknown>;
}

interface ActiveToolCall {
  toolName: string;
  toolCallId: string;
  args: Record<string, unknown>;
  result?: unknown;
  isError?: boolean;
}

/** Per-channel state accumulated across microtask bursts. */
interface ChannelState {
  fullText: string;
  activeTools: Map<string, ActiveToolCall>;
  /** Subagent statuses keyed by name. */
  subagents: Record<string, { name: string; status: string }>;
  streamDone: boolean;
  interrupted: boolean;
  interrupts: Array<Record<string, unknown>>;
}

type FlushCallback = (
  content: ThreadMessageLike["content"],
  status: { type: "running" } | { type: "complete" } | { type: "incomplete"; reason: "cancelled" },
  meta: { subagents: Record<string, { name: string; status: string }>; interrupts: Array<Record<string, unknown>> },
) => void;

type ContentPart = NonNullable<ThreadMessageLike["content"]> extends (infer T)[] ? T : never;

let _toolCounter = 0;
function nextToolId(): string {
  _toolCounter += 1;
  return `ossia-tool-${_toolCounter}`;
}

export class V3Accumulator {
  private pending: StreamChunk[] = [];
  private flushScheduled = false;
  private state: ChannelState;
  private flush: FlushCallback;

  constructor(flush: FlushCallback) {
    this.flush = flush;
    this.state = {
      fullText: "",
      activeTools: new Map(),
      subagents: {},
      streamDone: false,
      interrupted: false,
      interrupts: [],
    };
  }

  ingest(chunk: StreamChunk): void {
    this.pending.push(chunk);
    if (!this.flushScheduled) {
      this.flushScheduled = true;
      queueMicrotask(() => this._flush());
    }
  }

  finalize(): void {
    if (this.flushScheduled) this._flush();
    if (!this.state.streamDone) {
      this.state.streamDone = true;
      this._emit();
    }
  }

  /** Reset for a new stream. Each streamResponse call creates a fresh accumulator. */
  reset(): void {
    this.pending = [];
    this.flushScheduled = false;
    this.state.fullText = "";
    this.state.activeTools.clear();
    this.state.subagents = {};
    this.state.streamDone = false;
    this.state.interrupted = false;
    this.state.interrupts = [];
  }

  // ── Private ─────────────────────────────────────────────────────────

  private _flush(): void {
    const batch = this.pending;
    this.pending = [];
    this.flushScheduled = false;

    for (const chunk of batch) {
      this._process(chunk);
    }

    this._emit();
  }

  private _process(chunk: StreamChunk): void {
    switch (chunk.channel) {
      case "messages":
        this._handleMessage(chunk.data);
        break;
      case "tool_calls":
        this._handleToolCall(chunk.data);
        break;
      case "subagents":
        this._handleSubagent(chunk.data);
        break;
      case "control":
        this._handleControl(chunk.data);
        break;
      // values — ignored by UI
    }
  }

  private _handleMessage(data: Record<string, unknown>): void {
    // Content is the authoritative full text (backed by resolved projection).
    const content = String(data.content ?? "");
    if (content) this.state.fullText = content;

    // Tool call chunks from AIMessage streaming.
    const chunks = data.tool_call_chunks as Array<Record<string, unknown>> | undefined;
    if (chunks) {
      for (const tc of chunks) {
        const name = String(tc.name ?? "");
        const id = String(tc.id ?? nextToolId());
        if (!name) continue;
        // Accumulate args across chunks: merge partial JSON strings.
        const existing = this.state.activeTools.get(id);
        if (!existing) {
          this.state.activeTools.set(id, {
            toolName: name,
            toolCallId: id,
            args: _tryParse(String(tc.args ?? "{}")),
          });
        } else {
          const prevArgs = String(existing.args ? JSON.stringify(existing.args) : "");
          const newArgs = String(tc.args ?? "");
          existing.args = _tryParse(prevArgs + newArgs.slice(Math.max(0, newArgs.length - (prevArgs ? 0 : newArgs.length))));
        }
      }
    }
  }

  private _handleToolCall(data: Record<string, unknown>): void {
    const toolName = String(data.tool_name ?? "");
    const state = String(data.state ?? "running");

    if (state === "completed" || state === "error") {
      // Update any running tool with matching name.
      for (const [, tool] of this.state.activeTools) {
        if (tool.toolName === toolName && tool.result === undefined && !tool.isError) {
          if (state === "error") {
            tool.isError = true;
            tool.result = data.error;
          } else {
            tool.result = data.output;
          }
          break;
        }
      }
    } else {
      // Running — add if not already tracked (belt-and-suspenders with _handleMessage).
      const input = (data.input as Record<string, unknown>) ?? {};
      const existing = [...this.state.activeTools.values()].find(
        (t) => t.toolName === toolName && t.result === undefined,
      );
      if (!existing) {
        const tid = nextToolId();
        this.state.activeTools.set(tid, { toolName, toolCallId: tid, args: input });
      }
    }
  }

  private _handleSubagent(data: Record<string, unknown>): void {
    const name = String(data.name ?? "");
    const status = String(data.status ?? "unknown");
    if (!name) return;
    this.state.subagents[name] = { name, status };
  }

  private _handleControl(data: Record<string, unknown>): void {
    const event = String(data.event ?? "");
    if (event === "interrupt") {
      this.state.interrupted = true;
      this.state.interrupts = (data.interrupts as Array<Record<string, unknown>>) ?? [];
    } else if (event === "complete") {
      this.state.streamDone = true;
      this.state.interrupted = Boolean(data.interrupted);
    }
  }

  private _emit(): void {
    const parts: Array<NonNullable<ThreadMessageLike["content"]>[number]> = [];
    if (this.state.fullText) {
      parts.push({ type: "text", text: this.state.fullText });
    }
    for (const tool of this.state.activeTools.values()) {
      parts.push({
        type: "tool-call",
        toolCallId: tool.toolCallId,
        toolName: tool.toolName,
        args: tool.args,
        argsText: JSON.stringify(tool.args),
        ...(tool.result !== undefined && { result: tool.result }),
        ...(tool.isError && { isError: true }),
      });
    }

    const status = this.state.streamDone
      ? ({ type: "complete" as const })
      : ({ type: "running" as const });

    this.flush(parts as ThreadMessageLike["content"], status, {
      subagents: this.state.subagents,
      interrupts: this.state.interrupts,
    });
  }
}

function _tryParse(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s) as Record<string, unknown>;
  } catch {
    return {};
  }
}
