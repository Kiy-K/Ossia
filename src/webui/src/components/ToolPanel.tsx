import { useState } from "react";
import { Wrench } from "@phosphor-icons/react";
import { motion } from "motion/react";
import type { ToolState, AsyncTaskState } from "../types";

interface ToolPanelProps {
  tools: ToolState[];
  asyncTasks: AsyncTaskState[];
}

const stateConfig: Record<string, { color: string; dot: string; bg: string }> = {
  running: { color: "text-ossia-yellow", dot: "●", bg: "bg-ossia-yellow-subtle" },
  completed: { color: "text-ossia-green", dot: "✓", bg: "bg-ossia-green-subtle" },
  failed: { color: "text-ossia-red", dot: "✗", bg: "bg-ossia-red-subtle" },
};

function ToolCard({ tool, index }: { tool: ToolState; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = stateConfig[tool.state];
  const hasInput = Object.keys(tool.input).length > 0;
  const hasOutput = tool.output != null || tool.error != null;
  const canExpand = hasInput || hasOutput;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03, ease: [0.16, 1, 0.3, 1] }}
      className="bg-ossia-surface rounded-xl border border-ossia-border-subtle hover:border-ossia-border hover:shadow-ossia-sm transition-all duration-150 overflow-hidden"
    >
      <div
        className={`flex items-center gap-3 px-4 py-3 ${canExpand ? "cursor-pointer select-none" : ""}`}
        onClick={() => canExpand && setExpanded(!expanded)}
      >
        <div className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-sm ${cfg?.bg || "bg-ossia-surface-2"}`}>
          <span className={cfg?.color || "text-ossia-muted"}>{cfg?.dot || "○"}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-ossia-text truncate flex items-center gap-2">
            {tool.name}
            {tool.state === "running" && (
              <span className="relative flex w-1.5 h-1.5">
                <span className="absolute inline-flex w-full h-full rounded-full bg-ossia-yellow opacity-75 animate-ping" />
                <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-ossia-yellow" />
              </span>
            )}
          </div>
          {tool.state === "running" && (
            <div className="text-[11px] text-ossia-muted mt-0.5">Executing...</div>
          )}
        </div>
        <span className={`text-[11px] font-mono font-medium px-2 py-0.5 rounded-md ${cfg?.bg || "bg-ossia-surface-2"} ${cfg?.color || "text-ossia-muted"}`}>
          {tool.state}
        </span>
        {canExpand && (
          <motion.span
            animate={{ rotate: expanded ? 180 : 0 }}
            transition={{ duration: 0.2 }}
            className="text-[10px] text-ossia-muted-more"
          >
            ▼
          </motion.span>
        )}
      </div>

      {/* Expandable content — uses CSS grid-rows transition instead of animated height */}
      <div
        className={`grid transition-all duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden min-h-0">
          <div className="border-t border-ossia-border-subtle px-4 py-3 space-y-2">
            {hasInput && (
              <div>
                <div className="text-[10px] text-ossia-muted-more font-medium uppercase tracking-wider mb-1">Input</div>
                <pre className="text-xs text-ossia-muted font-mono bg-ossia-bg rounded-lg px-3 py-2 overflow-x-auto whitespace-pre-wrap break-words max-h-[200px] overflow-y-auto">
                  {JSON.stringify(tool.input, null, 2)}
                </pre>
              </div>
            )}
            {tool.output != null && (
              <div>
                <div className="text-[10px] text-ossia-muted-more font-medium uppercase tracking-wider mb-1">Output</div>
                <div className="text-xs text-ossia-green font-mono bg-ossia-bg rounded-lg px-3 py-2 overflow-x-auto">
                  {String(tool.output).slice(0, 500)}
                  {String(tool.output).length > 500 && "..."}
                </div>
              </div>
            )}
            {tool.error && (
              <div>
                <div className="text-[10px] text-ossia-muted-more font-medium uppercase tracking-wider mb-1">Error</div>
                <div className="text-xs text-ossia-red font-mono bg-ossia-bg rounded-lg px-3 py-2">
                  {tool.error}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

export function ToolPanel({ tools, asyncTasks }: ToolPanelProps) {
  const hasAny = tools.length > 0 || asyncTasks.length > 0;

  return (
    <div className="h-full overflow-y-auto px-5 py-5">
      {!hasAny && (
        <div className="flex flex-col items-center justify-center h-full select-none">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-ossia-accent/20 to-ossia-cyan/20 flex items-center justify-center mb-4 border border-ossia-border-subtle shadow-ossia-sm">
            <Wrench size={24} weight="bold" className="text-ossia-accent" />
          </div>
          <p className="text-sm font-semibold text-ossia-text-secondary">No tool calls yet</p>
          <p className="text-xs text-ossia-muted mt-1.5 text-center max-w-[280px] leading-relaxed">
            Tool invocations appear here as the agent runs.
          </p>
        </div>
      )}

      {/* Tools */}
      {tools.length > 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mb-8">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-[11px] font-semibold text-ossia-muted uppercase tracking-[0.12em]">
              Tool Calls
            </h3>
            <span className="text-[10px] text-ossia-muted-more font-mono">{tools.length}</span>
          </div>
          <div className="space-y-2">
            {[...tools].reverse().map((tool, i) => (
              <ToolCard key={`${tool.name}-${tool.startedAt}-${i}`} tool={tool} index={i} />
            ))}
          </div>
        </motion.div>
      )}

      {/* Async Tasks */}
      {asyncTasks.length > 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-[11px] font-semibold text-ossia-muted uppercase tracking-[0.12em]">
              Background Tasks
            </h3>
            <span className="text-[10px] text-ossia-muted-more font-mono">{asyncTasks.length}</span>
          </div>
          <div className="space-y-2">
            {asyncTasks.map((task, i) => {
              const isComplete = task.status === "completed" || task.status === "success";
              const isFailed = task.status === "failed" || task.status === "error";
              return (
                <motion.div
                  key={task.task_id}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.04, ease: [0.16, 1, 0.3, 1] }}
                  className="group flex items-center gap-3 bg-ossia-surface rounded-xl px-4 py-3 border border-ossia-border-subtle hover:border-ossia-border hover:shadow-ossia-sm transition-all duration-150"
                >
                  <div className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-sm ${
                    isComplete ? "bg-ossia-green-subtle" : isFailed ? "bg-ossia-red-subtle" : "bg-ossia-yellow-subtle"
                  }`}>
                    <span className={isComplete ? "text-ossia-green" : isFailed ? "text-ossia-red" : "text-ossia-yellow"}>
                      {isComplete ? "✓" : isFailed ? "✗" : "●"}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-ossia-text truncate group-hover:text-ossia-text-secondary transition-colors">
                      {task.agent_name || task.task_id}
                    </div>
                    {task.error && <div className="text-xs text-ossia-red mt-0.5 truncate">{task.error}</div>}
                  </div>
                  <span className={`text-[11px] font-mono font-medium px-2 py-0.5 rounded-md ${
                    isComplete ? "bg-ossia-green-subtle text-ossia-green" :
                    isFailed ? "bg-ossia-red-subtle text-ossia-red" :
                    "bg-ossia-yellow-subtle text-ossia-yellow"
                  }`}>
                    {task.status}
                  </span>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      )}
    </div>
  );
}
