/**
 * Tests for the SSE stream parser (events/stream.ts).
 *
 * Tests parseSSEStream with mock Response objects and sendMessage with a
 * mocked global fetch. No real backend needed — all data is synthetic.
 */

import { describe, it, expect, mock, afterAll, beforeAll } from "bun:test";
import { parseSSEStream, sendMessage } from "../src/events/stream";
import type { OssiaEvent } from "../src/events/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Create a mock Response with a ReadableStream from text chunks.
 * Each chunk is a string of raw SSE text (one or more \n\n-terminated blocks).
 */
function mockResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** Collect all events from an async generator. */
async function collect(g: AsyncGenerator<OssiaEvent>): Promise<OssiaEvent[]> {
  const events: OssiaEvent[] = [];
  for await (const e of g) {
    events.push(e);
  }
  return events;
}

/** Build a complete SSE block (event + id + data + \n\n). */
function sseBlock(
  eventType: string,
  data: Record<string, unknown>,
  id = "evt-1",
): string {
  return [
    `event: ${eventType}`,
    `id: ${id}`,
    `data: ${JSON.stringify(data)}`,
    "",
    "",
  ].join("\n");
}

/** Minimal valid event data payload. */
function eventPayload(
  overrides: Partial<Record<string, unknown>> = {},
): Record<string, unknown> {
  return {
    id: "e1",
    seq: 1,
    timestamp: "2026-06-27T00:00:00Z",
    type: "message_delta",
    source: "coordinator",
    thread_id: "test:default",
    data: { text: "hello" },
    ...overrides,
  };
}

// ── parseSSEStream ──────────────────────────────────────────────────────────

