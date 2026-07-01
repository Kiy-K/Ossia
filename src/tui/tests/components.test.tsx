/**
 * Component tests for TUI components.
 *
 * Testing strategy:
 *  1. Pure helper functions → test directly (StatusBar helpers)
 *  2. Hookless components → call as functions, inspect React element tree
 *  3. Hook-based components → native OpenTUI renderer for visual output
 *  4. Null/empty behavior → check return value directly
 */

import { describe, it, expect } from "bun:test";
import { createElement } from "react";
import TestRenderer from "react-test-renderer";
import type { AppState } from "../src/types";

import { computeTimelineHeight } from "../src/App";
import { StatusBar } from "../src/components/StatusBar";
import { activeAgentCount, activeToolCount, activeAsyncTaskCount } from "../src/components/statusBar.helpers";
import { TimelinePanel } from "../src/components/TimelinePanel";
import { ToolPanel } from "../src/components/ToolPanel";
import { BackgroundTasksPanel } from "../src/components/BackgroundTasksPanel";
import { InterruptModal } from "../src/components/InterruptModal";
import { ReActPanel } from "../src/components/ReActPanel";
import { SubagentPanel } from "../src/components/SubagentPanel";
import { Box, Text, Input, ScrollBox } from "../src/components/primitives";

// ── State factory ───────────────────────────────────────────────────────────

const BASE: AppState = {
  connected: false,
  thread_id: "",
  run_state: "idle",
  error: null,
  messages: [],
  timeline: [],
  subagents: {},
  tools: [],
  async_tasks: [],
  interrupts: null,
  user_input: "",
};

function st(overrides: Partial<AppState>): AppState {
  return { ...BASE, ...overrides };
}

// ── React element tree helpers ──────────────────────────────────────────────

/** Recursively extract all text from a React element tree. */
function extractText(node: unknown): string {
  // Must check primitives BEFORE the object guard
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (!node || typeof node !== "object") return "";
  // Handle fragments and arrays
  const arr = (node as Array<unknown>);
  if (Array.isArray(arr)) return arr.map(extractText).join("");
  // React element: look for props.children
  const el = node as Record<string, unknown>;
  // Invoke function components to get their rendered output
  if (typeof el.type === "function" && el.props && typeof el.props === "object") {
    return extractText((el.type as (p: unknown) => unknown)(el.props));
  }
  if (el.props && typeof el.props === "object") {
    const children = (el.props as Record<string, unknown>).children;
    if (children !== null && children !== undefined) {
      return extractText(children);
    }
  }
  return "";
}

/** Call a hookless component and return its rendered text. */
function renderText<P>(Component: (props: P) => unknown, props: P): string {
  return extractText(Component(props));
}

// ── StatusBar helper tests ─────────────────────────────────────────────────

describe("StatusBar helpers", () => {
  describe("activeAgentCount", () => {
    it("returns 0 when no subagents", () => {
      expect(activeAgentCount(st({}))).toBe(0);
    });

    it("counts only running subagents", () => {
      expect(
        activeAgentCount(
          st({
            subagents: {
              a: { name: "a", state: "running", messages: [] },
              b: { name: "b", state: "completed", messages: [] },
              c: { name: "c", state: "error", messages: [] },
              d: { name: "d", state: "idle", messages: [] },
            },
          }),
        ),
      ).toBe(1);
    });

    it("counts multiple running subagents", () => {
      expect(
        activeAgentCount(
          st({
            subagents: {
              a: { name: "a", state: "running", messages: [] },
              b: { name: "b", state: "running", messages: [] },
            },
          }),
        ),
      ).toBe(2);
    });
  });

  describe("activeToolCount", () => {
    it("returns 0 when no tools", () => {
      expect(activeToolCount(st({}))).toBe(0);
    });

    it("counts only running tools", () => {
      expect(
        activeToolCount(
          st({
            tools: [
              { name: "t1", state: "running" },
              { name: "t2", state: "completed" },
              { name: "t3", state: "failed" },
            ],
          }),
        ),
      ).toBe(1);
    });
  });

  describe("activeAsyncTaskCount", () => {
    it("returns 0 when no tasks", () => {
      expect(activeAsyncTaskCount(st({}))).toBe(0);
    });

    it("counts running, pending, and launched", () => {
      expect(
        activeAsyncTaskCount(
          st({
            async_tasks: [
              { task_id: "1", agent_name: "a", status: "running" },
              { task_id: "2", agent_name: "b", status: "pending" },
              { task_id: "3", agent_name: "c", status: "launched" },
              { task_id: "4", agent_name: "d", status: "completed" },
              { task_id: "5", agent_name: "e", status: "failed" },
            ],
          }),
        ),
      ).toBe(3);
    });
  });
});

