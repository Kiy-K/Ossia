/**
 * Integration test for the full TUI data pipeline.
 *
 * Connects to a running Ossia backend server, sends a chat message via
 * the SSE endpoint, collects all ``OssiaEvent`` objects, feeds them
 * through the reducer, and asserts the final state is correct.
 *
 * This tests the full pipeline end-to-end:
 *   POST /v1/chat/stream → parseSSEStream → reduceEvent → AppState
 *
 * Prerequisites:
 *   - Backend server running on OSSIA_API_URL (default: http://localhost:8000)
 *   - ENABLE_HUMAN_REVIEW=false (to avoid interrupts on simple queries)
 *   - OSSIA_API_KEY must be set (default: 12345678)
 *
 * Run with:
 *   bun test src/tui/tests/integration.test.ts
 */

import { describe, it, expect } from "bun:test";
import { sendMessage } from "../src/events/stream";
import { reduceEvent, initialAppState } from "../src/events/reducer";
import type { OssiaEvent } from "../src/events/types";
import type { AppState } from "../src/types";

const API_URL = process.env.OSSIA_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.OSSIA_API_KEY ?? "12345678";
const HEALTH_URL = `${API_URL}/health`;

async function collectEvents(stream: AsyncGenerator<OssiaEvent>): Promise<OssiaEvent[]> {
  const events: OssiaEvent[] = [];
  for await (const event of stream) {
    events.push(event);
  }
  return events;
}

function reduceAll(events: OssiaEvent[]): AppState {
  let state = initialAppState();
  for (const e of events) {
    state = reduceEvent(state, e);
  }
  return state;
}

