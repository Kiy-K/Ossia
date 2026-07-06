/**
 * Code-related tool UI components
 *
 * - search_codebase — ripgrep code search
 * - run_tests       — test runner (pytest, etc.)
 * - propose_fix     — code fix proposal with patch
 * - fetch_url       — URL content fetcher
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

// ── search_codebase ─────────────────────────────────────────────────────────

type SearchCodebaseArgs = { query?: string; path?: string };
type SearchCodebaseResult = {
  results?: Array<{ file: string; line: number; content: string }>;
};

export const SearchCodebaseUI: ToolCallMessagePartComponent<SearchCodebaseArgs, SearchCodebaseResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-blue text-xs">📁</span>
          <span className={cardTitle}>Code Search</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>Searching code for &ldquo;{args?.query ?? "…"}&rdquo;…</div>
        </div>
      </div>
    );
  }

  const results = result?.results ?? [];
  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Code Search</span>
        <span className={badge}>{results.length} matches</span>
      </div>
      <div className="divide-y divide-ossia-border-subtle">
        {results.length === 0 ? (
          <div className="px-3 py-3 text-xs text-ossia-muted italic">
            No matches found
          </div>
        ) : (
          results.slice(0, 5).map((r, i) => (
            <div key={i} className="px-3 py-2.5 hover:bg-ossia-surface-2/50 transition-colors">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-mono text-ossia-text truncate flex-1">
                  {r.file}:{r.line}
                </span>
              </div>
              <pre className={`${resultText} bg-ossia-bg rounded px-2 py-1 overflow-x-auto max-h-[80px]`}>
                {r.content}
              </pre>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

// ── fetch_url ───────────────────────────────────────────────────────────────

type FetchUrlArgs = { url?: string; question?: string };
type FetchUrlResult = { content?: string; title?: string };

export const FetchUrlUI: ToolCallMessagePartComponent<FetchUrlArgs, FetchUrlResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-blue text-xs">🔗</span>
          <span className={cardTitle}>Fetching URL</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            {args?.url ? `Fetching ${args.url.slice(0, 60)}…` : "Fetching…"}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>URL Fetched</span>
        {result?.title && <span className={badge}>{result.title}</span>}
      </div>
      <div className={`${resultText} px-3 py-2.5 max-h-[200px] overflow-y-auto`}>
        {result?.content?.slice(0, 500) ?? "(no content)"}
        {(result?.content?.length ?? 0) > 500 ? "…" : ""}
      </div>
    </div>
  );
};

// ── run_tests ───────────────────────────────────────────────────────────────

type RunTestsArgs = { path?: string; command?: string };
type RunTestsResult = { passed?: boolean; output?: string };

export const RunTestsUI: ToolCallMessagePartComponent<RunTestsArgs, RunTestsResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-yellow text-xs">🧪</span>
          <span className={cardTitle}>Running Tests</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            {args?.command ?? "pytest"} {args?.path ?? "tests/"}
          </div>
        </div>
      </div>
    );
  }

  const passed = result?.passed;
  const statusIcon = passed ? "✅" : "❌";
  const statusColor = passed ? "text-ossia-green" : "text-ossia-red";

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className={`text-xs ${statusColor}`}>{statusIcon}</span>
        <span className={cardTitle}>Test Results</span>
        <span className={`${badge} ${statusColor}`}>
          {passed ? "PASSED" : "FAILED"}
        </span>
      </div>
      <div className={`${resultText} px-3 py-2.5 max-h-[300px] overflow-y-auto`}>
        {result?.output?.slice(0, 1000) ?? "(no output)"}
        {(result?.output?.length ?? 0) > 1000 ? "…" : ""}
      </div>
    </div>
  );
};

// ── propose_fix ─────────────────────────────────────────────────────────────

type ProposeFixArgs = { issue_description?: string; file_path?: string };
type ProposeFixResult = {
  summary?: string;
  patch?: string;
  context_file?: string;
};

export const ProposeFixUI: ToolCallMessagePartComponent<ProposeFixArgs, ProposeFixResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-yellow text-xs">🔧</span>
          <span className={cardTitle}>Proposing Fix</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            Analysing issue{args?.file_path ? ` in ${args.file_path}` : ""}…
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Proposed Fix</span>
        {result?.context_file && <span className={badge}>{result.context_file}</span>}
      </div>
      {result?.summary && (
        <div className="px-3 py-2 border-b border-ossia-border-subtle">
          <div className="text-xs text-ossia-text-secondary font-medium">
            {result.summary}
          </div>
        </div>
      )}
      {result?.patch && (
        <div className="px-3 py-2.5">
          <pre className={`${resultText} bg-ossia-bg rounded px-2 py-1.5 overflow-x-auto max-h-[250px] leading-relaxed`}>
            {result.patch.slice(0, 1200)}
            {(result.patch?.length ?? 0) > 1200 ? "\n… (truncated)" : ""}
          </pre>
        </div>
      )}
    </div>
  );
};
