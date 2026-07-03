import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ChatCircleText, Lightning, Wrench, Brain, Gear, Sun, Moon } from "@phosphor-icons/react";
import { STORAGE_KEYS } from "./constants";
import type { Config } from "./types";
import { initialAppState, reduceEvent } from "./reducer";
import { sendMessage, checkHealth } from "./stream";
import { ChatPanel } from "./components/ChatPanel";
import { SubagentPanel } from "./components/SubagentPanel";
import { ToolPanel } from "./components/ToolPanel";
import { ReActPanel } from "./components/ReActPanel";

const DEFAULT_CONFIG: Config = {
  apiUrl: "",
  apiKey: "dev",
};

type Panel = "chat" | "subagents" | "tools" | "react";

const PANEL_ICONS: Record<Panel, React.ElementType> = {
  chat: ChatCircleText,
  subagents: Lightning,
  tools: Wrench,
  react: Brain,
};

/* ── Theme-aware favicon ───────────────────────────────── */

const FAVICON_DARK_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="11" fill="none" stroke="#a78bfa" stroke-width="5.5"/></svg>';
const FAVICON_LIGHT_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="11" fill="none" stroke="#7c3aed" stroke-width="5.5"/></svg>';

function setFavicon(isDark: boolean) {
  const link = document.getElementById("favicon") as HTMLLinkElement | null;
  if (link) {
    link.href =
      "data:image/svg+xml;base64," + btoa(isDark ? FAVICON_DARK_SVG : FAVICON_LIGHT_SVG);
  }
}

