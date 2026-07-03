import { Brain } from "@phosphor-icons/react";
import { motion, AnimatePresence } from "motion/react";
import type { ReActStep } from "../types";

interface ReActPanelProps {
  steps: ReActStep[];
  streamingMessage: string;
}

const kindConfig = {
  thought: {
    label: "Thought",
    color: "text-ossia-accent",
    bg: "bg-ossia-accent-subtle",
    border: "border-ossia-accent/20",
    letter: "T",
    gradient: "from-ossia-accent/20 to-purple-500/10",
  },
  action: {
    label: "Action",
    color: "text-ossia-blue",
    bg: "bg-ossia-blue-subtle",
    border: "border-ossia-blue/20",
    letter: "A",
    gradient: "from-ossia-blue/20 to-sky-500/10",
  },
  observation: {
    label: "Observation",
    color: "text-ossia-green",
    bg: "bg-ossia-green-subtle",
    border: "border-ossia-green/20",
    letter: "O",
    gradient: "from-ossia-green/20 to-emerald-500/10",
  },
};

function StepCard({ step, index, isLast }: { step: ReActStep; index: number; isLast: boolean }) {
  const cfg = kindConfig[step.kind];
  const isActionFailed = step.kind === "observation" && step.success === false;

  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.04, ease: [0.16, 1, 0.3, 1] }}
      className="flex gap-3"
    >
      {/* Timeline connector */}
      <div className="flex flex-col items-center">
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", stiffness: 300, damping: 15, delay: index * 0.04 }}
          className={`w-7 h-7 rounded-xl flex items-center justify-center text-xs font-bold
            ${isActionFailed
              ? "bg-ossia-red-subtle text-ossia-red"
              : `${cfg.bg} ${cfg.color}`
            }
            border ${isActionFailed ? "border-ossia-red/20" : cfg.border}
            shadow-ossia-sm`}
        >
          {isActionFailed ? "✗" : cfg.letter}
        </motion.div>
        {!isLast && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: "100%" }}
            transition={{ duration: 0.3, delay: index * 0.04 }}
            className="w-px bg-gradient-to-b from-ossia-border to-ossia-border-subtle flex-1 min-h-6"
          />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 pb-5 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className={`text-[11px] font-medium ${isActionFailed ? "text-ossia-red" : cfg.color}`}>
            {isActionFailed ? "Failed" : cfg.label}
          </span>
          {step.tool && (
            <span className="text-[10px] font-mono text-ossia-muted-more bg-ossia-surface-2 px-1.5 py-0.5 rounded">
              {step.tool}
            </span>
          )}
          <span className="text-[10px] text-ossia-muted-more ml-auto">{step.time}</span>
        </div>

        <div className={`bg-ossia-surface rounded-xl px-4 py-3 border ${
          isActionFailed ? "border-ossia-red/20" : "border-ossia-border-subtle"
        } hover:border-ossia-border transition-colors duration-150`}>
          {step.kind === "thought" && (
            <div className="text-sm text-ossia-text-secondary whitespace-pre-wrap break-words leading-relaxed">
              {step.content}
            </div>
          )}
          {step.kind === "action" && (
            <div>
              <div className="text-sm font-semibold text-ossia-blue mb-1.5 tracking-tight">
                {step.tool}
              </div>
              {step.input && (
                <pre className="text-xs text-ossia-muted font-mono bg-ossia-bg rounded-lg p-2.5 overflow-x-auto whitespace-pre-wrap break-words max-h-[160px] overflow-y-auto">
                  {JSON.stringify(step.input, null, 2).slice(0, 300)}
                  {JSON.stringify(step.input, null, 2).length > 300 && "..."}
                </pre>
              )}
            </div>
          )}
          {step.kind === "observation" && (
            <div>
              {step.success !== false ? (
                <div className="text-sm text-ossia-text whitespace-pre-wrap break-words leading-relaxed">
                  {step.output != null
                    ? String(step.output).slice(0, 300)
                    : <span className="text-ossia-muted italic">(no output)</span>}
                </div>
              ) : (
                <div className="text-sm text-ossia-red">
                  <span className="font-medium">Failed:</span> {step.error || "Tool call failed"}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export function ReActPanel({ steps, streamingMessage }: ReActPanelProps) {
  if (steps.length === 0 && !streamingMessage) {
    return (
      <div className="flex flex-col items-center justify-center h-full select-none">
        <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-ossia-accent/20 to-ossia-cyan/20 flex items-center justify-center mb-4 border border-ossia-border-subtle shadow-ossia-sm">
          <Brain size={24} weight="bold" className="text-ossia-accent" />
        </div>
        <p className="text-sm font-semibold text-ossia-text-secondary">No reasoning steps yet</p>
        <p className="text-xs text-ossia-muted mt-1.5 text-center max-w-[280px] leading-relaxed">
          The agent's thought-action-observation loop appears here.
        </p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-5 py-5">
      <div className="flex items-center gap-2 mb-4">
        <h3 className="text-[11px] font-semibold text-ossia-muted uppercase tracking-[0.12em]">
          Reasoning Loop
        </h3>
        <span className="text-[10px] text-ossia-muted-more font-mono">
          {steps.length} step{steps.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="space-y-1">
        <AnimatePresence>
          {steps.map((step, i) => (
            <StepCard
              key={`step-${i}`}
              step={step}
              index={i}
              isLast={i === steps.length - 1 && !streamingMessage}
            />
          ))}
        </AnimatePresence>

        {/* Streaming indicator as a new thought step */}
        {streamingMessage && (
          <motion.div
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
            className="flex gap-3"
          >
            <div className="flex flex-col items-center">
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: "spring", stiffness: 300, damping: 15 }}
                className="w-7 h-7 rounded-xl flex items-center justify-center text-xs font-bold bg-ossia-accent-subtle text-ossia-accent border border-ossia-accent/20 shadow-ossia-sm"
              >
                T
              </motion.div>
            </div>
            <div className="flex-1 pb-5">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[11px] font-medium text-ossia-accent">Thought</span>
                <span className="text-[10px] text-ossia-muted-more ml-auto">
                  {new Date().toLocaleTimeString()}
                </span>
              </div>
              <div className="bg-ossia-surface rounded-xl px-4 py-3 border border-ossia-border-subtle">
                <span className="streaming-cursor text-sm text-ossia-text-secondary leading-relaxed">
                  {streamingMessage}
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}
