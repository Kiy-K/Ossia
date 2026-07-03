import { useRef, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Brain, Pause, XCircle, ArrowBendLeftDown } from "@phosphor-icons/react";
import type { ChatMessage, InterruptData } from "../types";

interface ChatPanelProps {
  messages: ChatMessage[];
  userMessages: string[];
  streamingMessage: string;
  runState: string;
  isRunning: boolean;
  error: string | null;
  onSend: (message: string) => void;
  onCancel: () => void;
  interrupts: InterruptData | null;
}

export function ChatPanel({
  messages,
  userMessages,
  streamingMessage,
  runState,
  isRunning,
  error,
  onSend,
  onCancel,
  interrupts,
}: ChatPanelProps) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingMessage]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim() && !isRunning) {
      onSend(input.trim());
      setInput("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
        {/* User messages appear instantly */}
        {userMessages.map((msg, i) => (
          <motion.div
            key={`user-${msg}-${i}`}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="flex justify-end"
          >
            <div className="max-w-[70%] rounded-xl px-4 py-2.5 text-sm leading-relaxed bg-ossia-accent-subtle text-ossia-text border border-ossia-accent/20 shadow-ossia-sm">
              {msg}
            </div>
          </motion.div>
        ))}

        {/* Empty state */}
        {messages.length === 0 && userMessages.length === 0 && !isRunning && (
          <div className="flex flex-col items-center justify-center h-full select-none">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-ossia-accent/20 to-ossia-cyan/20 flex items-center justify-center mb-5 border border-ossia-border-subtle shadow-ossia-sm">
              <Brain size={28} weight="bold" className="text-ossia-accent" />
            </div>
            <p className="text-base font-semibold text-ossia-text-secondary">Ossia Deep Agent</p>
            <p className="text-sm text-ossia-muted mt-1.5 max-w-[280px] text-center leading-relaxed">
              Send a message to start collaborating with the agent.
            </p>
            <div className="flex gap-3 mt-6">
              {["Search the codebase", "Run the audit", "Debug this issue"].map((hint) => (
                <button
                  key={hint}
                  onClick={() => onSend(hint)}
                  className="px-3 py-1.5 text-xs bg-ossia-surface-2 border border-ossia-border text-ossia-muted rounded-lg hover:text-ossia-text-secondary hover:border-ossia-border transition-colors"
                >
                  {hint}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Message list */}
          {messages.map((msg, i) => (
            <motion.div
              key={`msg-${i}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[70%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-ossia-accent-subtle text-ossia-text border border-ossia-accent/20"
                    : msg.role === "tool"
                      ? "bg-ossia-surface-2 text-ossia-muted text-xs font-mono border border-ossia-border-subtle"
                      : "bg-ossia-surface border border-ossia-border-subtle shadow-ossia-sm"
                }`}
              >
                {msg.role === "assistant" && i === messages.length - 1 && streamingMessage && !msg.content ? null : (
                  <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                )}
              </div>
            </motion.div>
          ))}

        {/* Streaming indicator */}
        {streamingMessage && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex justify-start"
          >
            <div className="max-w-[70%] rounded-xl px-4 py-2.5 text-sm leading-relaxed bg-ossia-surface border border-ossia-border-subtle shadow-ossia-sm">
              <span className="streaming-cursor">{streamingMessage}</span>
            </div>
          </motion.div>
        )}

        {/* Running indicator */}
        {isRunning && !streamingMessage && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex justify-start"
          >
            <div className="rounded-xl px-4 py-3 text-sm bg-ossia-surface border border-ossia-border-subtle flex items-center gap-2.5 shadow-ossia-sm">
              <span className="relative flex w-2 h-2">
                <span className="absolute inline-flex w-full h-full rounded-full bg-ossia-yellow opacity-75 animate-ping" />
                <span className="relative inline-flex w-2 h-2 rounded-full bg-ossia-yellow" />
              </span>
              <span className="text-ossia-muted text-xs font-medium">Agent is working...</span>
            </div>
          </motion.div>
        )}

        {/* Interrupt indicator */}
        {runState === "interrupted" && interrupts && (
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex justify-center"
          >
            <div className="rounded-xl px-5 py-2.5 text-sm bg-ossia-yellow-subtle border border-ossia-yellow/25 text-ossia-yellow font-medium shadow-ossia-sm">
              <Pause size={14} weight="fill" className="mr-2" /> Run paused - awaiting human review
            </div>
          </motion.div>
        )}

        {/* Error */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="flex justify-center"
            >
              <div className="rounded-xl px-5 py-2.5 text-sm bg-ossia-red-subtle border border-ossia-red/25 text-ossia-red shadow-ossia-sm max-w-[80%]">
                <XCircle size={14} weight="bold" className="mr-1.5" /> {error}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="border-t border-ossia-border-subtle bg-ossia-surface/95 backdrop-blur-sm px-5 py-4">
        <form onSubmit={handleSubmit} className="flex gap-2.5 items-end">
          <div className="flex-1 relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                runState === "interrupted"
                  ? "Type feedback to resume..."
                  : "Send a message..."
              }
              rows={1}
              className="w-full bg-ossia-bg border border-ossia-border rounded-xl px-4 py-2.5 pr-10 text-sm text-ossia-text outline-none resize-none transition-all duration-150 placeholder:text-ossia-muted-more focus:border-ossia-accent focus:shadow-[0_0_0_3px_var(--color-ossia-accent-subtle)]"
              disabled={isRunning}
            />
            <kbd className="absolute right-3 bottom-3 hidden sm:inline-flex items-center justify-center w-5 h-5 text-[10px] text-ossia-muted-more bg-ossia-surface-2 rounded border border-ossia-border-subtle">
              <ArrowBendLeftDown size={12} weight="bold" />
            </kbd>
          </div>
          {isRunning ? (
            <button
              type="button"
              onClick={onCancel}
              className="px-4 py-2.5 bg-ossia-red-subtle text-ossia-red text-sm font-medium rounded-xl hover:bg-ossia-red/20 transition-all duration-150 active:scale-[0.97]"
            >
              Cancel
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="px-5 py-2.5 bg-ossia-accent text-white text-sm font-medium rounded-xl hover:bg-ossia-accent-hover transition-all duration-150 disabled:opacity-35 disabled:cursor-not-allowed active:scale-[0.97] shadow-ossia-sm"
            >
              Send
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
