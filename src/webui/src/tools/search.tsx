/**
 * Search tool UI components
 *
 * - search_knowledge_base — KB document search
 * - internet_search       — Web search (Tavily / DuckDuckGo)
 * - qna_search            — Q&A / semantic search
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

// ── search_knowledge_base ───────────────────────────────────────────────────

type SearchKBArgs = { query?: string; limit?: number };
type SearchKBResult = { results?: Array<{ title: string; content: string; source: string }> };

export const SearchKBUI: ToolCallMessagePartComponent<SearchKBArgs, SearchKBResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-blue text-xs">🔍</span>
          <span className={cardTitle}>Knowledge Base Search</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            Searching &ldquo;{args?.query ?? "…"}&rdquo;…
          </div>
        </div>
      </div>
    );
  }

  const results = result?.results ?? [];
  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Knowledge Base Search</span>
        <span className={badge}>{results.length} results</span>
      </div>
      <div className="divide-y divide-ossia-border-subtle">
        {results.length === 0 ? (
          <div className="px-3 py-3 text-xs text-ossia-muted italic">
            No results found for &ldquo;{args?.query ?? "…"}&rdquo;
          </div>
        ) : (
          results.slice(0, 5).map((r, i) => (
            <div key={i} className="px-3 py-2.5 hover:bg-ossia-surface-2/50 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-medium text-ossia-text truncate flex-1">
                  {r.title || `Result ${i + 1}`}
                </span>
                <span className={badge}>KB</span>
              </div>
              <div className={`${resultText} mt-1 line-clamp-2`}>
                {r.content?.slice(0, 200)}
                {(r.content?.length ?? 0) > 200 ? "…" : ""}
              </div>
              {r.source && (
                <div className="text-[10px] text-ossia-muted-more mt-1 truncate font-mono">
                  {r.source}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

// ── internet_search ─────────────────────────────────────────────────────────

type InternetSearchArgs = { query?: string };
type InternetSearchResult = {
  results?: Array<{ title: string; url: string; content: string }>;
};

export const InternetSearchUI: ToolCallMessagePartComponent<InternetSearchArgs, InternetSearchResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-blue text-xs">🌐</span>
          <span className={cardTitle}>Web Search</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>Searching for &ldquo;{args?.query ?? "…"}&rdquo;…</div>
        </div>
      </div>
    );
  }

  const results = result?.results ?? [];
  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Web Search</span>
        <span className={badge}>{results.length} results</span>
      </div>
      <div className="divide-y divide-ossia-border-subtle">
        {results.length === 0 ? (
          <div className="px-3 py-3 text-xs text-ossia-muted italic">
            No results found
          </div>
        ) : (
          results.slice(0, 5).map((r, i) => (
            <div key={i} className="px-3 py-2.5 hover:bg-ossia-surface-2/50 transition-colors">
              <div className="text-xs font-medium text-ossia-blue truncate">
                {r.title || r.url}
              </div>
              <div className={`${resultText} mt-0.5 line-clamp-2`}>
                {r.content?.slice(0, 200)}
                {(r.content?.length ?? 0) > 200 ? "…" : ""}
              </div>
              {r.url && (
                <div className="text-[10px] text-ossia-muted-more mt-0.5 truncate">
                  {r.url}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

// ── qna_search ──────────────────────────────────────────────────────────────

type QnaSearchArgs = { query?: string; topic?: string };
type QnaSearchResult = { query?: string; answer?: string; backend?: string };

export const QnaSearchUI: ToolCallMessagePartComponent<QnaSearchArgs, QnaSearchResult> = ({
  args,
  result,
  status,
}) => {
  if (status.type === "running") {
    return (
      <div className={card}>
        <div className={cardHeader}>
          <span className="text-ossia-purple text-xs">💡</span>
          <span className={cardTitle}>Q&amp;A Search</span>
        </div>
        <div className="px-3 py-3">
          <div className={loadingPulse}>
            Answering &ldquo;{args?.query ?? "…"}&rdquo;…
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={card}>
      <div className={cardHeader}>
        <span className="text-ossia-green text-xs">✓</span>
        <span className={cardTitle}>Q&amp;A Answer</span>
        {result?.backend && <span className={badge}>{result.backend}</span>}
      </div>
      <div className={`${resultText} px-3 py-2.5`}>
        {result?.answer ?? "(no answer)"}
      </div>
    </div>
  );
};
