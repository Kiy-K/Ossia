/**
 * ToolFallback — default UI for tool calls without a custom renderer.
 *
 * Renders a compact card showing the tool name, arguments (formatted as
 * JSON), and result or error state. Styled consistently with the custom
 * Tool UI components in ``ossia-toolkit``.
 *
 * This component is used as the fallback in ``AssistantMessage`` when
 * ``part.toolUI`` is ``null`` (no custom renderer registered for the tool).
 */

"use client";

import { type ToolCallContentPartProps } from "@assistant-ui/react";

// ── Shared styles (mirrors ossia-toolkit.tsx) ───────────────────────────────

const card =
  "rounded-lg border border-ossia-border-subtle bg-ossia-surface overflow-hidden text-sm";

const cardHeader =
  "flex items-center gap-2 px-3 py-2 border-b border-ossia-border-subtle bg-ossia-surface-2";

const cardTitle =
  "text-[11px] font-semibold text-ossia-muted uppercase tracking-wider";

const badge =
  "text-[10px] font-mono px-1.5 py-0.5 rounded bg-ossia-bg text-ossia-muted-more";

const resultText =
  "text-xs text-ossia-text-secondary font-mono leading-relaxed whitespace-pre-wrap break-words";

const loadingPulse =
  "animate-pulse text-ossia-muted-more text-xs font-mono";

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatValue(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "string") return v.length > 120 ? `${v.slice(0, 120)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    const s = JSON.stringify(v, null, 1);
    return s.length > 300 ? `${s.slice(0, 300)}…` : s;
  } catch {
    return String(v);
  }
}

// ── ToolFallback component ──────────────────────────────────────────────────

export function ToolFallback(props: ToolCallContentPartProps) {
  // Guard against undefined status (ExternalStoreRuntime may not provide it).
  // Same pattern as ``withSafeStatus`` in ossia-toolkit.tsx.
  const status = props.status ?? { type: "complete" as const };
  const { toolName, args, result, isError } = props;

  const toolIcon = toolName.startsWith("mcp_") ? "🔌" : "⚙️";

  // ── Running state ───────────────────────────────────────────────────
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-blue text-xs">{toolIcon}</span>
          <span className={cardTitle}>{toolName}</span>
        </div>
        <div className="px-3 py-2">
          <div className={loadingPulse}>Running…</div>
          {args && typeof args === "object" && Object.keys(args).length > 0 && (
            <pre className={`${resultText} mt-1.5 max-h-[120px] overflow-y-auto`}>
              {JSON.stringify(args, null, 1).slice(0, 400)}
            </pre>
          )}
        </div>
      </div>
    );
  }

  // ── Error / incomplete state ────────────────────────────────────────
  if (status.type === "incomplete" || isError) {
    const reason =
      status.type === "incomplete" && "reason" in status
        ? (status as { reason?: string }).reason ?? "error"
        : "error";
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-red text-xs">⚠️</span>
          <span className={cardTitle}>{toolName}</span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-ossia-red/10 text-ossia-red">
            {reason}
          </span>
        </div>
        <div className="px-3 py-2.5">
          <div className="text-xs text-ossia-red font-medium">
            {result ? formatValue(result) : "Tool execution failed."}
          </div>
          {args && typeof args === "object" && Object.keys(args).length > 0 && (
            <details className="mt-1.5">
              <summary className="text-[10px] text-ossia-muted-more cursor-pointer hover:text-ossia-muted">
                Arguments
              </summary>
              <pre className={`${resultText} mt-1 max-h-[120px] overflow-y-auto`}>
                {JSON.stringify(args, null, 1).slice(0, 400)}
              </pre>
            </details>
          )}
        </div>
      </div>
    );
  }

  // ── Completed state ─────────────────────────────────────────────────
  const hasResult = result !== undefined && result !== null;
  const hasArgs =
    args && typeof args === "object" && Object.keys(args).length > 0;

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>{toolName}</span>
        {!hasResult && <span className={badge}>done</span>}
      </div>
      <div className="divide-y divide-ossia-border-subtle">
        {hasArgs && (
          <details className="px-3 py-2" open={!hasResult}>
            <summary className="text-[10px] text-ossia-muted-more cursor-pointer hover:text-ossia-muted font-medium uppercase tracking-wider">
              Input
            </summary>
            <pre className={`${resultText} mt-1 max-h-[150px] overflow-y-auto`}>
              {JSON.stringify(args, null, 1).slice(0, 600)}
            </pre>
          </details>
        )}
        {hasResult && (
          <div className="px-3 py-2">
            <div className="text-[10px] text-ossia-muted-more font-medium uppercase tracking-wider mb-1">
              Result
            </div>
            <div className={`${resultText} max-h-[300px] overflow-y-auto`}>
              {formatValue(result)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
