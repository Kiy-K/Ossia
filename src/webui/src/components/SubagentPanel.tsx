import { Lightning, Pause } from "@phosphor-icons/react";
import { motion } from "motion/react";
import type { SubagentState, PipelineState } from "../types";

interface SubagentPanelProps {
  subagents: Record<string, SubagentState>;
  pipelines: Record<string, PipelineState>;
}

const stateConfig: Record<string, { color: string; dot: string | null; bar: string }> = {
  running: { color: "text-ossia-yellow", dot: "●", bar: "bg-gradient-to-r from-ossia-yellow to-amber-400" },
  completed: { color: "text-ossia-green", dot: "✓", bar: "bg-ossia-green" },
  error: { color: "text-ossia-red", dot: "✗", bar: "bg-ossia-red" },
  interrupted: { color: "text-ossia-blue", dot: null, bar: "bg-ossia-blue" },
};

function DotContent({ cfg }: { cfg: { color: string; dot: string | null; bar: string } | undefined }) {
  if (!cfg) return <>○</>;
  if (cfg.dot === null) return <Pause size={10} weight="fill" />;
  return <>{cfg.dot}</>;
}

function StateBadge({ state }: { state: string }) {
  const cfg = stateConfig[state];
  if (!cfg) return null;
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-mono font-medium px-2 py-0.5 rounded-md ${
      state === "running" ? "bg-ossia-yellow-subtle text-ossia-yellow" :
      state === "completed" ? "bg-ossia-green-subtle text-ossia-green" :
      state === "error" ? "bg-ossia-red-subtle text-ossia-red" :
      state === "interrupted" ? "bg-ossia-blue-subtle text-ossia-blue" :
      "bg-ossia-surface-2 text-ossia-muted"
    }`}>
      <DotContent cfg={cfg} />
      {state}
    </span>
  );
}

export function SubagentPanel({ subagents, pipelines }: SubagentPanelProps) {
  const hasAny = Object.keys(subagents).length > 0 || Object.keys(pipelines).length > 0;

  return (
    <div className="h-full overflow-y-auto px-5 py-5">
      {!hasAny && (
        <div className="flex flex-col items-center justify-center h-full select-none">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-ossia-accent/20 to-ossia-cyan/20 flex items-center justify-center mb-4 border border-ossia-border-subtle shadow-ossia-sm">
            <Lightning size={24} weight="bold" className="text-ossia-accent" />
          </div>
          <p className="text-sm font-semibold text-ossia-text-secondary">No subagents active yet</p>
          <p className="text-xs text-ossia-muted mt-1.5 text-center max-w-[280px] leading-relaxed">
            Subagents appear here when the coordinator delegates tasks.
          </p>
        </div>
      )}

      {/* Subagents */}
        {Object.keys(subagents).length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mb-8"
          >
            <div className="flex items-center gap-2 mb-3">
              <h3 className="text-[11px] font-semibold text-ossia-muted uppercase tracking-[0.12em]">
                Subagents
              </h3>
              <span className="text-[10px] text-ossia-muted-more font-mono">
                {Object.keys(subagents).length}
              </span>
            </div>
            <div className="space-y-2">
              {Object.values(subagents).map((sub, i) => {
                const cfg = stateConfig[sub.state];
                return (
                  <motion.div
                    key={sub.name}
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.04, ease: [0.16, 1, 0.3, 1] }}
                    className="group flex items-center gap-3 bg-ossia-surface rounded-xl px-4 py-3 border border-ossia-border-subtle hover:border-ossia-border hover:shadow-ossia-sm transition-all duration-150"
                  >
                    <div className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-sm ${
                      sub.state === "running"
                        ? "bg-ossia-yellow-subtle"
                        : sub.state === "completed"
                          ? "bg-ossia-green-subtle"
                          : sub.state === "error"
                            ? "bg-ossia-red-subtle"
                            : "bg-ossia-surface-2"
                    }`}>
                      <span className={cfg?.color || "text-ossia-muted"}>
                        <DotContent cfg={cfg} />
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-ossia-text truncate group-hover:text-ossia-text-secondary transition-colors">
                        {sub.name}
                      </div>
                      {sub.error && (
                        <div className="text-xs text-ossia-red mt-0.5 truncate">{sub.error}</div>
                      )}
                    </div>
                    <StateBadge state={sub.state} />
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        )}

      {/* Pipelines */}
        {Object.keys(pipelines).length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <div className="flex items-center gap-2 mb-3">
              <h3 className="text-[11px] font-semibold text-ossia-muted uppercase tracking-[0.12em]">
                Pipelines
              </h3>
              <span className="text-[10px] text-ossia-muted-more font-mono">
                {Object.keys(pipelines).length}
              </span>
            </div>
            <div className="space-y-3">
              {Object.values(pipelines).map((pipeline, pi) => (
                <motion.div
                  key={pipeline.pipeline_id}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: pi * 0.05, ease: [0.16, 1, 0.3, 1] }}
                  className="bg-ossia-surface rounded-xl px-4 py-3.5 border border-ossia-border-subtle hover:border-ossia-border transition-all duration-150"
                >
                  <div className="flex items-center gap-2.5 mb-3">
                    <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm ${
                      pipeline.state === "running"
                        ? "bg-ossia-yellow-subtle"
                        : pipeline.state === "completed"
                          ? "bg-ossia-green-subtle"
                          : pipeline.state === "failed"
                            ? "bg-ossia-red-subtle"
                            : "bg-ossia-surface-2"
                    }`}>
                      <span className={stateConfig[pipeline.state]?.color || "text-ossia-muted"}>
                        <DotContent cfg={stateConfig[pipeline.state]} />
                      </span>
                    </div>
                    <span className="text-sm font-medium capitalize text-ossia-text">
                      {pipeline.pipeline_type}
                    </span>
                    <span className="text-[11px] text-ossia-muted-more font-mono">
                      {pipeline.current_step}/{pipeline.total_steps}
                    </span>
                    <div className="ml-auto">
                      <StateBadge state={pipeline.state} />
                    </div>
                  </div>

                  {/* Progress bar */}
                  <div className="w-full h-2 bg-ossia-bg rounded-full mb-3 overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{
                        width: `${(pipeline.current_step / pipeline.total_steps) * 100}%`,
                      }}
                      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                      className={`h-full rounded-full ${
                        pipeline.state === "failed"
                          ? "bg-ossia-red"
                          : pipeline.state === "completed"
                            ? "bg-ossia-green"
                            : "progress-shimmer"
                      }`}
                    />
                  </div>

                  {/* Steps timeline */}
                  <div className="space-y-1 relative">
                    {pipeline.steps.map((step, si) => {
                      const scfg = stateConfig[step.state];
                      const isLast = si === pipeline.steps.length - 1;
                      return (
                        <motion.div
                          key={step.index}
                          initial={{ opacity: 0, x: -8 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: si * 0.03, ease: [0.16, 1, 0.3, 1] }}
                          className="flex items-center gap-2.5 text-xs py-1"
                        >
                          {/* Timeline dot */}
                          <div className="relative flex items-center justify-center">
                            <div className={`w-4 h-4 rounded-full flex items-center justify-center ${
                              step.state === "running"
                                ? "bg-ossia-yellow-subtle"
                                : step.state === "completed"
                                  ? "bg-ossia-green-subtle"
                                  : step.state === "failed"
                                    ? "bg-ossia-red-subtle"
                                    : "bg-ossia-surface-2"
                            }`}>
                              <span className={scfg?.color || "text-ossia-muted"} style={{ fontSize: 8 }}>
                                <DotContent cfg={scfg} />
                              </span>
                            </div>
                            {!isLast && (
                              <div className="absolute top-4 w-px h-3 bg-ossia-border-subtle" />
                            )}
                          </div>
                          <span className="text-ossia-text">{step.name}</span>
                          {step.error && (
                            <span className="text-ossia-red ml-auto truncate max-w-[240px]">
                              {step.error}
                            </span>
                          )}
                        </motion.div>
                      );
                    })}
                  </div>
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
    </div>
  );
}
