/**
 * Tests for the Ossia TUI event reducer.
 *
 * Feeds real-shaped ``OssiaEvent`` objects through ``reduceEvent`` and
 * asserts correct state transitions. Uses Bun's built-in test runner
 * (``bun:test``) — no additional test framework required.
 *
 * Run with: ``bun test src/tui/tests/reducer.test.ts``
 */

import { describe, it, expect } from "bun:test";
import type { OssiaEvent } from "../src/events/types";
import type { AppState } from "../src/types";
import { reduceEvent, initialAppState } from "../src/events/reducer";

// ── Test helpers ────────────────────────────────────────────────────────────

/** Create a fake ``OssiaEvent`` with minimal fields. */
function event(
  type: string,
  data: Record<string, unknown> = {},
  overrides: Partial<OssiaEvent> = {},
): OssiaEvent {
  return {
    id: overrides.id ?? "test-id",
    seq: overrides.seq ?? 1,
    timestamp: overrides.timestamp ?? "2026-06-27T12:00:00Z",
    type,
    source: overrides.source ?? "coordinator",
    thread_id: overrides.thread_id ?? "test:default",
    data,
  };
}

/** Reduce N events in sequence from ``initialState()``. */
function reduceAll(...events: OssiaEvent[]): AppState {
  let state = initialAppState();
  for (const e of events) {
    state = reduceEvent(state, e);
  }
  return state;
}

/** Shortcut: create a message_completed event. */
function msgComplete(role: string, text: string): OssiaEvent {
  return event("message_completed", { role, text });
}

// ── Initial state ───────────────────────────────────────────────────────────

describe("initialAppState", () => {
  it("returns a blank state", () => {
    const s = initialAppState();
    expect(s.connected).toBe(false);
    expect(s.thread_id).toBe("");
    expect(s.run_state).toBe("idle");
    expect(s.error).toBeNull();
    expect(s.messages).toEqual([]);
    expect(s.timeline).toEqual([]);
    expect(s.subagents).toEqual({});
    expect(s.tools).toEqual([]);
    expect(s.async_tasks).toEqual([]);
    expect(s.interrupts).toBeNull();
    expect(s.user_input).toBe("");
  });
});

// ── Message events ──────────────────────────────────────────────────────────

describe("message_started", () => {
  it("marks connected=true, sets run_state=running, adds timeline entry", () => {
    const s = reduceAll(event("message_started", { role: "ai", text: "Hello" }));
    expect(s.connected).toBe(true);
    expect(s.run_state).toBe("running");
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("Assistant");
  });
});

describe("message_delta", () => {
  it("updates the last timeline entry's detail when preceded by message_started", () => {
    const s = reduceAll(
      event("message_started", { role: "ai", text: "Hel" }),
      event("message_delta", { role: "ai", text: "lo" }),
    );
    expect(s.timeline.length).toBe(1); // delta updates in-place, does not append
    expect(s.timeline[0]!.event).toBe("Assistant");
  });

  it("does not append a timeline entry when there is no previous entry", () => {
    // standalone message_delta with no prior events returns timeline unchanged
    const s = reduceAll(event("message_delta", { role: "ai", text: "Hello" }));
    expect(s.timeline.length).toBe(0); // nothing to update
    expect(s.connected).toBe(true); // but connected flag is still set
  });
});

describe("message_completed", () => {
  it("appends a chat message and a timeline entry", () => {
    const s = reduceAll(msgComplete("assistant", "Hello world"));
    expect(s.messages.length).toBe(1);
    expect(s.messages[0]!.role).toBe("assistant");
    expect(s.messages[0]!.content).toBe("Hello world");
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("Message");
  });

  it("normalizes 'ai' role to 'assistant'", () => {
    const s = reduceAll(msgComplete("ai", "test"));
    expect(s.messages[0]!.role).toBe("assistant");
  });
});

// ── Subagent lifecycle ──────────────────────────────────────────────────────

describe("subagent_spawned", () => {
  it("adds a running subagent and timeline entry", () => {
    const s = reduceAll(event("subagent_spawned", { name: "researcher", path: ["researcher"] }));
    expect(s.subagents["researcher"]).toBeDefined();
    expect(s.subagents["researcher"]!.state).toBe("running");
    expect(s.subagents["researcher"]!.messages).toEqual([]);
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("spawn researcher");
  });

  it("defaults name to 'unknown' when missing", () => {
    const s = reduceAll(event("subagent_spawned", { path: [] }));
    expect(s.subagents["unknown"]).toBeDefined();
    expect(s.subagents["unknown"]!.state).toBe("running");
  });
});

