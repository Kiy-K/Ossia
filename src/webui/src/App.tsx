/**
 * Ossia Web UI — ChatGPT-style chat interface.
 *
 * Full-screen chat layout matching the current chatgpt.com design:
 * - Minimal header (logo + dark mode + settings)
 * - Centered empty state with composer
 * - Sticky chat composer with tooltipped controls
 * - High-contrast user bubbles with Copy + Edit actions
 * - Assistant messages with full action bar
 */

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Plus,
  Mic,
  Square,
  ArrowUp,
  AudioLines,
  Copy,
  Pencil,
  ThumbsUp,
  ThumbsDown,
  Volume2,
  Share2,
  RefreshCw,
  MoreHorizontal,
  Sun,
  Moon,
  Settings,
  PanelLeftOpen,
} from "lucide-react";
import {
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  MessagePartPrimitive,
  ThreadPrimitive,
  useAui,
} from "@assistant-ui/react";
import { STORAGE_KEYS } from "./constants";
import type { Config } from "./types";
import { checkHealth } from "./stream";
import { MyRuntimeProvider } from "./components/MyRuntimeProvider";
import { MarkdownText } from "./components/MarkdownText";
import { ToolFallback } from "./components/ToolFallback";
import { TooltipIconButton } from "./components/TooltipIconButton";
import { SessionSidebar } from "./components/SessionSidebar";
import { InterruptPrompt } from "./components/InterruptPrompt";

// ── Theme-aware favicon ─────────────────────────────────────────────────────

