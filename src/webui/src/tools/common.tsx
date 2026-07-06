/**
 * Shared utilities and style constants for Ossia tool UI components.
 *
 * Exports the ``withSafeStatus`` wrapper (guards against undefined ``status``
 * from ExternalStoreRuntime) and the Tailwind class strings used by every
 * tool card for consistent styling.
 */

"use client";

import { type ToolCallMessagePartComponent } from "@assistant-ui/react";

// ── Safe status wrapper ───────────────────────────────────────────────────
// With ExternalStoreRuntime, the runtime may not provide `status` on
// tool-call content parts. This wrapper supplies a default "complete"
// status so components don't crash accessing `status.type`.

export function withSafeStatus<TArgs, TResult>(
  Component: ToolCallMessagePartComponent<TArgs, TResult>,
): ToolCallMessagePartComponent<TArgs, TResult> {
  const Wrapped: ToolCallMessagePartComponent<TArgs, TResult> = (props) => {
    const safeStatus = props.status ?? { type: "complete" as const };
    return <Component {...props} status={safeStatus} />;
  };
  Wrapped.displayName = `withSafeStatus(${Component.displayName || Component.name || "ToolUI"})`;
  return Wrapped;
}

// ── Shared style constants ────────────────────────────────────────────────

export const card =
  "rounded-lg border border-ossia-border-subtle bg-ossia-surface overflow-hidden text-sm";

export const cardHeader =
  "flex items-center gap-2 px-3 py-2 border-b border-ossia-border-subtle bg-ossia-surface-2";

export const cardTitle =
  "text-[11px] font-semibold text-ossia-muted uppercase tracking-wider";

export const badge =
  "text-[10px] font-mono px-1.5 py-0.5 rounded bg-ossia-bg text-ossia-muted-more";

export const resultText =
  "text-xs text-ossia-text-secondary font-mono leading-relaxed whitespace-pre-wrap break-words";

export const loadingPulse =
  "animate-pulse text-ossia-muted-more text-xs font-mono";
