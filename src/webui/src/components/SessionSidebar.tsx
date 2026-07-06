/**
 * Session Sidebar — custom thread list with click-to-switch.
 *
 * Layout matches ChatGPT's sidebar: scrollable thread list with
 * "New Chat" button at the top, each thread showing a truncated
 * title + relative timestamp. Thread metadata comes from
 * ``GET /v1/threads``; per-thread titles are loaded on first render
 * from ``GET /v1/threads/{id}/history`` (backend now also returns
 * the title in the list response, so this fallback is rare).
 *
 * Nested-button fix: the thread button and its delete action are
 * siblings inside the same ``<li>``, not nested. Previously the
 * delete button lived inside the thread button, which triggered
 * React's "button cannot be a descendant of button" hydration error.
 *
 * TODO: migrate to ``ThreadListPrimitive`` + ``ThreadListItemPrimitive``
 * once the inner runtime exposes a ``history`` adapter. The backend
 * endpoints and ``ossiaThreadListAdapter`` are ready.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Clock, MessageCircle, Plus, Search, Trash2, X } from "lucide-react";
import type { Config } from "../types";
import { useOssiaControls } from "./MyRuntimeProvider";
import { useSideChannel } from "../stores/sideChannel";

// ── Types ───────────────────────────────────────────────────────────────────

interface ThreadSummary {
  thread_id: string;
  updated_at: string;
  message_count: number;
  title: string | null;
}

interface ThreadListResponse {
  threads: Array<{
    thread_id: string;
    updated_at: string;
    message_count: number;
  }>;
  total: number;
}

interface ThreadHistoryResponse {
  thread_id: string;
  messages: Array<{ role: string; content: string }>;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

async function fetchThreadTitle(
  apiUrl: string,
  apiKey: string,
  threadId: string,
  signal: AbortSignal,
): Promise<string | null> {
  try {
    const resp = await fetch(
      `${apiUrl}/v1/threads/${encodeURIComponent(threadId)}/history`,
      { headers: { "X-API-Key": apiKey }, signal },
    );
    if (!resp.ok) return null;
    const data = (await resp.json()) as ThreadHistoryResponse;
    const firstUser = data.messages.find((m) => m.role === "user");
    if (!firstUser) return null;
    const text = firstUser.content?.trim();
    if (!text) return null;
    return text.length > 52 ? text.slice(0, 49) + "…" : text;
  } catch {
    return null;
  }
}

function relativeTime(iso: string): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "";
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w ago`;
}

function displayFallbackTitle(thread_id: string): string {
  const colon = thread_id.indexOf(":");
  const raw = colon >= 0 ? thread_id.slice(colon + 1) : thread_id;
  return raw.length > 8 ? raw.slice(0, 8) : raw;
}

// ── Props ───────────────────────────────────────────────────────────────────

interface SessionSidebarProps {
  config: Config;
  isOpen: boolean;
  onClose: () => void;
}

// ── Component ───────────────────────────────────────────────────────────────

export function SessionSidebar({ config, isOpen, onClose }: SessionSidebarProps) {
  const { switchThread } = useOssiaControls();
  const { thread_id: activeThreadId } = useSideChannel();
  const handleSwitchThread = useCallback(
    (threadId: string) => {
      void switchThread(threadId);
    },
    [switchThread],
  );

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const AbortRef = useRef<AbortController | null>(null);
  const titlesRequestedRef = useRef<Set<string>>(new Set());

  const loadTitle = useCallback(
    async (threadId: string) => {
      if (titlesRequestedRef.current.has(threadId)) return;
      titlesRequestedRef.current.add(threadId);
      const signal = AbortRef.current?.signal;
      if (!signal) return;
      const title = await fetchThreadTitle(config.apiUrl, config.apiKey, threadId, signal);
      if (signal.aborted) return;
      setThreads((prev) =>
        prev.map((t) =>
          t.thread_id === threadId
            ? { ...t, title: title ?? displayFallbackTitle(threadId) }
            : t,
        ),
      );
    },
    [config.apiUrl, config.apiKey],
  );

  const fetchThreads = useCallback(async () => {
    AbortRef.current?.abort();
    titlesRequestedRef.current.clear();
    const controller = new AbortController();
    AbortRef.current = controller;
    setLoading(true);
    try {
      const resp = await fetch(`${config.apiUrl}/v1/threads?limit=50`, {
        headers: { "X-API-Key": config.apiKey },
        signal: controller.signal,
      });
      if (!resp.ok) {
        setThreads([]);
        return;
      }
      const data = (await resp.json()) as ThreadListResponse;
      setThreads(
        data.threads.map((t) => ({
          thread_id: t.thread_id,
          updated_at: t.updated_at,
          message_count: t.message_count,
          title: null,
        })),
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setThreads([]);
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [config.apiUrl, config.apiKey]);

  useEffect(() => {
    if (isOpen) {
      void fetchThreads();
    }
    return () => {
      AbortRef.current?.abort();
    };
  }, [isOpen, fetchThreads]);

  // Load titles for visible threads after the list renders
  useEffect(() => {
    for (const t of threads) {
      if (t.title === null) {
        void loadTitle(t.thread_id).catch((e: unknown) =>
          console.warn("[SessionSidebar] title load failed:", t.thread_id, e),
        );
      }
    }
  }, [threads, loadTitle]);

  const filteredThreads = threads.filter((t) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (t.title ?? displayFallbackTitle(t.thread_id)).toLowerCase().includes(q);
  });

  const handleNew = useCallback(() => {
    handleSwitchThread("");
    onClose();
  }, [handleSwitchThread, onClose]);

  return (
    <aside
      className={`${
        isOpen ? "w-72" : "w-0"
      } shrink-0 transition-all duration-200 overflow-hidden bg-[#f9f9f9] dark:bg-[#1a1a1a] border-r border-[#e5e5e5] dark:border-[#2f2f2f]`}
    >
      <div className="h-full flex flex-col w-72">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 shrink-0">
          <h2 className="text-sm font-semibold text-[#0d0d0d] dark:text-[#ececec]">
            Sessions
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 text-[#5d5d5d] dark:text-[#cdcdcd] hover:bg-black/7 dark:hover:bg-white/15 rounded-lg transition-colors"
            aria-label="Close sidebar"
          >
            <X size={16} />
          </button>
        </div>

        {/* New Chat button */}
        <div className="px-3 pb-2">
          <button
            onClick={handleNew}
            className="flex items-center gap-2 w-full px-3 py-2 text-sm text-[#5d5d5d] dark:text-[#cdcdcd] hover:bg-black/7 dark:hover:bg-white/15 rounded-lg transition-colors border border-dashed border-[#e5e5e5] dark:border-[#2f2f2f]"
          >
            <Plus size={16} />
            <span>New Chat</span>
          </button>
        </div>

        {/* Search */}
        <div className="px-3 pb-2">
          <div className="relative">
            <Search
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#9ca3af] dark:text-[#6b6b6b] pointer-events-none"
            />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search sessions…"
              className="w-full pl-8 pr-2 py-1.5 text-xs bg-transparent border border-[#e5e5e5] dark:border-[#2f2f2f] rounded-md outline-none focus:border-[#0d0d0d] dark:focus:border-[#ececec] text-[#0d0d0d] dark:text-[#ececec] placeholder:text-[#9ca3af] dark:placeholder:text-[#6b6b6b]"
            />
          </div>
        </div>

        {/* Thread list */}
        <div className="flex-1 overflow-y-auto px-2">
          {loading && threads.length === 0 ? (
            <div className="text-xs text-[#9ca3af] dark:text-[#6b6b6b] text-center py-4">
              Loading…
            </div>
          ) : filteredThreads.length === 0 ? (
            <div className="text-xs text-[#9ca3af] dark:text-[#6b6b6b] text-center py-4">
              No sessions yet
            </div>
          ) : (
            <ul className="space-y-0.5">
              {filteredThreads.map((t) => (
                <li
                  key={t.thread_id}
                  className={`group relative flex items-center rounded-lg text-sm hover:bg-black/5 dark:hover:bg-white/10 ${
                    t.thread_id === activeThreadId
                      ? "bg-black/10 dark:bg-white/15"
                      : ""
                  }`}
                >
                  <button
                    onClick={() => handleSwitchThread(t.thread_id)}
                    className="flex-1 min-w-0 text-left px-2.5 py-2"
                  >
                    <div className="flex items-start gap-2.5">
                      <MessageCircle
                        size={14}
                        className="shrink-0 mt-0.5 text-[#9ca3af] dark:text-[#6b6b6b]"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="truncate text-[13px] text-[#0d0d0d] dark:text-[#ececec]">
                          {t.title ?? displayFallbackTitle(t.thread_id)}
                        </div>
                        <div className="flex items-center gap-1 text-[11px] text-[#9ca3af] dark:text-[#6b6b6b] mt-0.5">
                          <Clock size={10} />
                          <span>{relativeTime(t.updated_at)}</span>
                          {t.message_count > 0 && (
                            <>
                              <span>·</span>
                              <span>{t.message_count} msg</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void fetch(
                        `${config.apiUrl}/v1/threads/${encodeURIComponent(t.thread_id)}`,
                        {
                          method: "DELETE",
                          headers: { "X-API-Key": config.apiKey },
                        },
                      ).then(() => {
                        setThreads((prev) =>
                          prev.filter((x) => x.thread_id !== t.thread_id),
                        );
                        if (t.thread_id === activeThreadId) handleNew();
                      });
                    }}
                    className="opacity-0 group-hover:opacity-100 p-1 mr-1 text-[#9ca3af] dark:text-[#6b6b6b] hover:text-red-500 dark:hover:text-red-400 rounded transition-all"
                    aria-label={`Delete session ${t.title ?? displayFallbackTitle(t.thread_id)}`}
                  >
                    <Trash2 size={12} />
                  </button>
                </li>
              ))}
            </ul>
           )}
         </div>
       </div>
     </aside>
   );
}
