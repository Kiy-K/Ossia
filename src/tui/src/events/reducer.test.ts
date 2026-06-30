/**
 * Tests for ReAct step accumulation in the event reducer.
 *
 * Verifies that the reducer correctly maps SSE events into the
 * react_steps array on AppState.
 *
 * Run with: bun test
 */
import { expect, test, describe } from "bun:test";
import { initialAppState, reduceEvent } from "./reducer";
import type { OssiaEvent } from "./types";

/** Build a minimal valid OssiaEvent for testing. */
function makeEvent(type: string, data: Record<string, unknown>): OssiaEvent {
  return {
    id: "test-id",
    seq: 0,
    timestamp: new Date().toISOString(),
    type,
    source: "test",
    thread_id: "thread-1",
    data,
  };
}

describe("initialAppState", () => {
  test("includes an empty react_steps array", () => {
    const state = initialAppState();
    expect(state.react_steps).toEqual([]);
  });
});

describe("ReAct step accumulation", () => {
  test("message_completed (assistant) appends a thought step", () => {
    const state = initialAppState();
    const next = reduceEvent(
      state,
      makeEvent("message_completed", { role: "assistant", text: "I should search for tests." }),
    );
    expect(next.react_steps).toHaveLength(1);
    const thoughtStep = next.react_steps?.[0];
    if (!thoughtStep) throw new Error("Expected a thought step");
    expect(thoughtStep.kind).toBe("thought");
    expect((thoughtStep as { kind: "thought"; content: string }).content).toBe(
      "I should search for tests.",
    );
  });

  test("message_completed (user role) does NOT append a thought step", () => {
    const state = initialAppState();
    const next = reduceEvent(
      state,
      makeEvent("message_completed", { role: "user", text: "Hello" }),
    );
    expect(next.react_steps).toHaveLength(0);
  });

  test("tool_started appends an action step with correct tool name and input", () => {
    const state = initialAppState();
    const next = reduceEvent(
      state,
      makeEvent("tool_started", { name: "search_codebase", input: { query: "auth" } }),
    );
    expect(next.react_steps).toHaveLength(1);
    const step = next.react_steps?.[0];
    if (!step) throw new Error("Expected an action step");
    expect(step.kind).toBe("action");
    expect((step as { kind: "action"; tool: string }).tool).toBe("search_codebase");
    expect((step as { kind: "action"; input: Record<string, unknown> }).input).toEqual({ query: "auth" });
  });

  test("tool_completed appends a successful observation step", () => {
    const state = initialAppState();
    const next = reduceEvent(
      state,
      makeEvent("tool_completed", { name: "search_codebase", output: "3 files found" }),
    );
    expect(next.react_steps).toHaveLength(1);
    const step = next.react_steps?.[0];
    if (!step) throw new Error("Expected an observation step");
    expect(step.kind).toBe("observation");
    expect((step as { success: boolean }).success).toBe(true);
    expect((step as { output: unknown }).output).toBe("3 files found");
  });

  test("tool_failed appends a failed observation step with error", () => {
    const state = initialAppState();
    const next = reduceEvent(
      state,
      makeEvent("tool_failed", { name: "run_tests", error: "timeout after 30s" }),
    );
    expect(next.react_steps).toHaveLength(1);
    const step = next.react_steps?.[0];
    if (!step) throw new Error("Expected a failed observation step");
    expect(step.kind).toBe("observation");
    expect((step as { success: boolean }).success).toBe(false);
    expect((step as { error?: string }).error).toBe("timeout after 30s");
  });

  test("steps accumulate in temporal order across multiple events", () => {
    let state = initialAppState();
    state = reduceEvent(state, makeEvent("message_completed", { role: "assistant", text: "Thinking..." }));
    state = reduceEvent(state, makeEvent("tool_started", { name: "run_tests", input: {} }));
    state = reduceEvent(state, makeEvent("tool_completed", { name: "run_tests", output: "passed" }));

    const steps = state.react_steps ?? [];
    expect(steps).toHaveLength(3);
    const s0 = steps[0];
    const s1 = steps[1];
    const s2 = steps[2];
    if (!s0 || !s1 || !s2) throw new Error("Expected 3 steps");
    expect(s0.kind).toBe("thought");
    expect(s1.kind).toBe("action");
    expect(s2.kind).toBe("observation");
  });

  test("unrelated events do not affect react_steps", () => {
    let state = initialAppState();
    state = reduceEvent(state, makeEvent("subagent_spawned", { name: "researcher" }));
    state = reduceEvent(state, makeEvent("pipeline_started", { pipeline_type: "bugfix", total_steps: 3 }));
    state = reduceEvent(state, makeEvent("async_task_started", { task_id: "t1", agent_name: "auditor", status: "running" }));

    expect(state.react_steps).toHaveLength(0);
  });
});
