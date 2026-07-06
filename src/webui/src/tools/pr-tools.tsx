/**
 * PR and issue tool UI components
 *
 * - create_pr   — create a GitHub pull request
 * - fetch_issue — fetch issue / PR details from GitHub
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

// ── create_pr ────────────────────────────────────────────────────────────────

type CreatePrArgs = {
  repo?: string;
  title?: string;
  body?: string;
  head?: string;
  base?: string;
};
type CreatePrResult = { url?: string; number?: number };

export const CreatePrUI: ToolCallMessagePartComponent<CreatePrArgs, CreatePrResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-yellow text-xs">🔄</span>
          <span className={cardTitle}>Creating Pull Request</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            {args?.title ? `&ldquo;${args.title.slice(0, 60)}&rdquo;` : "Opening PR…"}
            {args?.head ? ` (${args.head} → ${args.base ?? "main"})` : ""}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Pull Request Created</span>
        {result?.number && (
          <span className={badge}>#{result.number}</span>
        )}
      </div>
      <div className="px-3 py-3 space-y-2">
        <div className="text-xs font-medium text-ossia-text">
          {args?.title ?? "(untitled)"}
        </div>
        {result?.url && (
          <div className={`${resultText} text-ossia-blue break-all`}>
            <a
              href={result.url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:no-underline"
            >
              {result.url}
            </a>
          </div>
        )}
        {args?.body && (
          <div className={`${resultText} border-t border-ossia-border-subtle pt-2 mt-2`}>
            {args.body.slice(0, 200)}
            {args.body.length > 200 ? "…" : ""}
          </div>
        )}
      </div>
    </div>
  );
};

// ── fetch_issue ─────────────────────────────────────────────────────────────

type FetchIssueArgs = { repo?: string; issue_number?: number };
type FetchIssueResult = {
  number?: number;
  title?: string;
  body?: string;
};

export const FetchIssueUI: ToolCallMessagePartComponent<FetchIssueArgs, FetchIssueResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-blue text-xs">📋</span>
          <span className={cardTitle}>Fetching Issue</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            {args?.repo ? `${args.repo}#${args.issue_number ?? "?"}` : "Loading…"}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Issue / PR</span>
        {result?.number && (
          <span className={badge}>#{result.number}</span>
        )}
      </div>
      <div className="divide-y divide-ossia-border-subtle">
        <div className="px-3 py-2.5">
          <div className="text-xs font-semibold text-ossia-text leading-snug">
            {result?.title ?? "(no title)"}
          </div>
          {args?.repo && (
            <div className="text-[10px] text-ossia-muted-more mt-0.5 font-mono">
              {args.repo}
            </div>
          )}
        </div>
        {result?.body && (
          <div className={`${resultText} px-3 py-2.5 max-h-[250px] overflow-y-auto`}>
            {result.body.slice(0, 500)}
            {result.body.length > 500 ? "…" : ""}
          </div>
        )}
      </div>
    </div>
  );
};
