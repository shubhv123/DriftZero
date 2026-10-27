"use client";

import React, { useEffect, useState } from "react";
import {
  CheckCircle2,
  Clock,
  Loader2,
  AlertCircle,
  FileSearch,
  Sparkles,
  Cpu,
  ShieldAlert,
  Download,
} from "lucide-react";
import { StageTiming } from "../types/migration";

interface PipelineStepperProps {
  currentStage: number; // 0 (idle), 1, 2, 3, 4, 5
  isCompleted: boolean;
  isError: boolean;
  stageTimings: Record<number, StageTiming>;
}

const STAGES = [
  {
    id: 1,
    title: "1. AST Blast-Radius",
    desc: "Zero-Token AST Scan",
    icon: FileSearch,
  },
  {
    id: 2,
    title: "2. Tavily Live Delta",
    desc: "Search & Synthesize",
    icon: Sparkles,
  },
  {
    id: 3,
    title: "3. Token Factory Router",
    desc: "Super / Ultra Tiered",
    icon: Cpu,
  },
  {
    id: 4,
    title: "4. Nebius Sandbox & Heal",
    desc: "Differential Parity",
    icon: ShieldAlert,
  },
  {
    id: 5,
    title: "5. Context Rules Export",
    desc: ".cursorrules & PR",
    icon: Download,
  },
];

export function PipelineStepper({
  currentStage,
  isCompleted,
  isError,
  stageTimings,
}: PipelineStepperProps) {
  // Live tick for running stage elapsed duration
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (currentStage > 0 && !isCompleted && !isError) {
      const interval = setInterval(() => setNow(Date.now()), 100);
      return () => clearInterval(interval);
    }
  }, [currentStage, isCompleted, isError]);

  const formatDuration = (stageId: number) => {
    const timing = stageTimings[stageId];
    if (!timing) return null;

    if (timing.durationMs !== undefined && timing.durationMs > 0) {
      return timing.durationMs < 1000
        ? `${Math.round(timing.durationMs)}ms`
        : `${(timing.durationMs / 1000).toFixed(1)}s`;
    }

    if (stageId === currentStage && timing.startedAt) {
      const elapsed = Math.max(0, now - timing.startedAt);
      return elapsed < 1000
        ? `${elapsed}ms`
        : `${(elapsed / 1000).toFixed(1)}s`;
    }

    return null;
  };

  return (
    <div className="w-full bg-[#0a0f1d]/80 border-b border-slate-800/60 px-4 py-2.5 overflow-x-auto">
      <div className="flex items-center justify-between min-w-[760px] gap-2">
        {STAGES.map((stage, idx) => {
          const isCurrent = stage.id === currentStage && !isCompleted && !isError;
          const isDone =
            stage.id < currentStage || (stage.id === 5 && isCompleted);
          const isFailed = stage.id === currentStage && isError;
          const durationStr = formatDuration(stage.id);
          const Icon = stage.icon;

          return (
            <React.Fragment key={stage.id}>
              <div
                className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border transition-all ${
                  isFailed
                    ? "bg-rose-950/30 border-rose-500/50 text-rose-300"
                    : isCurrent
                    ? "bg-blue-600/15 border-blue-500/60 text-blue-300 shadow-sm shadow-blue-500/20"
                    : isDone
                    ? "bg-emerald-950/20 border-emerald-500/40 text-emerald-300"
                    : "bg-slate-900/40 border-slate-800/40 text-slate-500"
                }`}
              >
                <div
                  className={`w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 text-xs font-bold ${
                    isFailed
                      ? "bg-rose-500/20 text-rose-400"
                      : isCurrent
                      ? "bg-blue-500 text-white animate-pulse"
                      : isDone
                      ? "bg-emerald-500/20 text-emerald-400"
                      : "bg-slate-800 text-slate-500"
                  }`}
                >
                  {isFailed ? (
                    <AlertCircle className="w-3.5 h-3.5 text-rose-400" />
                  ) : isDone ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  ) : isCurrent ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-white" />
                  ) : (
                    <Icon className="w-3.5 h-3.5" />
                  )}
                </div>

                <div className="flex flex-col">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold tracking-tight">
                      {stage.title}
                    </span>
                    {durationStr && (
                      <span
                        className={`text-[10px] font-mono px-1.5 py-0.2 rounded-md font-medium ${
                          isDone
                            ? "bg-emerald-500/20 text-emerald-300"
                            : isCurrent
                            ? "bg-blue-500/30 text-blue-200 animate-pulse"
                            : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {durationStr}
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] text-slate-400/80 leading-none">
                    {stage.desc}
                  </span>
                </div>
              </div>

              {idx < STAGES.length - 1 && (
                <div
                  className={`flex-1 h-[2px] min-w-4 max-w-10 transition-colors ${
                    stage.id < currentStage
                      ? "bg-emerald-500/60 shadow-sm shadow-emerald-500/40"
                      : stage.id === currentStage
                      ? "bg-gradient-to-r from-blue-500 to-slate-800 animate-pulse"
                      : "bg-slate-800/80"
                  }`}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