describe("parseSSEStream", () => {
  it("parses a single SSE event", async () => {
    const response = mockResponse([
      sseBlock("message_delta", eventPayload({ seq: 1 })),
    ]);
    const events = await collect(parseSSEStream(response));

    expect(events).toHaveLength(1);
    expect(events[0]!.type).toBe("message_delta");
    expect(events[0]!.seq).toBe(1);
    expect(events[0]!.data.text).toBe("hello");
  });

  it("parses multiple events in a single chunk", async () => {
    const response = mockResponse([
      sseBlock("message_delta", eventPayload({ seq: 1, type: "message_delta" })) +
      sseBlock("message_completed", eventPayload({ seq: 2, type: "message_completed" })),
    ]);
    const events = await collect(parseSSEStream(response));

    expect(events).toHaveLength(2);
    expect(events[0]!.type).toBe("message_delta");
    expect(events[1]!.type).toBe("message_completed");
    expect(events[1]!.seq).toBe(2);
  });

  it("handles a single SSE block split across two chunks", async () => {
    const block = sseBlock("message_delta", eventPayload({ seq: 1 }));
    const mid = Math.floor(block.length / 2);
    const response = mockResponse([block.slice(0, mid), block.slice(mid)]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(1);
    expect(events[0]!.type).toBe("message_delta");
  });

  it("handles multiple events split across chunks", async () => {
    const e1 = sseBlock("message_delta", eventPayload({ seq: 1, type: "message_delta" }));
    const e2 = sseBlock("message_completed", eventPayload({ seq: 2, type: "message_completed" }));

    // Split first event across two chunks, second entirely in the second chunk
    const mid = Math.floor(e1.length / 2);
    const response = mockResponse([e1.slice(0, mid), e1.slice(mid) + e2]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(2);
    expect(events[0]!.type).toBe("message_delta");
    expect(events[1]!.type).toBe("message_completed");
  });

  it("only parses lines starting with 'data:'", async () => {
    // `event:` and `id:` lines should be skipped; only `data:` is consumed
    const block = [
      "event: foo",
      "id: 1",
      `data: ${JSON.stringify(eventPayload({ seq: 1 }))}`,
      "",
      "",
    ].join("\n");
    const response = mockResponse([block]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(1);
    expect(events[0]!.seq).toBe(1);
  });

  it("returns nothing when the data line is missing", async () => {
    const block = "event: complete\nid: 1\n\n";
    const response = mockResponse([block]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(0);
  });

  it("returns nothing for invalid JSON in data", async () => {
    const block = "data: {not valid json}\n\n";
    const response = mockResponse([block]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(0);
  });

  it("returns nothing for an empty body", async () => {
    const response = mockResponse([""]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(0);
  });

  it("flushes the buffer when the stream ends without trailing \\n\\n", async () => {
    // No trailing \n\n — the parser should flush any buffered data on 'done'
    const incomplete =
      "event: message_delta\nid: 1\ndata: " +
      JSON.stringify(eventPayload({ seq: 1 })) +
      "\n"; // missing second \n\n
    const response = mockResponse([incomplete]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(1);
    expect(events[0]!.seq).toBe(1);
  });

  it("uses the last data: line when multiple exist in one block", async () => {
    const block =
      "data: {\"seq\":1,\"type\":\"old\"}\n" +
      `data: ${JSON.stringify(eventPayload({ seq: 2 }))}\n\n`;
    const response = mockResponse([block]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(1);
    expect(events[0]!.seq).toBe(2); // last wins
  });

  it("preserves full OssiaEvent envelope through parse round-trip", async () => {
    const payload = eventPayload({
      id: "uuid-abc",
      seq: 42,
      timestamp: "2026-06-27T12:34:56Z",
      type: "message_completed",
      source: "coordinator.deep_agent",
      thread_id: "test:thread-42",
      data: { role: "assistant", text: "Four score and seven years ago", id: null },
    });
    const response = mockResponse([sseBlock("message_completed", payload)]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(1);

    const e = events[0]!;
    expect(e.id).toBe("uuid-abc");
    expect(e.seq).toBe(42);
    expect(e.timestamp).toBe("2026-06-27T12:34:56Z");
    expect(e.type).toBe("message_completed");
    expect(e.source).toBe("coordinator.deep_agent");
    expect(e.thread_id).toBe("test:thread-42");
    expect(e.data.text).toBe("Four score and seven years ago");
  });

  it("preserves nested objects in data field via JSON round-trip", async () => {
    const nested = {
      interrupts: [
        { action: "ask_approval", message: "Approve?" },
        { action: "ask_fix", message: "Fix?" },
      ],
    };
    const payload = eventPayload({ data: nested });
    const response = mockResponse([sseBlock("interrupt", payload)]);

    const events = await collect(parseSSEStream(response));
    expect(events).toHaveLength(1);

    const d = events[0]!.data as { interrupts: Array<Record<string, unknown>> };
    expect(d.interrupts).toHaveLength(2);
    expect(d.interrupts[0]!.action).toBe("ask_approval");
  });
});

// ── sendMessage ─────────────────────────────────────────────────────────────

describe("sendMessage", () => {
  const API_URL = "http://localhost:8000";
  const API_KEY = "test-key-123";
  let originalFetch: typeof globalThis.fetch;

  beforeAll(() => {
    originalFetch = globalThis.fetch;
  });

  afterAll(() => {
    globalThis.fetch = originalFetch;
  });

  it("yields events from a successful response", async () => {
    const payload = eventPayload({ seq: 1, type: "message_delta" });
    const body = sseBlock("message_delta", payload);

    globalThis.fetch = mock(async () =>
      new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    ) as unknown as typeof globalThis.fetch;

    const events = await collect(sendMessage("hello", { apiUrl: API_URL, apiKey: API_KEY }));
    expect(events).toHaveLength(1);
    expect(events[0]!.type).toBe("message_delta");
  });

  it("throws on HTTP 401 (unauthorized)", async () => {
    globalThis.fetch = mock(async () =>
      new Response(JSON.stringify({ error: { message: "Invalid API key" } }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    ) as unknown as typeof globalThis.fetch;

    let err: Error | undefined;
    try {
      await collect(sendMessage("hello", { apiUrl: API_URL, apiKey: "bad-key" }));
    } catch (e) {
      err = e as Error;
    }

    expect(err).toBeDefined();
    expect(err!.message).toContain("API error");
    expect(err!.message).toContain("Invalid API key");
  });

  it("throws with HTTP status when response body is not JSON", async () => {
    globalThis.fetch = mock(async () =>
      new Response("Gateway Timeout", {
        status: 504,
        headers: { "Content-Type": "text/plain" },
      }),
    ) as unknown as typeof globalThis.fetch;

    let err: Error | undefined;
    try {
      await collect(sendMessage("hello", { apiUrl: API_URL, apiKey: API_KEY }));
    } catch (e) {
      err = e as Error;
    }

    expect(err).toBeDefined();
    expect(err!.message).toContain("API error");
    expect(err!.message).toContain("HTTP 504");
  });

  it("throws on HTTP 500", async () => {
    globalThis.fetch = mock(async () =>
      new Response("Internal Server Error", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      }),
    ) as unknown as typeof globalThis.fetch;

    let err: Error | undefined;
    try {
      await collect(sendMessage("hello", { apiUrl: API_URL, apiKey: API_KEY }));
    } catch (e) {
      err = e as Error;
    }

    expect(err).toBeDefined();
    expect(err!.message).toContain("API error");
  });

  it("forwards thread_id in the request body", async () => {
    let capturedBody = "";

    globalThis.fetch = mock(async (_url, opts) => {
      capturedBody = (opts as RequestInit).body as string;
      return new Response("", { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    await collect(
      sendMessage("hi", { apiUrl: API_URL, apiKey: API_KEY, threadId: "custom-thread" }),
    );

    const body = JSON.parse(capturedBody);
    expect(body.message).toBe("hi");
    expect(body.thread_id).toBe("custom-thread");
  });

  it("includes correct headers in the request", async () => {
    let capturedHeaders: Record<string, string> = {};

    globalThis.fetch = mock(async (_url, opts) => {
      capturedHeaders = (opts as RequestInit).headers as Record<string, string>;
      return new Response("", { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    await collect(sendMessage("test", { apiUrl: API_URL, apiKey: "secret-42" }));

    expect(capturedHeaders["Content-Type"]).toBe("application/json");
    expect(capturedHeaders["X-API-Key"]).toBe("secret-42");
  });

  it("uses the provided AbortSignal", async () => {
    const ac = new AbortController();

    globalThis.fetch = mock(async (_url, opts) => {
      expect((opts as RequestInit).signal).toBe(ac.signal);
      return new Response("", { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    await collect(sendMessage("test", { apiUrl: API_URL, apiKey: API_KEY }, ac.signal));
  });
});