describe("subagent_completed", () => {
  it("marks an existing subagent as completed", () => {
    const s = reduceAll(
      event("subagent_spawned", { name: "writer", path: ["writer"] }),
      event("subagent_completed", { name: "writer", path: ["writer"] }),
    );
    expect(s.subagents["writer"]!.state).toBe("completed");
    expect(s.timeline.length).toBe(2);
    expect(s.timeline[1]!.event).toBe("done writer");
  });

  it("no-ops when the subagent was never spawned", () => {
    const s = reduceAll(event("subagent_completed", { name: "ghost" }));
    expect(s.subagents).toEqual({});
  });
});

describe("subagent_failed", () => {
  it("marks an existing subagent as error with message", () => {
    const s = reduceAll(
      event("subagent_spawned", { name: "tester", path: ["tester"] }),
      event("subagent_failed", { name: "tester", error: "timeout", path: ["tester"] }),
    );
    expect(s.subagents["tester"]!.state).toBe("error");
    expect(s.subagents["tester"]!.error).toBe("timeout");
    expect(s.timeline.length).toBe(2);
    expect(s.timeline[1]!.event).toBe("failed tester");
    expect(s.timeline[1]!.detail).toBe("timeout");
  });

  it("no-ops when the subagent was never spawned", () => {
    const s = reduceAll(event("subagent_failed", { name: "ghost", error: "err" }));
    expect(s.subagents).toEqual({});
  });
});

describe("subagent_interrupted", () => {
  it("adds a timeline entry", () => {
    const s = reduceAll(event("subagent_interrupted", { name: "reviewer", path: ["reviewer"] }));
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("interrupted");
  });
});

// ── Tool lifecycle ──────────────────────────────────────────────────────────

describe("tool_started", () => {
  it("adds a running tool and timeline entry", () => {
    const s = reduceAll(
      event("tool_started", { name: "search_codebase", input: { query: "foo" }, source: "coordinator" }),
    );
    expect(s.tools.length).toBe(1);
    expect(s.tools[0]!.name).toBe("search_codebase");
    expect(s.tools[0]!.state).toBe("running");
    expect(s.tools[0]!.input).toEqual({ query: "foo" });
    expect(s.connected).toBe(true);
    expect(s.timeline[0]!.event).toBe("tool search_codebase");
  });

  it("defaults tool name to 'unknown' when missing", () => {
    const s = reduceAll(event("tool_started", { source: "coordinator" }));
    expect(s.tools[0]!.name).toBe("unknown");
  });
});

describe("tool_progress", () => {
  it("updates timeline but does not change tool state", () => {
    const s0 = reduceAll(
      event("tool_started", { name: "fetch", input: {}, source: "coordinator" }),
    );
    expect(s0.tools[0]!.state).toBe("running");

    // tool_progress does not modify tools array
    const s1 = reduceEvent(s0, event("tool_progress", { name: "fetch", output_delta: "connecting...", source: "coordinator" }));
    expect(s1.tools).toEqual(s0.tools); // tools unchanged

    // Multiple progress events don't accumulate in tools
    const s2 = reduceEvent(s1, event("tool_progress", { name: "fetch", output_delta: "data received", source: "coordinator" }));
    expect(s2.tools).toEqual(s0.tools);
  });
});

describe("tool_completed", () => {
  it("marks a running tool as completed", () => {
    const s = reduceAll(
      event("tool_started", { name: "ls", input: {}, source: "coordinator" }),
      event("tool_completed", { name: "ls", output: ["file1", "file2"], source: "coordinator" }),
    );
    expect(s.tools.length).toBe(1);
    expect(s.tools[0]!.state).toBe("completed");
    expect(s.tools[0]!.output).toEqual(["file1", "file2"]);
  });

  it("no-ops when there is no matching running tool", () => {
    const s = reduceAll(
      event("tool_started", { name: "tool_a", input: {}, source: "coordinator" }),
      event("tool_completed", { name: "tool_b", source: "coordinator" }),
    );
    expect(s.tools[0]!.state).toBe("running"); // unchanged
  });
});

