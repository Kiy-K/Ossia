/**
 * Response / communication tool UI components
 *
 * - send_response  — send a response reply to the user
 * - grade_response — grade / evaluate response quality
 */

"use client";

import { type ToolCallMessagePartComponent } from "@assistant-ui/react";
import {
  badge,
  card,
  cardHeader,
  cardTitle,
  loadingPulse,
  resultText,
} from "./common";

// ── send_response ───────────────────────────────────────────────────────────

type SendResponseArgs = { response?: string; channel?: string };
type SendResponseResult = { sent?: boolean; id?: string };

export const SendResponseUI: ToolCallMessagePartComponent<SendResponseArgs, SendResponseResult> = ({
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-yellow text-xs">✉️</span>
          <span className={cardTitle}>Sending Response…</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>Drafting reply…</div>
        </div>
      </div>
    );
  }

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Response Sent</span>
        {result?.id && <span className={badge}>#{result.id}</span>}
      </div>
      <div className="px-3 py-3 text-xs text-ossia-text-secondary">
        {result?.sent !== false
          ? "Response has been delivered."
          : "Response could not be sent."}
      </div>
    </div>
  );
};

// ── grade_response ──────────────────────────────────────────────────────────

type GradeResponseArgs = { text?: string };
type GradeResponseResult = {
  grade?: string;
  score?: number;
  reasoning?: string;
};

export const GradeResponseUI: ToolCallMessagePartComponent<GradeResponseArgs, GradeResponseResult> = ({
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-yellow text-xs">📊</span>
          <span className={cardTitle}>Grading Response…</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>Analysing quality…</div>
        </div>
      </div>
    );
  }

  const grade = result?.grade ?? "unknown";
  const score = result?.score;
  const isGood = grade === "good" || grade === "pass" || (score !== undefined && score >= 0.7);
  const gradeColor = isGood ? "text-ossia-green" : "text-ossia-yellow";

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className={`text-xs ${gradeColor}`}>📊</span>
        <span className={cardTitle}>Quality Grade</span>
        <span className={`${badge} ${gradeColor}`}>
          {grade}{score !== undefined ? ` (${(score * 100).toFixed(0)}%)` : ""}
        </span>
      </div>
      {result?.reasoning && (
        <div className="px-3 py-2.5 border-t border-ossia-border-subtle">
          <div className={resultText}>{result.reasoning}</div>
        </div>
      )}
    </div>
  );
};
