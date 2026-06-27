/**
 * Ossia TUI — SSE stream client.
 *
 * Parses the Server-Sent Events stream from ``/v1/chat/stream`` into
 * typed ``OssiaEvent`` objects. This is the only module that touches
 * raw HTTP or SSE parsing — components never see the stream directly.
 */

import type { OssiaEvent } from "./types";

/** API connection options. */
export interface StreamOptions {
  apiUrl: string;
  apiKey: string;
  threadId?: string;
}

/**
 * Parse an SSE response body into an async generator of ``OssiaEvent``.
 *
 * Handles the standard SSE format:
 * ```
 * event: message_delta
 * id: 42
 * data: {"seq":42,"type":"message_delta",...}
 * ```
 */
export async function* parseSSEStream(
  response: Response,
): AsyncGenerator<OssiaEvent> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      // Flush remaining buffer
      if (buffer.trim()) {
        const parsed = parseSSEBuffer(buffer);
        for (const event of parsed) {
          yield event;
        }
      }
      break;
    }

    buffer += decoder.decode(value, { stream: true });

    // SSE events are separated by \n\n
    const parts = buffer.split("\n\n");
    // Keep the last incomplete part in the buffer
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      if (!part.trim()) continue;
      const parsed = parseSSEBuffer(part);
      for (const event of parsed) {
        yield event;
      }
    }
  }
}

/** Parse a single SSE message block into zero or more OssiaEvents. */
function parseSSEBuffer(block: string): OssiaEvent[] {
  const lines = block.split("\n");
  let jsonData = "";

  for (const line of lines) {
    if (line.startsWith("data: ")) {
      jsonData = line.slice(6);
    }
  }

  if (!jsonData) return [];

  try {
    const parsed = JSON.parse(jsonData) as OssiaEvent;
    return [parsed];
  } catch {
    return [];
  }
}

/**
 * Send a chat message and yield the SSE event stream.
 *
 * Connects to ``POST /v1/chat/stream`` and pipes the response through
 * the SSE parser. Throws on non-ok responses.
 *
 * @param signal Optional AbortSignal to cancel the fetch mid-flight.
 */
export async function* sendMessage(
  message: string,
  options: StreamOptions,
  signal?: AbortSignal,
): AsyncGenerator<OssiaEvent> {
  const url = `${options.apiUrl}/v1/chat/stream`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": options.apiKey,
    },
    body: JSON.stringify({
      message,
      thread_id: options.threadId,
    }),
    signal,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body?.error?.message ?? detail;
    } catch {
      // Use default detail
    }
    throw new Error(`API error: ${detail}`);
  }

  yield* parseSSEStream(response);
}
