/**
 * Ossia RemoteThreadListAdapter — bridges assistant-ui's thread list runtime
 * to the Ossia backend's ``/v1/threads*`` endpoints.
 *
 * Implements the {@link RemoteThreadListAdapter} contract:
 *   - ``list``      → ``GET /v1/threads``  (regular + optional archived)
 *   - ``fetch``     → ``GET /v1/threads/{id}``
 *   - ``initialize``→ ``POST /v1/threads`` (returns caller-scoped remoteId)
 *   - ``rename``    → ``PATCH /v1/threads/{id}``  body ``{title}``
 *   - ``archive``   → ``PATCH /v1/threads/{id}``  body ``{status:"archived"}``
 *   - ``unarchive`` → ``POST /v1/threads/{id}/unarchive``
 *   - ``delete``    → ``DELETE /v1/threads/{id}``
 *   - ``generateTitle`` → returns an ``AssistantStream`` of the first user
 *     message (truncated). The backend stores it via the subsequent
 *     ``rename`` call from the thread list runtime.
 */

import type { Config } from "../types";
import type {
  RemoteThreadInitializeResponse,
  RemoteThreadListAdapter,
  RemoteThreadListResponse,
  RemoteThreadMetadata,
  ThreadMessage,
} from "@assistant-ui/react";
import type { AssistantStream } from "assistant-stream";

// ── Wire types (subset of GET /v1/threads response) ──────────────────────────

interface OssiaThreadInfo {
  thread_id: string;
  external_id: string | null;
  status: "regular" | "archived";
  title: string | null;
  updated_at: string;
  last_message_at: string | null;
  message_count: number;
}

interface OssiaThreadListWire {
  threads: OssiaThreadInfo[];
  total: number;
  next_cursor: string | null;
}

interface OssiaThreadInitWire {
  thread_id: string;
  external_id: string | null;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function toRemoteThreadMetadata(t: OssiaThreadInfo): RemoteThreadMetadata {
  return {
    status: t.status,
    remoteId: t.thread_id,
    externalId: t.external_id ?? undefined,
    title: t.title ?? undefined,
    lastMessageAt: t.last_message_at ? new Date(t.last_message_at) : undefined,
  };
}

async function authed(
  config: Config,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${config.apiUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": config.apiKey,
      ...(init.headers ?? {}),
    },
  });
}

/**
 * Build an AssistantStream that emits a single ``text-delta`` for the given
 * title. The chunk envelope is ``part-start`` → ``text-delta`` → ``part-finish``
 * so any consumer (e.g. the thread list title bar) can render it as text.
 */
function titleStream(title: string): AssistantStream {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [
    encoder.encode(JSON.stringify({ type: "step-start", messageId: "title" }) + "\n"),
    encoder.encode(
      JSON.stringify({ type: "part-start", path: [0], part: { type: "text" } }) + "\n",
    ),
    encoder.encode(
      JSON.stringify({ type: "text-delta", path: [0], textDelta: title }) + "\n",
    ),
    encoder.encode(JSON.stringify({ type: "part-finish", path: [0] }) + "\n"),
  ];
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(chunks[i++]!);
      } else {
        controller.close();
      }
    },
  }) as AssistantStream;
}

/** Derive a short title from the first user message in the given messages. */
function deriveTitle(messages: readonly ThreadMessage[]): string {
  for (const m of messages) {
    if (m.role !== "user") continue;
    for (const part of m.content) {
      if (part.type === "text") {
        const text = part.text.trim();
        if (text) return text.length > 52 ? text.slice(0, 49) + "…" : text;
      }
    }
  }
  return "New Chat";
}

// ── Adapter factory ─────────────────────────────────────────────────────────

/**
 * Build a `RemoteThreadListAdapter` bound to the given backend config.
 * Stable across renders: pass the returned value to ``useRemoteThreadListRuntime``.
 */
export function createOssiaThreadListAdapter(config: Config): RemoteThreadListAdapter {
  return {
    async list(params) {
      const qs = new URLSearchParams();
      qs.set("include_archived", "true");
      qs.set("limit", "200");
      if (params?.after) qs.set("cursor", params.after);
      const resp = await authed(config, `/v1/threads?${qs.toString()}`);
      if (!resp.ok) throw new Error(`list threads failed: ${resp.status}`);
      const data = (await resp.json()) as OssiaThreadListWire;
      const result: RemoteThreadListResponse = {
        threads: data.threads.map(toRemoteThreadMetadata),
        nextCursor: data.next_cursor ?? undefined,
      };
      return result;
    },

    async fetch(remoteId) {
      const resp = await authed(config, `/v1/threads/${encodeURIComponent(remoteId)}`);
      if (!resp.ok) throw new Error(`fetch thread failed: ${resp.status}`);
      const t = (await resp.json()) as OssiaThreadInfo;
      return toRemoteThreadMetadata(t);
    },

    async initialize(threadId) {
      const resp = await authed(config, `/v1/threads`, {
        method: "POST",
        body: JSON.stringify({ external_id: threadId }),
      });
      if (!resp.ok) throw new Error(`initialize thread failed: ${resp.status}`);
      const data = (await resp.json()) as OssiaThreadInitWire;
      const result: RemoteThreadInitializeResponse = {
        remoteId: data.thread_id,
        externalId: data.external_id ?? undefined,
      };
      return result;
    },

    async rename(remoteId, newTitle) {
      const resp = await authed(
        config,
        `/v1/threads/${encodeURIComponent(remoteId)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ title: newTitle }),
        },
      );
      if (!resp.ok) throw new Error(`rename thread failed: ${resp.status}`);
    },

    async archive(remoteId) {
      const resp = await authed(
        config,
        `/v1/threads/${encodeURIComponent(remoteId)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status: "archived" }),
        },
      );
      if (!resp.ok) throw new Error(`archive thread failed: ${resp.status}`);
    },

    async unarchive(remoteId) {
      const resp = await authed(
        config,
        `/v1/threads/${encodeURIComponent(remoteId)}/unarchive`,
        { method: "POST" },
      );
      if (!resp.ok) throw new Error(`unarchive thread failed: ${resp.status}`);
    },

    async delete(remoteId) {
      const resp = await authed(
        config,
        `/v1/threads/${encodeURIComponent(remoteId)}`,
        { method: "DELETE" },
      );
      // Backend returns 200 with {deleted:false} when no checkpointer; treat
      // that as success since the thread is effectively gone.
      if (!resp.ok) throw new Error(`delete thread failed: ${resp.status}`);
    },

    async generateTitle(_remoteId, messages) {
      return titleStream(deriveTitle(messages));
    },
  };
}