describe("tool_failed", () => {
  it("marks a running tool as failed with error", () => {
    const s = reduceAll(
      event("tool_started", { name: "fetch_url", input: { url: "bad" }, source: "coordinator" }),
      event("tool_failed", { name: "fetch_url", error: "Network error", source: "coordinator" }),
    );
    expect(s.tools.length).toBe(1);
    expect(s.tools[0]!.state).toBe("failed");
    expect(s.tools[0]!.error).toBe("Network error");
  });
});

// ── Pipeline lifecycle ──────────────────────────────────────────────────────

describe("pipeline_* events", () => {
  it("pipeline_started adds timeline entry", () => {
    const s = reduceAll(
      event("pipeline_started", { pipeline_type: "bugfix", total_steps: 3, pipeline_id: "p-1" }),
    );
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("pipeline bugfix");
    expect(s.timeline[0]!.detail).toBe("3 steps");
  });

  it("pipeline_step_started adds a subagent and timeline entry", () => {
    const s = reduceAll(
      event("pipeline_started", { pipeline_type: "bugfix", total_steps: 2, pipeline_id: "p-1" }),
      event("pipeline_step_started", { pipeline_id: "p-1", step_name: "bug-diagnostician", step_index: 0, total_steps: 2 }),
    );
    expect(s.subagents["bug-diagnostician"]).toBeDefined();
    expect(s.subagents["bug-diagnostician"]!.state).toBe("running");
    expect(s.timeline[1]!.event).toBe("  step bug-diagnostician");
  });

  it("pipeline_step_completed marks the subagent done", () => {
    const s = reduceAll(
      event("pipeline_step_started", { pipeline_id: "p-1", step_name: "diagnoser", step_index: 0, total_steps: 2 }),
      event("pipeline_step_completed", { pipeline_id: "p-1", step_name: "diagnoser", step_index: 0 }),
    );
    expect(s.subagents["diagnoser"]!.state).toBe("completed");
  });

  it("pipeline_step_failed marks the subagent as error", () => {
    const s = reduceAll(
      event("pipeline_step_started", { pipeline_id: "p-1", step_name: "diagnoser", step_index: 0, total_steps: 2 }),
      event("pipeline_step_failed", { pipeline_id: "p-1", step_name: "diagnoser", step_index: 0, error: "analysis failed" }),
    );
    expect(s.subagents["diagnoser"]!.state).toBe("error");
    expect(s.subagents["diagnoser"]!.error).toBe("analysis failed");
    expect(s.timeline.length).toBe(2);
    expect(s.timeline[1]!.event).toBe("  failed diagnoser");
    expect(s.timeline[1]!.detail).toBe("analysis failed");
  });

  it("pipeline_completed adds a timeline entry", () => {
    const s = reduceAll(event("pipeline_completed", { pipeline_id: "p-1", result: "done" }));
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("pipeline done");
  });

  it("pipeline_failed adds a timeline entry", () => {
    const s = reduceAll(event("pipeline_failed", { pipeline_id: "p-1", error: "step failed" }));
    expect(s.timeline.length).toBe(1);
    expect(s.timeline[0]!.event).toBe("pipeline failed");
    expect(s.timeline[0]!.detail).toBe("step failed");
  });
});

// ── Async tasks ─────────────────────────────────────────────────────────────

describe("async_task_* events", () => {
  it("async_task_started adds a task and timeline entry", () => {
    const s = reduceAll(
      event("async_task_started", { task_id: "t1", agent_name: "auditor", status: "running" }),
    );
    expect(s.async_tasks.length).toBe(1);
    expect(s.async_tasks[0]!.task_id).toBe("t1");
    expect(s.async_tasks[0]!.agent_name).toBe("auditor");
    expect(s.async_tasks[0]!.status).toBe("running");
    expect(s.timeline[0]!.event).toBe("async auditor");
  });

  it("async_task_updated changes the task status", () => {
    const s = reduceAll(
      event("async_task_started", { task_id: "t1", agent_name: "indexer", status: "running" }),
      event("async_task_updated", { task_id: "t1", status: "processing" }),
    );
    expect(s.async_tasks[0]!.status).toBe("processing");
  });

  it("async_task_completed marks the task done", () => {
    const s = reduceAll(
      event("async_task_started", { task_id: "t1", agent_name: "indexer", status: "running" }),
      event("async_task_completed", { task_id: "t1" }),
    );
    expect(s.async_tasks[0]!.status).toBe("completed");
  });

  it("async_task_failed marks the task failed with error", () => {
    const s = reduceAll(
      event("async_task_started", { task_id: "t1", agent_name: "indexer", status: "running" }),
      event("async_task_failed", { task_id: "t1", error: "timeout" }),
    );
    expect(s.async_tasks[0]!.status).toBe("failed");
    expect(s.async_tasks[0]!.error).toBe("timeout");
  });

  it("async_task_cancelled marks the task cancelled", () => {
    const s = reduceAll(
      event("async_task_started", { task_id: "t1", agent_name: "indexer", status: "running" }),
      event("async_task_cancelled", { task_id: "t1" }),
    );
    expect(s.async_tasks[0]!.status).toBe("cancelled");
  });
});