const FAVICON_DARK = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="11" fill="none" stroke="white" stroke-width="5.5"/></svg>`;

function setFavicon(isDark: boolean) {
  const link = document.getElementById("favicon") as HTMLLinkElement | null;
  if (link) {
    link.href = `data:image/svg+xml;base64,${btoa(FAVICON_DARK)}`;
  }
}

// ── App component ───────────────────────────────────────────────────────────

export default function App() {
  const [config, setConfig] = useState<Config>(() => ({
    apiUrl: localStorage.getItem(STORAGE_KEYS.API_URL) || "http://localhost:8000",
    apiKey: localStorage.getItem(STORAGE_KEYS.API_KEY) || "dev",
  }));
  const [showConfig, setShowConfig] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [darkMode, setDarkMode] = useState(true);

  useEffect(() => {
    const check = async () => {
      const ok = await checkHealth(config);
      setIsConnected(ok);
    };
    check();
    const interval = setInterval(check, 15000);
    return () => clearInterval(interval);
  }, [config]);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.DARK_MODE);
    if (stored !== null) {
      const isDark = stored === "true";
      setDarkMode(isDark);
      document.documentElement.classList.toggle("dark", isDark);
    } else {
      document.documentElement.classList.add("dark");
      localStorage.setItem(STORAGE_KEYS.DARK_MODE, "true");
    }
  }, []);

  useEffect(() => setFavicon(darkMode), [darkMode]);

  const handleToggleDark = useCallback(() => {
    setDarkMode((prev) => {
      const next = !prev;
      document.documentElement.classList.toggle("dark", next);
      localStorage.setItem(STORAGE_KEYS.DARK_MODE, String(next));
      return next;
    });
  }, []);

  return (
    <MyRuntimeProvider config={config}>
      <AppBody
        config={config}
        showConfig={showConfig}
        setShowConfig={setShowConfig}
        isConnected={isConnected}
        setIsConnected={setIsConnected}
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        darkMode={darkMode}
        onToggleDark={handleToggleDark}
      />
    </MyRuntimeProvider>
  );
}

// ── App body (rendered inside the runtime provider so it can use the controls) ─

interface AppBodyProps {
  config: Config;
  showConfig: boolean;
  setShowConfig: (v: boolean) => void;
  isConnected: boolean;
  setIsConnected: (v: boolean) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean) => void;
  darkMode: boolean;
  onToggleDark: () => void;
}

function AppBody({
  config,
  showConfig,
  setShowConfig,
  isConnected,
  setIsConnected,
  sidebarOpen,
  setSidebarOpen,
  darkMode,
  onToggleDark,
}: AppBodyProps) {
  const saveConfig = useCallback(async (url: string, key: string) => {
    localStorage.setItem(STORAGE_KEYS.API_URL, url);
    localStorage.setItem(STORAGE_KEYS.API_KEY, key);
    setConfig({ apiUrl: url, apiKey: key });
    const ok = await checkHealth({ apiUrl: url, apiKey: key });
    setIsConnected(ok);
    setShowConfig(false);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-white text-[#0d0d0d] dark:bg-black dark:text-[#ececec]">
      {/* ── Minimal header ───────────────────────────────────── */}
      <header className="flex items-center justify-between px-4 py-1.5 shrink-0 min-h-10">
        <div className="flex items-center gap-1.5">
          {/* Sidebar toggle */}
          <button
            onClick={() => setSidebarOpen((prev) => !prev)}
            className="p-1.5 text-[#5d5d5d] dark:text-[#cdcdcd] hover:bg-black/7 dark:hover:bg-white/15 rounded-lg transition-colors"
            aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
          >
            <PanelLeftOpen size={16} />
          </button>
          <h1 className="text-sm font-semibold tracking-tight text-[#0d0d0d] dark:text-[#ececec]">
            Ossia
          </h1>
          <span
            className={`inline-block w-1.5 h-1.5 rounded-full transition-colors duration-300 ${
              isConnected ? "bg-green-500 dark:bg-green-400" : "bg-red-500"
            }`}
          />
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={onToggleDark}
            className="p-1.5 text-[#5d5d5d] dark:text-[#cdcdcd] hover:bg-black/7 dark:hover:bg-white/15 rounded-full transition-colors active:scale-[0.97]"
            aria-label={darkMode ? "Switch to light mode" : "Switch to dark mode"}
          >
            {darkMode ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button
            onClick={() => setShowConfig(!showConfig)}
            className="p-1.5 text-[#5d5d5d] dark:text-[#cdcdcd] hover:bg-black/7 dark:hover:bg-white/15 rounded-full transition-colors"
            aria-label="Settings"
          >
            <Settings size={16} />
          </button>
        </div>
      </header>

      {/* ── Config panel ──────────────────────────────────────── */}
      <AnimatePresence>
        {showConfig && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
          >
            <ConfigPanel config={config} onSave={saveConfig} onClose={() => setShowConfig(false)} />
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Body: sidebar + chat ─────────────────────────────── */}
      <div className="flex-1 flex overflow-hidden">
        {/* Session sidebar */}
        <SessionSidebar
          config={config}
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
        />

        {/* Main chat area */}
        <main className="flex-1 overflow-hidden">
          <ChatView />
        </main>
      </div>
    </div>
  );
}

// ── Config Panel ────────────────────────────────────────────────────────────

function ConfigPanel({
  config,
  onSave,
  onClose,
}: {
  config: Config;
  onSave: (url: string, key: string) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(config.apiUrl || "http://localhost:8000");
  const [key, setKey] = useState(config.apiKey);

  return (
    <div className="border-b border-[#e5e5e5] dark:border-transparent bg-white/80 dark:bg-[#212121]/80 backdrop-blur-sm px-5 py-4">
      <div className="flex items-end gap-4 max-w-xl mx-auto">
        <div className="flex-1">
          <label className="block text-[11px] font-medium text-[#5d5d5d] dark:text-[#afafaf] uppercase tracking-wider mb-1.5">
            API URL
          </label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="w-full bg-white dark:bg-[#0d0d0d] border border-[#e5e5e5] dark:border-[#3f3f46] rounded-lg px-3 py-2 text-sm text-[#0d0d0d] dark:text-[#ececec] outline-none transition-all placeholder:text-[#9ca3af] focus:border-[#0d0d0d] dark:focus:border-[#ececec]"
            placeholder="http://localhost:8000"
          />
        </div>
        <div className="flex-1">
          <label className="block text-[11px] font-medium text-[#5d5d5d] dark:text-[#afafaf] uppercase tracking-wider mb-1.5">
            API Key
          </label>
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="w-full bg-white dark:bg-[#0d0d0d] border border-[#e5e5e5] dark:border-[#3f3f46] rounded-lg px-3 py-2 text-sm text-[#0d0d0d] dark:text-[#ececec] outline-none transition-all placeholder:text-[#9ca3af] focus:border-[#0d0d0d] dark:focus:border-[#ececec]"
            placeholder="dev"
          />
        </div>
        <button
          onClick={() => onSave(url, key)}
          className="px-5 py-2 bg-[#0d0d0d] dark:bg-[#ececec] text-white dark:text-[#0d0d0d] text-sm font-medium rounded-lg hover:opacity-90 transition-all active:scale-[0.97]"
        >
          Connect
        </button>
        <button
          onClick={onClose}
          className="px-3 py-2 text-xs text-[#5d5d5d] dark:text-[#afafaf] hover:text-[#0d0d0d] dark:hover:text-[#ececec] transition-colors"
        >
          Close
        </button>
      </div>
    </div>
  );
}

// ── Empty state ─────────────────────────────────────────────────────────────

function EmptyState() {
  const aui = useAui();

  const suggestions = [
    "Write a TypeScript function to merge two sorted arrays",
    "Write a Python decorator that logs function execution time",
    "Explain the CAP theorem in simple terms",
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full select-none px-6">
      <h1 className="text-2xl font-semibold tracking-tight text-[#0d0d0d] dark:text-[#ececec] mb-6">
        Where should we begin?
      </h1>
      <div className="w-full max-w-2xl">
        <ChatComposer placeholder="Ask anything" />
        <div className="flex flex-wrap justify-center gap-2 mt-4">
          {suggestions.map((hint) => (
            <button
              key={hint}
              onClick={() =>
                aui.thread().append({
                  role: "user",
                  content: [{ type: "text", text: hint }],
                })
              }
              className="px-3 py-1.5 text-[13px] text-[#5d5d5d] dark:text-[#afafaf] hover:text-[#0d0d0d] dark:hover:text-[#ececec] hover:bg-black/5 dark:hover:bg-white/10 rounded-lg transition-colors text-left leading-snug border border-transparent hover:border-[#e5e5e5] dark:hover:border-[#2f2f2f]"
            >
              {hint}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Chat View ───────────────────────────────────────────────────────────────

function ChatView() {
  return (
    <ThreadPrimitive.Root className="flex flex-col h-full">
      <AuiIf condition={(s) => s.thread.isEmpty}>
        <EmptyState />
      </AuiIf>

      <AuiIf condition={(s) => !s.thread.isEmpty}>
        <ThreadPrimitive.Viewport
          className="flex-1 overflow-y-auto px-4"
          turnAnchor="top"
          autoScroll={false}
        >
          <div className="mx-auto max-w-3xl py-4">
            <ThreadPrimitive.Messages>
              {({ message }) => {
                if (message.role === "user") return <UserMessage />;
                return <AssistantMessage />;
              }}
            </ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter className="sticky bottom-0 pt-3 pb-4">
            <div className="mx-auto max-w-3xl">
              <InterruptPrompt />
              <ChatComposer placeholder="Ask anything" />
              <p className="text-center text-[11px] text-[#5d5d5d] dark:text-[#afafaf] mt-3">
                Ossia can make mistakes. Check important info.
              </p>
            </div>
          </ThreadPrimitive.ViewportFooter>
        </ThreadPrimitive.Viewport>
      </AuiIf>
    </ThreadPrimitive.Root>
  );
}

// ── ChatGPT Composer ────────────────────────────────────────────────────────

function ChatComposer({ placeholder }: { placeholder: string }) {
  return (
    <ComposerPrimitive.Root className="flex w-full items-end gap-1 rounded-[28px] border border-[#e5e5e5] bg-white px-3 py-2 focus-within:shadow-sm dark:border-transparent dark:bg-[#212121] dark:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.1)] transition-all duration-150">
      <ComposerPrimitive.AddAttachment asChild>
        <TooltipIconButton tooltip="Add photos & files" aria-label="Add attachment">
          <Plus size={18} />
        </TooltipIconButton>
      </ComposerPrimitive.AddAttachment>

      <ComposerPrimitive.Input
        autoFocus
        rows={1}
        placeholder={placeholder}
        className="min-h-10 w-full resize-none bg-transparent text-sm text-[#0d0d0d] dark:text-[#ececec] outline-none placeholder:text-[#9ca3af] dark:placeholder:text-[#6b6b6b] leading-relaxed px-1"
        submitMode="enter"
      />

      <PrimaryAction />
    </ComposerPrimitive.Root>
  );
}

// ── Four-State Primary Action ──────────────────────────────────────────────

function PrimaryAction() {
  return (
    <>
      <AuiIf condition={(s) => s.thread.isRunning}>
        <ComposerPrimitive.Cancel asChild>
          <TooltipIconButton tooltip="Cancel" aria-label="Cancel">
            <Square size={18} />
          </TooltipIconButton>
        </ComposerPrimitive.Cancel>
      </AuiIf>

      <AuiIf condition={(s) => !s.thread.isRunning && s.composer.dictation != null}>
        <ComposerPrimitive.StopDictation asChild>
          <TooltipIconButton tooltip="Stop dictation" aria-label="Stop dictation">
            <Square size={18} />
          </TooltipIconButton>
        </ComposerPrimitive.StopDictation>
      </AuiIf>

      <AuiIf condition={(s) => !s.thread.isRunning && s.composer.dictation == null && !s.composer.isEmpty}>
        <ComposerPrimitive.Send asChild>
          <button
            type="submit"
            className="flex items-center justify-center w-9 h-9 rounded-full bg-[#0d0d0d] dark:bg-white text-white dark:text-[#0d0d0d] hover:opacity-80 transition-all active:scale-[0.93]"
            aria-label="Send message"
          >
            <ArrowUp size={18} />
          </button>
        </ComposerPrimitive.Send>
      </AuiIf>

      <AuiIf condition={(s) => !s.thread.isRunning && s.composer.dictation == null && s.composer.isEmpty}>
        <ComposerPrimitive.Dictate asChild>
          <TooltipIconButton tooltip="Dictate" aria-label="Dictate">
            <Mic size={18} />
          </TooltipIconButton>
        </ComposerPrimitive.Dictate>
        <TooltipIconButton
          tooltip="Use voice mode"
          aria-hidden="true"
          tabIndex={-1}
          className="bg-[#0d0d0d] dark:bg-white text-white dark:text-[#0d0d0d] hover:opacity-80"
        >
          <AudioLines size={18} />
        </TooltipIconButton>
      </AuiIf>
    </>
  );
}

// ── User Message ────────────────────────────────────────────────────────────

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex flex-col items-end gap-1 mb-4 group">
      <div className="max-w-[70%] rounded-[22px] bg-[#0d0d0d] px-4 py-2.5 text-sm leading-relaxed text-white dark:bg-[#ececec] dark:text-[#0d0d0d]">
        <MessagePrimitive.Parts>
          {({ part }) => {
            if (part.type === "text") {
              return (
                <p className="whitespace-pre-wrap break-words">
                  <MessagePartPrimitive.Text />
                </p>
              );
            }
            return null;
          }}
        </MessagePrimitive.Parts>
      </div>

      {/* Copy + Edit actions (auto-hide on hover) */}
      <ActionBarPrimitive.Root autohide="always" className="flex items-center gap-0.5">
        <ActionBarPrimitive.Copy asChild>
          <TooltipIconButton tooltip="Copy" aria-label="Copy">
            <Copy size={16} />
          </TooltipIconButton>
        </ActionBarPrimitive.Copy>
        <ActionBarPrimitive.Edit asChild>
          <TooltipIconButton tooltip="Edit" aria-label="Edit">
            <Pencil size={16} />
          </TooltipIconButton>
        </ActionBarPrimitive.Edit>
      </ActionBarPrimitive.Root>
    </MessagePrimitive.Root>
  );
}

// ── Assistant Message ───────────────────────────────────────────────────────

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="flex flex-col gap-1 mb-4">
      <div className="max-w-[75%] text-sm leading-relaxed">
        <MessagePrimitive.Parts>
          {({ part }) => {
            switch (part.type) {
              case "text":
                return (
                  <div className="message-content">
                    <MarkdownText />
                  </div>
                );
              case "reasoning":
                return null;
              case "tool-call":
                return part.toolUI ?? <ToolFallback {...part} />;
              default:
                return null;
            }
          }}
        </MessagePrimitive.Parts>
      </div>

      {/* Full action bar */}
      <AssistantActionBar />
    </MessagePrimitive.Root>
  );
}

// ── Assistant Action Bar ────────────────────────────────────────────────────

function AssistantActionBar() {
  return (
    <div className="flex items-center gap-0.5 mt-1">
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="Copy" aria-label="Copy">
          <Copy size={16} />
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>

      <ActionBarPrimitive.FeedbackPositive asChild>
        <TooltipIconButton tooltip="Good response" aria-label="Good response">
          <ThumbsUp size={16} />
        </TooltipIconButton>
      </ActionBarPrimitive.FeedbackPositive>

      <ActionBarPrimitive.FeedbackNegative asChild>
        <TooltipIconButton tooltip="Bad response" aria-label="Bad response">
          <ThumbsDown size={16} />
        </TooltipIconButton>
      </ActionBarPrimitive.FeedbackNegative>

      <ActionBarPrimitive.Speak asChild>
        <TooltipIconButton tooltip="Read aloud" aria-label="Read aloud">
          <Volume2 size={16} />
        </TooltipIconButton>
      </ActionBarPrimitive.Speak>

      <TooltipIconButton tooltip="Share" aria-label="Share">
        <Share2 size={16} />
      </TooltipIconButton>

      <ActionBarPrimitive.Reload asChild>
        <TooltipIconButton tooltip="Regenerate" aria-label="Regenerate">
          <RefreshCw size={16} />
        </TooltipIconButton>
      </ActionBarPrimitive.Reload>

      <TooltipIconButton tooltip="More" aria-label="More">
        <MoreHorizontal size={16} />
      </TooltipIconButton>
    </div>
  );
}