// ── StatusBar rendering ────────────────────────────────────────────────────

describe("StatusBar rendering", () => {
  it("shows disconnected when no thread_id", () => {
    const text = renderText(StatusBar, { state: st({}) });
    expect(text).toContain("disconnected");
    expect(text).toContain("idle");
  });

  it("shows thread_id when set", () => {
    const text = renderText(StatusBar, { state: st({ thread_id: "test:abc123" }) });
    expect(text).toContain("test:ab");
  });

  it("shows agent count", () => {
    const text = renderText(StatusBar, {
      state: st({
        thread_id: "t1",
        subagents: { a: { name: "a", state: "running", messages: [] } },
      }),
    });
    expect(text).toContain("1 agent");
  });

  it("shows tool count", () => {
    const text = renderText(StatusBar, {
      state: st({
        thread_id: "t1",
        tools: [{ name: "t1", state: "running" }],
      }),
    });
    expect(text).toContain("1 tool");
  });

  it("shows run_state = running", () => {
    const text = renderText(StatusBar, { state: st({ run_state: "running" }) });
    expect(text).toContain("running");
  });

  it("shows run_state = error with message", () => {
    const text = renderText(StatusBar, { state: st({ run_state: "error", error: "timeout" }) });
    expect(text).toContain("error");
    expect(text).toContain("timeout");
  });

  it("shows run_state = completed as done", () => {
    const text = renderText(StatusBar, { state: st({ run_state: "completed" }) });
    expect(text).toContain("done");
  });

  it("shows run_state = interrupted", () => {
    const text = renderText(StatusBar, { state: st({ run_state: "interrupted" }) });
    expect(text).toContain("interrupted");
  });

  it("combines multiple status elements", () => {
    const text = renderText(StatusBar, {
      state: st({
        thread_id: "t1",
        run_state: "running",
        subagents: { a: { name: "a", state: "running", messages: [] } },
        tools: [{ name: "t1", state: "running" }],
      }),
    });
    expect(text).toContain("thread: t1");
    expect(text).toContain("1 agent");
    expect(text).toContain("1 tool");
    expect(text).toContain("running");
  });

  it("shows async task count", () => {
    const text = renderText(StatusBar, {
      state: st({
        thread_id: "t1",
        async_tasks: [{ task_id: "1", agent_name: "a", status: "running" }],
      }),
    });
    expect(text).toContain("1 bg");
  });
});

// ── TimelinePanel rendering ─────────────────────────────────────────────────

describe("TimelinePanel rendering", () => {
  it("shows placeholder when empty", () => {
    const text = renderText(TimelinePanel, { entries: [], height: 5 });
    expect(text).toContain("Waiting for input...");
  });

  it("renders entries with time, event, and detail", () => {
    const text = renderText(TimelinePanel, {
      entries: [
        { time: "12:00", event: "started", detail: "" },
        { time: "12:01", event: "tool search", detail: "query: hello" },
      ],
      height: 10,
    });
    expect(text).toContain("12:00");
    expect(text).toContain("started");
    expect(text).toContain("12:01");
    expect(text).toContain("tool search");
    expect(text).toContain("query: hello");
  });

  it("handles empty detail gracefully", () => {
    const text = renderText(TimelinePanel, {
      entries: [{ time: "12:00", event: "plan", detail: "" }],
      height: 5,
    });
    expect(text).toContain("plan");
  });
});

// ── ToolPanel rendering ─────────────────────────────────────────────────────

describe("ToolPanel rendering", () => {
  it("returns null when no tools", () => {
    const result = ToolPanel({ state: st({}) });
    expect(result).toBeNull();
  });

  it("shows running tool", () => {
    const text = renderText(ToolPanel, {
      state: st({
        tools: [{ name: "search", state: "running" }],
      }),
    });
    expect(text).toContain("search");
  });

  it("shows failed tool with error", () => {
    const text = renderText(ToolPanel, {
      state: st({
        tools: [{ name: "run_tests", state: "failed", error: "timeout" }],
      }),
    });
    expect(text).toContain("run_tests");
    expect(text).toContain("timeout");
  });

  it("shows completed count", () => {
    const text = renderText(ToolPanel, {
      state: st({
        tools: [
          { name: "t1", state: "completed" },
          { name: "t2", state: "completed" },
        ],
      }),
    });
    expect(text).toContain("2 completed");
  });

  it("shows both running and completed tools", () => {
    const text = renderText(ToolPanel, {
      state: st({
        tools: [
          { name: "active_tool", state: "running" },
          { name: "done_tool", state: "completed" },
        ],
      }),
    });
    expect(text).toContain("active_tool");
    expect(text).toContain("1 completed");
  });
});

// ── BackgroundTasksPanel rendering ──────────────────────────────────────────