// ── Interrupt / Error / Complete ────────────────────────────────────────────

describe("interrupt", () => {
  it("sets run_state, stores interrupt payload, adds timeline entry", () => {
    const payload = { interrupts: [{ action: "approve_tool_call", tool: "send_response" }] };
    const s = reduceAll(event("interrupt", payload));
    expect(s.run_state).toBe("interrupted");
    expect(s.interrupts).toBeDefined();
    expect(s.interrupts!.interrupts[0]!.action).toBe("approve_tool_call");
    expect(s.timeline[0]!.event).toBe("interrupted");
    expect(s.timeline[0]!.detail).toBe("awaiting input");
  });
});

describe("error", () => {
  it("sets run_state=error, stores error message, adds timeline entry", () => {
    const s = reduceAll(event("error", { error: "LLM call failed" }));
    expect(s.run_state).toBe("error");
    expect(s.error).toBe("LLM call failed");
    expect(s.timeline[0]!.event).toBe("Error");
    expect(s.timeline[0]!.detail).toBe("LLM call failed");
  });
});

describe("complete", () => {
  it("sets run_state=completed when not interrupted", () => {
    const s = reduceAll(event("complete", { output: {}, interrupted: false }));
    expect(s.run_state).toBe("completed");
    expect(s.timeline[0]!.event).toBe("done");
  });

  it("sets run_state=interrupted when interrupted flag is true", () => {
    const s = reduceAll(event("complete", { output: {}, interrupted: true }));
    expect(s.run_state).toBe("interrupted");
    expect(s.timeline[0]!.event).toBe("paused");
  });
});

// ── Pass-through events ─────────────────────────────────────────────────────

describe("artifact_* events", () => {
  // Note: withThreadId at top of reduceEvent propagates event.thread_id
  // into state even for pass-through events, so we expect thread_id
  // to match the event fixture.

  it("artifact_received passes through unchanged (except thread_id)", () => {
    const s = initialAppState();
    const next = reduceEvent(s, event("artifact_received", { artifact_id: "a1", type: "image", filename: "img.png" }));
    expect(next.thread_id).toBe("test:default");
    expect(next.messages).toEqual([]);
    expect(next.timeline).toEqual([]);
    expect(next.tools).toEqual([]);
    expect(next.subagents).toEqual({});
  });

  it("artifact_processed passes through unchanged (except thread_id)", () => {
    const s = initialAppState();
    const next = reduceEvent(s, event("artifact_processed", { artifact_id: "a1", analysis_state: "analyzing" }));
    expect(next.thread_id).toBe("test:default");
    expect(next.messages).toEqual([]);
  });

  it("image_analysis_started passes through unchanged (except thread_id)", () => {
    const s = initialAppState();
    const next = reduceEvent(s, event("image_analysis_started", { artifact_id: "a1" }));
    expect(next.thread_id).toBe("test:default");
  });

  it("image_analysis_completed passes through unchanged (except thread_id)", () => {
    const s = initialAppState();
    const next = reduceEvent(s, event("image_analysis_completed", { artifact_id: "a1", summary: "done" }));
    expect(next.thread_id).toBe("test:default");
  });
});

describe("unknown event type", () => {
  it("passes through unchanged (except thread_id)", () => {
    const s = initialAppState();
    const next = reduceEvent(s, event("bogus_type", { foo: "bar" }));
    expect(next.thread_id).toBe("test:default");
    expect(next.messages).toEqual([]);
    expect(next.timeline).toEqual([]);
  });
});