async function isServerReachable(): Promise<boolean> {
  try {
    const res = await fetch(HEALTH_URL, {
      headers: { "X-API-Key": API_KEY },
      signal: AbortSignal.timeout(3000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function runChat(message: string, threadId?: string, timeoutMs = 20_000): Promise<{ events: OssiaEvent[]; state: AppState }> {
  const stream = sendMessage(
    message,
    { apiUrl: API_URL, apiKey: API_KEY, threadId },
    AbortSignal.timeout(timeoutMs),
  );
  const events = await collectEvents(stream);
  const state = reduceAll(events);
  return { events, state };
}

/** Returns true if run_state is one of the valid terminal states. */
function isTerminal(state: AppState): boolean {
  return state.run_state === "completed" || state.run_state === "interrupted" || state.run_state === "error";
}

const SERVER_REACHABLE = await isServerReachable();
if (!SERVER_REACHABLE) {
  console.warn(
    `\n  ⚠ Backend not reachable at ${API_URL}. Integration tests skipped.\n` +
    `  Start the server with:\n` +
    `    ENABLE_HUMAN_REVIEW=false cd /home/khoi/ossia && make dev\n\n`,
  );
}

// ── Tests ───────────────────────────────────────────────────────────────────

if (SERVER_REACHABLE) {
  describe("live backend integration", () => {
    it("health endpoint responds", async () => {
      const res = await fetch(HEALTH_URL, { headers: { "X-API-Key": API_KEY } });
      expect(res.ok).toBe(true);
      const body = await res.json() as Record<string, unknown>;
      expect(body.status).toBe("ok");
    }, 5000);

    it("events have valid OssiaEvent structure", async () => {
      const { events, state } = await runChat("Say hello in one word");

      // Every event has the required envelope fields
      for (const event of events) {
        expect(event).toHaveProperty("id");
        expect(event).toHaveProperty("seq");
        expect(event).toHaveProperty("timestamp");
        expect(event).toHaveProperty("type");
        expect(event).toHaveProperty("source");
        expect(event).toHaveProperty("thread_id");
        expect(event).toHaveProperty("data");
        // seq must be a positive integer from the backend
        expect(typeof event.seq).toBe("number");
        expect(event.seq).toBeGreaterThan(0);
      }

      // At least one event + a complete event
      expect(events.length).toBeGreaterThanOrEqual(2);
      expect(events[events.length - 1]!.type).toBe("complete");

      // Thread id is propagated into state
      expect(state.thread_id).toBeTruthy();
    }, 30_000);

    it("final state is terminal after chat", async () => {
      const { state } = await runChat("Return the number 42");

      expect(state.connected).toBe(true);
      expect(isTerminal(state)).toBe(true);
      expect(state.thread_id).toBeTruthy();

      // Timeline ends with a terminal event
      expect(state.timeline.length).toBeGreaterThanOrEqual(2);
      const lastTimelineEvent = state.timeline[state.timeline.length - 1]!.event;
      expect(["done", "paused"]).toContain(lastTimelineEvent);

      // When the run completed successfully, check for assistant content.
      // When interrupted (HITL), there may be no content — skip content assertions.
      if (state.run_state === "completed") {
        const assistantMessages = state.messages.filter((m) => m.role === "assistant");
        expect(assistantMessages.length).toBeGreaterThanOrEqual(1);
        expect(assistantMessages[0]!.content.length).toBeGreaterThan(0);
      }
    }, 30_000);

    it("contains message events in the event stream", async () => {
      const { events, state } = await runChat("Repeat hello world");

      // The stream must contain message events
      const messageTypes = events.map((e) => e.type).filter((t) => t.startsWith("message_"));
      expect(messageTypes.length).toBeGreaterThanOrEqual(1);

      // Messages must include a completed message
      expect(messageTypes).toContain("message_completed");

      // Final event is complete
      expect(events[events.length - 1]!.type).toBe("complete");

      // When the run completed, check for assistant message content.
      // The filter should replace Python repr strings with "[content available]".
      if (state.run_state === "completed") {
        expect(state.messages.length).toBeGreaterThanOrEqual(1);
        const lastMsg = state.messages[state.messages.length - 1]!;
        expect(lastMsg!.role).toBe("assistant");
        expect(lastMsg!.content.length).toBeGreaterThan(0);
      }
    }, 30_000);

    it("multiple calls produce distinct thread_ids per call", async () => {
      const ids = new Set<string>();
      for (let i = 0; i < 3; i++) {
        const { state } = await runChat(`Return the number ${i}`, `test-seq-${i}`);
        expect(state.thread_id).toBeTruthy();
        ids.add(state.thread_id);
      }
      expect(ids.size).toBe(3);
    }, 60_000);

    it("handles a knowledge question correctly", async () => {
      const { state } = await runChat("What is the capital of France? Answer in one word");

      expect(isTerminal(state)).toBe(true);

      if (state.run_state === "completed") {
        const assistantMsgs = state.messages.filter((m) => m.role === "assistant");
        expect(assistantMsgs.length).toBeGreaterThanOrEqual(1);
        const final = assistantMsgs[assistantMsgs.length - 1]!.content.toLowerCase();
        expect(final).toContain("paris");
      }
      // If interrupted, skip the content check — the agent didn't respond
    }, 30_000);

    it("thread_id is scoped to provided value", async () => {
      const { events, state } = await runChat("Say hello", "integration-test-scope");

      // All events from one call share the same thread_id
      const ids = new Set(events.map((e) => e.thread_id));
      expect(ids.size).toBe(1);
      const tid = ids.values().next().value;
      expect(tid).toBeTruthy();
      expect(tid).toContain("integration-test-scope");

      expect(state.thread_id).toBeTruthy();
      expect(isTerminal(state)).toBe(true);

      if (state.run_state === "completed") {
        expect(state.messages.length).toBeGreaterThanOrEqual(1);
      }
    }, 30_000);

    it("returns error on invalid API key", async () => {
      try {
        const stream = sendMessage("hello", { apiUrl: API_URL, apiKey: "wrong-key" });
        await collectEvents(stream);
        expect(true).toBe(false); // Should not reach
      } catch (err) {
        expect(err).toBeDefined();
        const msg = err instanceof Error ? err.message : String(err);
        expect(msg).toContain("API error");
      }
    }, 10_000);

    it("state includes assistant reply on successful run", async () => {
      const { state } = await runChat("Say yes");

      expect(isTerminal(state)).toBe(true);

      if (state.run_state === "completed") {
        // SSE stream only emits AI/tool events, not the user message
        expect(state.messages.length).toBeGreaterThanOrEqual(1);
        const lastMsg = state.messages[state.messages.length - 1]!;
        expect(lastMsg!.role).toBe("assistant");
        expect(lastMsg!.content.length).toBeGreaterThan(0);
      }
    }, 30_000);

    it("can include tool events (agent may search or run tools)", async () => {
      const { events } = await runChat("What is 2+2? Answer in one number");

      // The agent may or may not use tools; either is valid
      const toolEvents = events.filter((e) => e.type.startsWith("tool_"));
      // If there are tool events, they should have valid structure
      for (const te of toolEvents) {
        expect(te.data.name || te.data).toBeTruthy();
      }
    }, 30_000);
  });
} else {
  describe("live backend integration", () => {
    it("all tests skipped — server not reachable", () => {});
  });
}
