"use client";

import React from "react";
import {
  Square,
  Zap,
  Github,
  Sparkles,
} from "lucide-react";

interface TopControlBarProps {
  repoUrl: string;
  setRepoUrl: (val: string) => void;
  detectedInfo?: {
    library: string;
    targetVersion: string;
    sourceVersion?: string | null;
  } | null;
  isRunning: boolean;
  onRunMigration: (customRepo?: string) => void;
  onStopMigration: () => void;
}

export function TopControlBar({
  repoUrl,
  setRepoUrl,
  detectedInfo,
  isRunning,
  onRunMigration,
  onStopMigration,
}: TopControlBarProps) {
  const handleJudgeDemoClick = () => {
    const demoRepo = "./demo_target_repo";
    setRepoUrl(demoRepo);
    onRunMigration(demoRepo);
  };

  return (
    <header className="w-full bg-[#090d18]/90 border-b border-slate-800/80 px-5 py-3 flex flex-wrap items-center justify-between gap-3 shadow-lg shadow-black/40 z-20">
      {/* Brand & Tagline */}
      <div className="flex items-center gap-3">
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold tracking-tight bg-gradient-to-r from-blue-400 via-indigo-300 to-cyan-300 bg-clip-text text-transparent">
              DriftZero
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-blue-500/10 border border-blue-500/30 text-blue-400">
              Autonomous
            </span>
          </div>
          <span className="text-xs text-slate-400 hidden sm:inline">
            Autonomous Breaking-Change Migration Engine
          </span>
        </div>
      </div>

      {/* Center: Controls Input Bar (Zero manual library/version input required) */}
      <div className="flex items-center flex-1 max-w-3xl gap-2 flex-wrap sm:flex-nowrap">
        {/* Repo URL Input (Developer only enters the repo URL) */}
        <div className="relative flex-1 min-w-[280px]">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400">
            <Github className="w-4 h-4" />
          </div>
          <input
            type="text"
            id="repo-url-input"
            disabled={isRunning}
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="Enter any GitHub repository URL (e.g. https://github.com/shubhv123/pdf-rag) or local path"
            className="w-full bg-slate-900/90 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/70 focus:ring-1 focus:ring-blue-500/40 transition-all font-mono"
          />
        </div>

        {/* Autonomous Detection Pill (dynamically surfaces the discovered target) */}
        {detectedInfo && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 font-mono text-[11px] whitespace-nowrap shadow-sm shadow-emerald-950">
            <Sparkles className="w-3.5 h-3.5 text-emerald-400 animate-spin" style={{ animationDuration: "3s" }} />
            <span>
              Target: <strong className="text-white">{detectedInfo.library}</strong> ({detectedInfo.sourceVersion || "legacy"} → {detectedInfo.targetVersion})
            </span>
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {isRunning ? (
            <button
              onClick={onStopMigration}
              id="stop-migration-btn"
              className="flex items-center gap-1.5 px-3 py-1.5 bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/40 rounded-lg text-xs font-medium transition-all shadow-sm shadow-rose-900/30"
            >
              <Square className="w-3.5 h-3.5 fill-current" />
              <span>Abort</span>
            </button>
          ) : (
            <button
              onClick={() => onRunMigration()}
              id="run-migration-btn"
              className="flex items-center gap-1.5 px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-blue-600/30 transition-all active:scale-95 whitespace-nowrap"
            >
              <Zap className="w-3.5 h-3.5 fill-current" />
              <span>Fix Repository</span>
            </button>
          )}

          {/* ⚡ 1-Click Instant Judge Demo Button */}
          <button
            onClick={handleJudgeDemoClick}
            id="judge-demo-btn"
            disabled={isRunning}
            title="Pre-loads demo repo and autonomously migrates across all stages"
            className="flex items-center gap-1.5 px-3.5 py-1.5 bg-gradient-to-r from-amber-500/20 via-amber-500/30 to-yellow-500/20 hover:from-amber-500/30 hover:to-yellow-500/30 text-amber-300 border border-amber-500/50 rounded-lg text-xs font-semibold shadow-sm shadow-amber-500/20 transition-all active:scale-95 disabled:opacity-50 group whitespace-nowrap"
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-400 fill-amber-400/80 group-hover:scale-110 transition-transform" />
            <span>1-Click Judge Demo</span>
          </button>
        </div>
      </div>
    </header>
  );
}
