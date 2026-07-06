/**
 * HITL Interrupt Prompt.
 *
 * Renders a review card when the v3 stream surfaces an ``interrupt`` event.
 * The backend payload is a list of action requests; each one is rendered with
 * Approve / Reject buttons. ``useOssiaControls().resume()`` posts the
 * decisions to ``/v1/threads/{id}/resume`` and continues the run.
 */

import { useState } from "react";
import { useOssiaControls } from "./MyRuntimeProvider";
import { useSideChannel } from "../stores/sideChannel";

export function InterruptPrompt() {
  const { interrupts } = useSideChannel();
  const { resume } = useOssiaControls();
  const [busy, setBusy] = useState(false);

  if (interrupts.length === 0) return null;

  const handleApprove = async () => {
    setBusy(true);
    try {
      await resume(interrupts.map(() => ({ type: "approve" })));
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    setBusy(true);
    try {
      await resume(
        interrupts.map(() => ({ type: "reject", message: "Rejected by user." })),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-3 rounded-lg border border-ossia-yellow/40 bg-ossia-yellow-subtle/40 px-4 py-3 text-sm">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-base">🛡️</span>
        <span className="font-semibold text-[#0d0d0d] dark:text-[#ececec]">
          Human review required
        </span>
        <span className="text-[11px] font-mono text-ossia-muted ml-auto">
          {interrupts.length} action{interrupts.length !== 1 ? "s" : ""}
        </span>
      </div>
      <ul className="space-y-1.5 mb-3 max-h-[160px] overflow-y-auto">
        {interrupts.map((it, i) => (
          <li
            key={i}
            className="text-xs font-mono text-ossia-text-secondary bg-white/60 dark:bg-black/20 rounded px-2 py-1.5"
          >
            <ActionRequestSummary action={it} />
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-2">
        <button
          onClick={handleApprove}
          disabled={busy}
          className="px-3 py-1.5 text-xs font-medium rounded-md bg-ossia-green text-white hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {busy ? "Resuming…" : "Approve all"}
        </button>
        <button
          onClick={handleReject}
          disabled={busy}
          className="px-3 py-1.5 text-xs font-medium rounded-md border border-ossia-border bg-white dark:bg-transparent text-ossia-text hover:bg-black/5 dark:hover:bg-white/10 transition-colors disabled:opacity-50"
        >
          Reject all
        </button>
      </div>
    </div>
  );
}

/** Render a one-line summary of a single interrupt action_request. */
function ActionRequestSummary({ action }: { action: Record<string, unknown> }) {
  const req = (action.action_request ?? action) as Record<string, unknown>;
  const tool = String(req.name ?? req.tool ?? "unknown tool");
  const args = req.args ?? req.input;
  const argsStr =
    args && typeof args === "object"
      ? JSON.stringify(args).slice(0, 120)
      : "";
  return (
    <span>
      <span className="font-semibold text-ossia-text">{tool}</span>
      {argsStr && <span className="text-ossia-muted"> · {argsStr}</span>}
    </span>
  );
}
