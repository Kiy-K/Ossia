/**
 * ToolGroup — collapsible card that wraps consecutive tool-call parts.
 *
 * When an assistant message contains multiple tool calls in sequence
 * (e.g. search_codebase then run_tests), they render as a single
 * collapsible group instead of separate floating cards.
 *
 * Usage:
 * ```tsx
 * <MessagePrimitive.GroupedParts
 *   groupBy={groupPartByType({ "tool-call": ["group-tool"] })}
 * >
 *   {({ part, children }) => {
 *     switch (part.type) {
 *       case "group-tool": return <ToolGroup part={part}>{children}</ToolGroup>;
 *       case "tool-call":  return part.toolUI ?? <ToolFallback {...part} />;
 *       case "indicator":  return <ThinkingDots />;
 *       default:           return null;
 *     }
 *   }}
 * </MessagePrimitive.GroupedParts>
 * ```
 */

"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

// ── Props ───────────────────────────────────────────────────────────────────

interface ToolGroupProps {
  part: {
    type: "group-tool";
    status: { type: "running" | "incomplete" | "complete" };
    indices: readonly number[];
  };
  children: React.ReactNode;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function statusLabel(status: MessagePrimitiveGroupedParts.GroupPart["status"]): string {
  switch (status.type) {
    case "running":
      return "Running…";
    case "incomplete":
      return "Interrupted";
    case "complete":
      return "Done";
    default:
      return "";
  }
}

function statusColor(status: MessagePrimitiveGroupedParts.GroupPart["status"]): string {
  switch (status.type) {
    case "running":
      return "text-ossia-blue";
    case "incomplete":
      return "text-ossia-red";
    case "complete":
      return "text-ossia-green";
    default:
      return "text-ossia-muted";
  }
}

function statusDot(status: MessagePrimitiveGroupedParts.GroupPart["status"]): string {
  switch (status.type) {
    case "running":
      return "bg-ossia-blue animate-pulse";
    case "incomplete":
      return "bg-ossia-red";
    case "complete":
      return "bg-ossia-green";
    default:
      return "bg-ossia-muted";
  }
}

// ── ToolGroup component ─────────────────────────────────────────────────────

export function ToolGroup({ part, children }: ToolGroupProps) {
  const [open, setOpen] = useState(part.status.type === "running");
  const count = part.indices.length;

  return (
    <div className="rounded-lg border border-ossia-border-subtle bg-ossia-surface overflow-hidden text-sm">
      {/* ── Collapsible header ────────────────────────────────────── */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 border-b border-ossia-border-subtle bg-ossia-surface-2 hover:bg-ossia-surface-2/80 transition-colors text-left"
      >
        {/* Expand/collapse caret */}
        <span className="text-ossia-muted-more shrink-0">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>

        {/* Status dot */}
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot(part.status)}`} />

        {/* Title */}
        <span className="text-[11px] font-semibold text-ossia-muted uppercase tracking-wider">
          Tool Calls
        </span>

        {/* Badge: count + status */}
        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-ossia-bg text-ossia-muted-more ml-auto">
          {count} {count === 1 ? "call" : "calls"}
        </span>

        <span className={`text-[10px] font-mono ${statusColor(part.status)}`}>
          {statusLabel(part.status)}
        </span>
      </button>

      {/* ── Collapsible content — individual tool cards ──────────── */}
      {open && (
        <div className="divide-y divide-ossia-border-subtle">
          {children}
        </div>
      )}
    </div>
  );
}