// ── Integration: full conversation lifecycle ────────────────────────────────

describe("full lifecycle (simple chat)", () => {
  it("processes a complete assistant message", () => {
    const s = reduceAll(
      event("message_started", { role: "ai", text: "Hel" }),
      event("message_delta", { role: "ai", text: "lo there" }),
      event("message_completed", { role: "ai", text: "Hello there" }),
      event("complete", { output: {}, interrupted: false }),
    );

    expect(s.connected).toBe(true);
    expect(s.run_state).toBe("completed");
    expect(s.messages.length).toBe(1);
    expect(s.messages[0]!.role).toBe("assistant");
    expect(s.messages[0]!.content).toBe("Hello there");
    // Timeline: started(1) + completed(1) + complete(1) = 3
    // (delta updates in-place, does not append)
    expect(s.timeline.length).toBe(3);
    expect(s.timeline[2]!.event).toBe("done");
  });
});

describe("full lifecycle (with subagent + tools)", () => {
  it("processes subagent spawning, tool calls, and completion", () => {
    const s = reduceAll(
      // 1. User message completed
      event("message_completed", { role: "user", text: "Find the bug" }),

      // 2. Coordinator spawns researcher subagent
      event("subagent_spawned", { name: "researcher", path: ["researcher"] }),

      // 3. Subagent runs a tool
      event("tool_started", { name: "search_codebase", input: { query: "bug" }, source: "coordinator.researcher" }),
      event("tool_completed", { name: "search_codebase", output: ["file1.ts"], source: "coordinator.researcher" }),

      // 4. Subagent completes
      event("subagent_completed", { name: "researcher", path: ["researcher"] }),

      // 5. Coordinator generates final message
      event("message_completed", { role: "ai", text: "I found the bug in file1.ts" }),

      // 6. Done
      event("complete", { output: { messages: [] }, interrupted: false }),
    );

    expect(s.run_state).toBe("completed");
    expect(s.messages.length).toBe(2); // user + assistant
    expect(s.messages[1]!.content).toBe("I found the bug in file1.ts");

    // Subagent: visible but completed
    expect(s.subagents["researcher"]!.state).toBe("completed");

    // Tool: visible but completed
    expect(s.tools.length).toBe(1);
    expect(s.tools[0]!.state).toBe("completed");

    // Timeline has entries for all steps (7 events, 2 don't append: tool_completed)
    expect(s.timeline.length).toBeGreaterThanOrEqual(6);
  });
});

describe("full lifecycle (with pipeline)", () => {
  it("processes a full bugfix pipeline", () => {
    const s = reduceAll(
      event("message_completed", { role: "user", text: "Fix the crash" }),
      event("tool_started", { name: "run_bugfix_pipeline", input: { issue: "crash" }, source: "coordinator" }),
      event("tool_completed", { name: "run_bugfix_pipeline", output: { pipeline: "bugfix" }, source: "coordinator" }),
      event("pipeline_started", { pipeline_type: "bugfix", total_steps: 3, pipeline_id: "p-1" }),

      // Step 1
      event("pipeline_step_started", { pipeline_id: "p-1", step_name: "bug-diagnostician", step_index: 0, total_steps: 3 }),
      event("pipeline_step_completed", { pipeline_id: "p-1", step_name: "bug-diagnostician", step_index: 0 }),

      // Step 2
      event("pipeline_step_started", { pipeline_id: "p-1", step_name: "fix-proposer", step_index: 1, total_steps: 3 }),
      event("pipeline_step_completed", { pipeline_id: "p-1", step_name: "fix-proposer", step_index: 1 }),

      // Step 3
      event("pipeline_step_started", { pipeline_id: "p-1", step_name: "test-runner", step_index: 2, total_steps: 3 }),
      event("pipeline_step_completed", { pipeline_id: "p-1", step_name: "test-runner", step_index: 2 }),

      event("pipeline_completed", { pipeline_id: "p-1", result: "Pipeline bugfix completed" }),
      event("complete", { output: {}, interrupted: false }),
    );

    expect(s.run_state).toBe("completed");

    // Subagents for each pipeline step
    expect(s.subagents["bug-diagnostician"]!.state).toBe("completed");
    expect(s.subagents["fix-proposer"]!.state).toBe("completed");
    expect(s.subagents["test-runner"]!.state).toBe("completed");

    // Verify pipeline-related timeline entries by checking the event text
    const pipelineEvents = s.timeline.filter((e) => e.event.startsWith("pipeline"));
    expect(pipelineEvents.length).toBe(2); // "pipeline bugfix" + "pipeline done"
    const pipelineStepEvents = s.timeline.filter((e) => e.event.startsWith("  "));
    expect(pipelineStepEvents.length).toBe(6); // "  step" * 3 + "  done" * 3
  });
});

