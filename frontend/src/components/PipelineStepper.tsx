"use client";

import React, { useEffect, useState } from "react";
import {
  Check,
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
    title: "1. AST Scan",
    desc: "Blast-radius inspection",
    icon: FileSearch,
  },
  {
    id: 2,
    title: "2. Live Delta",
    desc: "Tavily search & rules",
    icon: Sparkles,
  },
  {
    id: 3,
    title: "3. Smart Router",
    desc: "Super / Ultra tiering",
    icon: Cpu,
  },
  {
    id: 4,
    title: "4. Nebius Sandbox",
    desc: "Parity verification",
    icon: ShieldAlert,
  },
  {
    id: 5,
    title: "5. Rules Export",
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
    <div className="w-full bg-[#0a0a0a] border-b border-neutral-800 px-4 py-2 overflow-x-auto select-none">
      <div className="flex items-center justify-between min-w-[720px] gap-2">
        {STAGES.map((stage) => {
          const isCurrent = stage.id === currentStage && !isCompleted && !isError;
          const isDone =
            stage.id < currentStage || (stage.id === 5 && isCompleted);
          const isFailed = stage.id === currentStage && isError;
          const durationStr = formatDuration(stage.id);
          const Icon = stage.icon;

          return (
            <div
              key={stage.id}
              className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md border text-xs transition-colors flex-1 min-w-[130px] ${
                isFailed
                  ? "bg-neutral-900 border-neutral-700 text-neutral-200"
                  : isCurrent
                  ? "bg-neutral-800 border-neutral-600 text-white font-medium"
                  : isDone
                  ? "bg-neutral-900/60 border-neutral-800/80 text-neutral-300"
                  : "bg-transparent border-transparent text-neutral-500"
              }`}
            >
              <div
                className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 text-xs ${
                  isFailed
                    ? "bg-neutral-800 text-neutral-300"
                    : isCurrent
                    ? "bg-white text-black font-bold"
                    : isDone
                    ? "bg-neutral-800 text-neutral-300"
                    : "bg-neutral-900 text-neutral-600"
                }`}
              >
                {isFailed ? (
                  <AlertCircle className="w-3 h-3 text-neutral-300" />
                ) : isDone ? (
                  <Check className="w-3 h-3 text-neutral-300" />
                ) : isCurrent ? (
                  <Loader2 className="w-3 h-3 animate-spin text-black" />
                ) : (
                  <Icon className="w-3 h-3 text-neutral-500" />
                )}
              </div>

              <div className="flex flex-col min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="truncate">{stage.title}</span>
                  {durationStr && (
                    <span className="text-[10px] font-mono px-1 rounded bg-neutral-800 text-neutral-400">
                      {durationStr}
                    </span>
                  )}
                </div>
                <span className="text-[10px] text-neutral-500 truncate leading-tight">
                  {stage.desc}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