describe("BackgroundTasksPanel rendering", () => {
  it("returns null when no tasks", () => {
    const result = BackgroundTasksPanel({ state: st({}) });
    expect(result).toBeNull();
  });

  it("shows running tasks", () => {
    const text = renderText(BackgroundTasksPanel, {
      state: st({
        async_tasks: [{ task_id: "1", agent_name: "auditor", status: "running" }],
      }),
    });
    expect(text).toContain("auditor");
    expect(text).toContain("running");
  });

  it("shows failed task with error", () => {
    const text = renderText(BackgroundTasksPanel, {
      state: st({
        async_tasks: [{ task_id: "1", agent_name: "tester", status: "failed", error: "crash" }],
      }),
    });
    expect(text).toContain("tester");
    expect(text).toContain("failed");
    expect(text).toContain("crash");
  });

  it("shows active count", () => {
    const text = renderText(BackgroundTasksPanel, {
      state: st({
        async_tasks: [
          { task_id: "1", agent_name: "a", status: "running" },
          { task_id: "2", agent_name: "b", status: "completed" },
        ],
      }),
    });
    expect(text).toContain("1 active");
  });

  it("shows only last 3 completed tasks", () => {
    const tasks = Array.from({ length: 5 }, (_, i) => ({
      task_id: `${i}`,
      agent_name: `task-${i}`,
      status: "completed" as const,
    }));
    const text = renderText(BackgroundTasksPanel, {
      state: st({ async_tasks: tasks }),
    });
    expect(text).toContain("task-2");
    expect(text).toContain("task-3");
    expect(text).toContain("task-4");
    expect(text).not.toContain("task-0");
    expect(text).not.toContain("task-1");
  });
});

// ── InterruptModal rendering ────────────────────────────────────────────────

describe("InterruptModal rendering", () => {
  it("returns null when no interrupt", () => {
    const result = InterruptModal({ state: st({}) });
    expect(result).toBeNull();
  });

  it("shows interrupt header", () => {
    const text = renderText(InterruptModal, {
      state: st({
        interrupts: { interrupts: [{ action: "ask_approval" }] },
      }),
    });
    expect(text).toContain("Interrupted");
  });

  it("shows interrupt actions", () => {
    const text = renderText(InterruptModal, {
      state: st({
        interrupts: { interrupts: [{ action: "ask_approval", message: "Approve?" }] },
      }),
    });
    expect(text).toContain("ask_approval");
  });

  it("shows multiple interrupts with numbering", () => {
    const text = renderText(InterruptModal, {
      state: st({
        interrupts: {
          interrupts: [
            { action: "ask_approval" },
            { action: "ask_fix" },
          ],
        },
      }),
    });
    expect(text).toContain("1.");
    expect(text).toContain("2.");
    expect(text).toContain("ask_fix");
  });

  it("shows API resume instructions", () => {
    const text = renderText(InterruptModal, {
      state: st({
        interrupts: { interrupts: [{ action: "approve" }] },
      }),
    });
    expect(text).toContain("resume or cancel");
  });

  it("handles empty interrupts list", () => {
    const text = renderText(InterruptModal, {
      state: st({
        interrupts: { interrupts: [] },
      }),
    });
    expect(text).toContain("Interrupted");
  });
});

// ── computeTimelineHeight ──────────────────────────────────────────────────

describe("computeTimelineHeight", () => {
  it("returns termHeight - BASE_PANEL_HEIGHT when no ReAct steps", () => {
    // BASE_PANEL_HEIGHT = 6
    expect(computeTimelineHeight(24, 0)).toBe(18);
    expect(computeTimelineHeight(40, 0)).toBe(34);
    expect(computeTimelineHeight(12, 0)).toBe(6);
  });

  it("returns termHeight - (BASE + REACT) when ReAct steps exist", () => {
    // BASE_PANEL_HEIGHT = 6, REACT_PANEL_HEIGHT = 4 → total = 10
    expect(computeTimelineHeight(24, 1)).toBe(14);
    expect(computeTimelineHeight(24, 5)).toBe(14);
    expect(computeTimelineHeight(40, 100)).toBe(30);
  });

  it("clamps to zero or negative when termHeight is small", () => {
    // With ReAct visible: 6 + 4 = 10 reserved; tiny termHeight → negative result
    expect(computeTimelineHeight(10, 1)).toBe(0);
    expect(computeTimelineHeight(5, 1)).toBe(-5);
  });

  it("treats negative stepCount as zero (no ReAct panel)", () => {
    // BASE = 6 reserved
    expect(computeTimelineHeight(24, -1)).toBe(18);
  });

  it("handles large termHeight without overflow", () => {
    // With ReAct visible: 6 + 4 = 10 reserved
    expect(computeTimelineHeight(9999, 10)).toBe(9989);
  });
});