describe("full lifecycle (with async tasks)", () => {
  it("processes async task lifecycle", () => {
    const s = reduceAll(
      event("async_task_started", { task_id: "bg-1", agent_name: "indexer", status: "running" }),
      event("async_task_updated", { task_id: "bg-1", status: "indexing files" }),
      event("async_task_completed", { task_id: "bg-1" }),
    );
    expect(s.async_tasks.length).toBe(1);
    expect(s.async_tasks[0]!.status).toBe("completed");
  });
});

// ── Edge cases ──────────────────────────────────────────────────────────────

describe("edge cases", () => {
  it("handles empty/missing data gracefully", () => {
    const s = reduceAll(
      event("message_completed", {}),
      event("tool_started", {}),
      event("subagent_spawned", {}),
      event("complete", {}),
    );
    expect(s.messages.length).toBe(1);
    expect(s.messages[0]!.content).toBe("");
    expect(s.tools.length).toBe(1);
    expect(s.tools[0]!.name).toBe("unknown");
    expect(s.subagents["unknown"]).toBeDefined();
    // complete with no interrupted field defaults to false → "completed"
    expect(s.run_state).toBe("completed");
  });

  it("is immutable — original state not mutated", () => {
    const original = initialAppState();
    const originalJson = JSON.stringify(original);
    reduceEvent(original, event("message_started", { role: "ai", text: "test" }));
    expect(JSON.stringify(original)).toBe(originalJson);
  });

  it("preserves accumulated state across multiple events", () => {
    const s = reduceAll(
      msgComplete("user", "first"),
      msgComplete("assistant", "reply 1"),
      msgComplete("user", "second"),
      msgComplete("assistant", "reply 2"),
    );
    expect(s.messages.length).toBe(4);
    expect(s.messages[0]!.content).toBe("first");
    expect(s.messages[1]!.content).toBe("reply 1");
    expect(s.messages[2]!.content).toBe("second");
    expect(s.messages[3]!.content).toBe("reply 2");
  });

  it("handles multiple subagents independently", () => {
    const s = reduceAll(
      event("subagent_spawned", { name: "researcher", path: ["researcher"] }),
      event("subagent_spawned", { name: "auditor", path: ["auditor"] }),
      event("subagent_completed", { name: "researcher", path: ["researcher"] }),
      event("subagent_completed", { name: "auditor", path: ["auditor"] }),
    );
    expect(s.subagents["researcher"]!.state).toBe("completed");
    expect(s.subagents["auditor"]!.state).toBe("completed");
  });

  it("handles tool failure after partial state", () => {
    const s = reduceAll(
      event("tool_started", { name: "fetch_url", input: { url: "bad" }, source: "coordinator" }),
      event("tool_progress", { name: "fetch_url", output_delta: "connecting...", source: "coordinator" }),
      event("tool_failed", { name: "fetch_url", error: "timeout", source: "coordinator" }),
    );
    expect(s.tools.length).toBe(1);
    expect(s.tools[0]!.state).toBe("failed");
    expect(s.tools[0]!.error).toBe("timeout");
  });

  it("handles zero-step pipeline", () => {
    const s = reduceAll(
      event("pipeline_started", { pipeline_type: "empty", total_steps: 0, pipeline_id: "p-0" }),
      event("pipeline_completed", { pipeline_id: "p-0" }),
    );
    expect(s.timeline.length).toBe(2);
    expect(s.timeline[1]!.event).toBe("pipeline done");
  });

  it("complete event overwrites error state (current behavior)", () => {
    // The reducer does not prevent subsequent events from changing run_state.
    // An error followed by a complete event will overwrite the error state.
    const s = reduceAll(
      event("error", { error: "LLM call failed" }),
      event("complete", { output: {}, interrupted: true }),
    );
    expect(s.run_state).toBe("interrupted"); // overwritten by complete
    expect(s.error).toBe("LLM call failed"); // error detail preserved
  });
});