export default function App() {
  const [config, setConfig] = useState<Config>(DEFAULT_CONFIG);
  const [state, dispatch] = useReducer(reduceEvent, undefined, initialAppState);
  const [isConnected, setIsConnected] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [userMessages, setUserMessages] = useState<{ text: string; timestamp: number }[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [activePanel, setActivePanel] = useState<Panel>(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.ACTIVE_PANEL);
    const valid: Panel[] = ["chat", "subagents", "tools", "react"];
    return valid.includes(stored as Panel) ? (stored as Panel) : "chat";
  });
  const [showConfig, setShowConfig] = useState(false);
  const [darkMode, setDarkMode] = useState(true);

  // Initialize dark mode from localStorage
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEYS.DARK_MODE);
    if (stored !== null) {
      const isDark = stored === "true";
      setDarkMode(isDark);
      document.documentElement.classList.toggle("dark", isDark);
    } else {
      // Default to dark, persist default
      document.documentElement.classList.add("dark");
      localStorage.setItem(STORAGE_KEYS.DARK_MODE, "true");
    }
  }, []);

  // Sync favicon with dark mode
  useEffect(() => {
    setFavicon(darkMode);
  }, [darkMode]);

  const handleToggleDark = useCallback(() => {
    setDarkMode((prev) => {
      const next = !prev;
      document.documentElement.classList.toggle("dark", next);
      localStorage.setItem(STORAGE_KEYS.DARK_MODE, String(next));
      return next;
    });
  }, []);

  // Check backend health on mount
  useEffect(() => {
    const check = async () => {
      const apiUrl = localStorage.getItem(STORAGE_KEYS.API_URL) || "";
      const apiKey = localStorage.getItem(STORAGE_KEYS.API_KEY) || "dev";
      const cfg = { apiUrl, apiKey };
      setConfig(cfg);
      if (apiUrl) {
        const ok = await checkHealth(cfg);
        setIsConnected(ok);
      }
    };
    check();
    const interval = setInterval(check, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleSend = useCallback(async (message: string) => {
    if (!message.trim() || isRunning) return;
    setIsRunning(true);
    setUserMessages((prev) => [...prev, { text: message.trim(), timestamp: Date.now() }]);

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      setErrorMessage(null);
      for await (const event of sendMessage(message, config, state.thread_id || undefined, abort.signal)) {
        dispatch(event);
      }
    } catch (err: unknown) {
      if ((err as Error)?.name !== "AbortError") {
        const msg = String(err);
        setErrorMessage(msg);
      }
    } finally {
      setIsRunning(false);
      abortRef.current = null;
    }
  }, [config, state.thread_id, isRunning]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const saveConfig = useCallback(async (url: string, key: string) => {
    localStorage.setItem(STORAGE_KEYS.API_URL, url);
    localStorage.setItem(STORAGE_KEYS.API_KEY, key);
    const cfg = { apiUrl: url, apiKey: key };
    setConfig(cfg);
    const ok = await checkHealth(cfg);
    setIsConnected(ok);
    setShowConfig(false);
  }, []);

  const panelCounts = {
    subagents: Object.keys(state.subagents).length,
    tools: state.tools.length,
    react: state.react_steps.length,
  };

  return (
    <div className="h-screen flex flex-col bg-ossia-bg text-ossia-text selection:bg-ossia-accent/30 selection:text-white">
      {/* Header */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-ossia-border-subtle bg-ossia-surface shrink-0 relative">
        {/* Subtle grain overlay */}
        <div className="absolute inset-0 opacity-[0.03] pointer-events-none"
          style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E\")" }}
        />

        <div className="flex items-center gap-3 relative">
          <h1 className="text-lg font-bold bg-gradient-to-r from-ossia-accent via-purple-400 to-ossia-cyan bg-clip-text text-transparent tracking-tight">
            Ossia
          </h1>
          <span className="h-4 w-px bg-ossia-border-subtle" />
          <span className="text-xs text-ossia-muted font-medium tracking-wide">Deep Agent UI</span>
          <div className="flex items-center gap-1.5 ml-1">
            <span className={`inline-block w-1.5 h-1.5 rounded-full transition-colors duration-300 ${
              isConnected ? "bg-ossia-green shadow-[0_0_6px_var(--color-ossia-green)]" : "bg-ossia-red"
            }`} />
            <span className="text-[10px] text-ossia-muted-more font-mono">
              {isConnected ? "LIVE" : "OFFLINE"}
            </span>
          </div>
        </div>

        {/* Panel tabs */}
        <nav className="flex gap-0.5 bg-ossia-bg/60 rounded-lg p-0.5 border border-ossia-border-subtle relative">
          {(["chat", "subagents", "tools", "react"] as Panel[]).map((panel) => (
            <button
              key={panel}
              onClick={() => {
                setActivePanel(panel);
                localStorage.setItem(STORAGE_KEYS.ACTIVE_PANEL, panel);
              }}
              className={`relative px-3 py-1.5 text-xs font-medium rounded-md transition-colors duration-150 ${
                activePanel === panel
                  ? "text-white"
                  : "text-ossia-muted hover:text-ossia-text-secondary"
              }`}
            >
              {activePanel === panel && (
                <motion.div
                  layoutId="active-tab"
                  className="absolute inset-0 bg-ossia-accent rounded-md"
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                />
              )}
              <span className="relative z-10 flex items-center gap-1.5">
                {(() => {
                  const Icon = PANEL_ICONS[panel];
                  return <Icon size={16} className="opacity-60" weight="bold" />;
                })()}
                {panel === "chat" && "Chat"}
                {panel === "subagents" && `Subagents${panelCounts.subagents > 0 ? ` ${panelCounts.subagents}` : ""}`}
                {panel === "tools" && `Tools${panelCounts.tools > 0 ? ` ${panelCounts.tools}` : ""}`}
                {panel === "react" && `ReAct${panelCounts.react > 0 ? ` ${panelCounts.react}` : ""}`}
              </span>
            </button>
          ))}
          <button
            onClick={() => setShowConfig(!showConfig)}
            className="px-2.5 py-1.5 text-xs text-ossia-muted hover:text-ossia-text-secondary rounded-md transition-colors ml-0.5 flex items-center justify-center"
          >
            <Gear size={16} weight="bold" />
          </button>
        </nav>

        <div className="flex items-center gap-2 relative">
          {state.run_state === "running" && (
            <button
              onClick={handleCancel}
              className="px-3 py-1.5 text-xs font-medium bg-ossia-red-subtle text-ossia-red rounded-md hover:bg-ossia-red/20 transition-colors active:scale-[0.97]"
            >
              Cancel
            </button>
          )}
          {state.thread_id && (
            <span className="text-[11px] text-ossia-muted-more font-mono tracking-tight">
              {state.thread_id.slice(0, 10)}
            </span>
          )}
          <button
            onClick={handleToggleDark}
            className="p-1.5 text-ossia-muted hover:text-ossia-text-secondary rounded-md transition-colors active:scale-[0.97]"
            aria-label={darkMode ? "Switch to light mode" : "Switch to dark mode"}
          >
            {darkMode ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </header>

      {/* Config panel */}
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

      {/* Main content with panel transitions */}
      <main className="flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={activePanel}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="h-full"
          >
            {activePanel === "chat" && (
              <ChatPanel
                messages={state.messages}
                userMessages={userMessages.map((m) => m.text)}
                streamingMessage={state.streamingMessage}
                runState={state.run_state}
                isRunning={isRunning}
                error={errorMessage || state.error}
                onSend={handleSend}
                onCancel={handleCancel}
                interrupts={state.interrupts}
              />
            )}
            {activePanel === "subagents" && (
              <SubagentPanel subagents={state.subagents} pipelines={state.pipelines} />
            )}
            {activePanel === "tools" && (
              <ToolPanel tools={state.tools} asyncTasks={state.async_tasks} />
            )}
            {activePanel === "react" && (
              <ReActPanel steps={state.react_steps} streamingMessage={state.streamingMessage} />
            )}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

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
    <div className="border-b border-ossia-border-subtle bg-ossia-surface-2/80 backdrop-blur-sm px-5 py-4">
      <div className="flex items-end gap-4 max-w-xl mx-auto">
        <div className="flex-1">
          <label className="block text-[11px] font-medium text-ossia-muted uppercase tracking-wider mb-1.5">
            API URL
          </label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="w-full bg-ossia-bg border border-ossia-border rounded-lg px-3 py-2 text-sm text-ossia-text outline-none transition-all duration-150 placeholder:text-ossia-muted-more focus:border-ossia-accent focus:shadow-[0_0_0_3px_var(--color-ossia-accent-subtle)]"
            placeholder="http://localhost:8000"
          />
        </div>
        <div className="flex-1">
          <label className="block text-[11px] font-medium text-ossia-muted uppercase tracking-wider mb-1.5">
            API Key
          </label>
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="w-full bg-ossia-bg border border-ossia-border rounded-lg px-3 py-2 text-sm text-ossia-text outline-none transition-all duration-150 placeholder:text-ossia-muted-more focus:border-ossia-accent focus:shadow-[0_0_0_3px_var(--color-ossia-accent-subtle)]"
            placeholder="dev"
          />
        </div>
        <button
          onClick={() => onSave(url, key)}
          className="px-5 py-2 bg-ossia-accent text-white text-sm font-medium rounded-lg hover:bg-ossia-accent-hover transition-all duration-150 active:scale-[0.97] shadow-ossia-sm"
        >
          Connect
        </button>
        <button
          onClick={onClose}
          className="px-3 py-2 text-xs text-ossia-muted hover:text-ossia-text-secondary transition-colors"
        >
          Close
        </button>
      </div>
    </div>
  );
}