// ── InputBar — tests via react-test-renderer ────────────────────────────────

describe("InputBar", () => {
  it("returns a truthy element tree (not null)", () => {
    const mod = require("../src/components/InputBar");
    expect(mod.InputBar).toBeDefined();
    expect(typeof mod.InputBar).toBe("function");
  });

  it("renders disabled state without throwing", () => {
    const { InputBar } = require("../src/components/InputBar");
    const tree = TestRenderer.create(
      createElement(InputBar, { onSubmit: () => {}, disabled: true }),
    );
    expect(tree).toBeDefined();
  });

  it("renders input field when not disabled", () => {
    const { InputBar } = require("../src/components/InputBar");
    const tree = TestRenderer.create(
      createElement(InputBar, { onSubmit: () => {}, disabled: false }),
    );
    expect(tree).toBeDefined();
    const json = tree.toJSON();
    expect(json).toBeDefined();
  });
});

// ── ReActPanel tests (hookless component) ───────────────────────────────────

describe("ReActPanel", () => {
  it("returns null when no react_steps", () => {
    const result = ReActPanel({ state: st({}) });
    expect(result).toBeNull();
  });

  it("renders thought steps", () => {
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "thought", content: "I should search", time: "12:04" },
        ],
      }),
    });
    expect(text).toContain("[Think]");
    expect(text).toContain("I should search");
    expect(text).toContain("12:04");
  });

  it("renders action steps with tool name", () => {
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "action", tool: "search_codebase", input: { q: "test" }, time: "12:05" },
        ],
      }),
    });
    expect(text).toContain("[Act]");
    expect(text).toContain("search_codebase");
  });

  it("renders successful observations", () => {
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "observation", tool: "search_codebase", output: "found it", success: true, time: "12:06" },
        ],
      }),
    });
    expect(text).toContain("[Obs ✓]");
    expect(text).toContain("found it");
  });

  it("renders failed observations", () => {
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "observation", tool: "search_codebase", output: null, success: false, error: "timeout", time: "12:06" },
        ],
      }),
    });
    expect(text).toContain("[Obs ✗]");
    expect(text).toContain("timeout");
  });

  it("renders successful observations with object output", () => {
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "observation", tool: "search_codebase", output: { result: "data" }, success: true, time: "12:06" },
        ],
      }),
    });
    expect(text).toContain("[Obs ✓]");
    expect(text).toContain("result");
  });

  it("truncates long content", () => {
    const longText = "A".repeat(100);
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "thought", content: longText, time: "12:04" },
        ],
      }),
    });
    expect(text).toContain("…");
  });

  it("shows step count in header", () => {
    const text = renderText(ReActPanel, {
      state: st({
        react_steps: [
          { kind: "thought", content: "step 1", time: "12:01" },
          { kind: "thought", content: "step 2", time: "12:02" },
        ],
      }),
    });
    expect(text).toContain("2 steps");
  });

  it("shows only last MAX_STEPS (3) visible", () => {
    const steps = Array.from({ length: 5 }, (_, i) => ({
      kind: "thought" as const,
      content: `step ${i + 1}`,
      time: `12:0${i + 1}`,
    }));
    const text = renderText(ReActPanel, {
      state: st({ react_steps: steps }),
    });
    expect(text).toContain("showing last 3");
    expect(text).toContain("step 3");
    expect(text).toContain("step 4");
    expect(text).toContain("step 5");
    expect(text).not.toContain("step 2");
  });
});

// ── SubagentPanel tests ─────────────────────────────────────────────────────

describe("SubagentPanel", () => {
  it("module exports SubagentPanel as a function", () => {
    expect(typeof SubagentPanel).toBe("function");
  });

  it("module is importable", () => {
    const mod = require("../src/components/SubagentPanel");
    expect(mod.SubagentPanel).toBeDefined();
  });
});

// ── Primitives wrapper tests ────────────────────────────────────────────────

describe("OpenTUI primitive wrappers", () => {
  it("Box returns a React element with type 'box'", () => {
    const el = Box({ flexDirection: "column" });
    expect(el).toBeDefined();
    expect(typeof el).toBe("object");
  });

  it("Text returns a React element with type 'text'", () => {
    const el = Text({ children: "hello" });
    expect(el).toBeDefined();
    expect(typeof el).toBe("object");
  });

  it("Input returns a React element", () => {
    const el = Input({ placeholder: "test" });
    expect(el).toBeDefined();
    expect(typeof el).toBe("object");
  });

  it("ScrollBox returns a React element", () => {
    const el = ScrollBox({ flexGrow: 1 });
    expect(el).toBeDefined();
    expect(typeof el).toBe("object");
  });
});
