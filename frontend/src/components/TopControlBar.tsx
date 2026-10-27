"use client";

import React, { useState } from "react";
import {
  Square,
  Zap,
  Github,
  Sparkles,
  FileText,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

interface TopControlBarProps {
  repoUrl: string;
  setRepoUrl: (val: string) => void;
  targetLibrary?: string;
  setTargetLibrary?: (val: string) => void;
  targetVersion?: string;
  setTargetVersion?: (val: string) => void;
  buildLog?: string;
  setBuildLog?: (val: string) => void;
  detectedInfo?: {
    library: string;
    targetVersion: string;
    sourceVersion?: string | null;
  } | null;
  isRunning: boolean;
  onRunMigration: (
    customRepo?: string,
    targetLib?: string,
    targetVer?: string,
    buildLog?: string
  ) => void;
  onStopMigration: () => void;
}

export function TopControlBar({
  repoUrl,
  setRepoUrl,
  targetLibrary,
  setTargetLibrary,
  targetVersion,
  setTargetVersion,
  buildLog,
  setBuildLog,
  detectedInfo,
  isRunning,
  onRunMigration,
  onStopMigration,
}: TopControlBarProps) {
  const [internalLib, setInternalLib] = useState("");
  const [internalVer, setInternalVer] = useState("");
  const [internalLog, setInternalLog] = useState("");
  const [showLogDrawer, setShowLogDrawer] = useState(false);

  const lib = targetLibrary !== undefined ? targetLibrary : internalLib;
  const setLib = setTargetLibrary || setInternalLib;
  const ver = targetVersion !== undefined ? targetVersion : internalVer;
  const setVer = setTargetVersion || setInternalVer;
  const log = buildLog !== undefined ? buildLog : internalLog;
  const setLog = setBuildLog || setInternalLog;

  const handleFixRepository = () => {
    onRunMigration(repoUrl, lib, ver, log);
  };

  const handleJudgeDemoClick = () => {
    const demoRepo = "./demo_target_repo";
    setRepoUrl(demoRepo);
    onRunMigration(demoRepo, lib, ver, log);
  };

  return (
    <header className="w-full bg-[#090d18]/90 border-b border-slate-800/80 px-5 py-3 flex flex-col gap-2 shadow-lg shadow-black/40 z-20">
      <div className="flex flex-wrap items-center justify-between gap-3">
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

        {/* Center: Controls Input Bar */}
        <div className="flex items-center flex-1 max-w-4xl gap-2 flex-wrap sm:flex-nowrap">
          {/* Repo URL Input */}
          <div className="relative flex-1 min-w-[240px]">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400">
              <Github className="w-4 h-4" />
            </div>
            <input
              type="text"
              id="repo-url-input"
              disabled={isRunning}
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="Enter GitHub URL or local path (e.g. https://github.com/shubhv123/pdf-rag)"
              className="w-full bg-slate-900/90 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/70 focus:ring-1 focus:ring-blue-500/40 transition-all font-mono"
            />
          </div>

          {/* Optional Target Library Input */}
          <div className="relative w-28 sm:w-32 flex-shrink-0">
            <input
              type="text"
              id="target-library-input"
              disabled={isRunning}
              value={lib}
              onChange={(e) => setLib(e.target.value)}
              placeholder="Lib (opt: pydantic)"
              title="Optional target library (auto-detected if blank)"
              className="w-full bg-slate-900/90 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/70 focus:ring-1 focus:ring-blue-500/40 transition-all font-mono"
            />
          </div>

          {/* Optional Target Version Input */}
          <div className="relative w-24 sm:w-28 flex-shrink-0">
            <input
              type="text"
              id="target-version-input"
              disabled={isRunning}
              value={ver}
              onChange={(e) => setVer(e.target.value)}
              placeholder="Ver (opt: 2.9.2)"
              title="Optional target version (auto-detected if blank)"
              className="w-full bg-slate-900/90 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/70 focus:ring-1 focus:ring-blue-500/40 transition-all font-mono"
            />
          </div>

          {/* Build Log Toggle Button */}
          <button
            type="button"
            id="toggle-build-log-btn"
            onClick={() => setShowLogDrawer(!showLogDrawer)}
            title="Attach build / test failure traceback for grounded migration"
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-mono transition-all flex-shrink-0 ${
              log.trim()
                ? "bg-amber-500/15 border-amber-500/50 text-amber-300 shadow-sm shadow-amber-500/10"
                : showLogDrawer
                ? "bg-slate-800 border-slate-700 text-slate-200"
                : "bg-slate-900/90 border-slate-800 text-slate-400 hover:text-slate-200"
            }`}
          >
            <FileText className="w-3.5 h-3.5 text-amber-400" />
            <span className="hidden sm:inline">Build Log</span>
            {log.trim() ? (
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
            ) : showLogDrawer ? (
              <ChevronUp className="w-3 h-3 text-slate-400" />
            ) : (
              <ChevronDown className="w-3 h-3 text-slate-400" />
            )}
          </button>

          {/* Autonomous Detection Pill */}
          {detectedInfo && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 font-mono text-[11px] whitespace-nowrap shadow-sm shadow-emerald-950">
              <Sparkles className="w-3.5 h-3.5 text-emerald-400 animate-spin" style={{ animationDuration: "3s" }} />
              <span>
                Target: <strong className="text-white">{detectedInfo.library}</strong> ({detectedInfo.sourceVersion || "legacy"} → {detectedInfo.targetVersion})
              </span>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex items-center gap-2 flex-shrink-0">
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
                onClick={handleFixRepository}
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
      </div>

      {/* Expandable Build-Log Textarea Drawer */}
      {showLogDrawer && (
        <div className="w-full pt-2 border-t border-slate-800/80 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[11px] text-slate-400">
            <span className="flex items-center gap-1.5 font-medium text-slate-300">
              <FileText className="w-3.5 h-3.5 text-amber-400" />
              CI / Build / Test Error Traceback (Optional Grounding Input)
            </span>
            <div className="flex items-center gap-2">
              {log.trim() && (
                <button
                  type="button"
                  onClick={() => setLog("")}
                  className="text-[10px] text-slate-400 hover:text-rose-400 transition-colors"
                >
                  Clear Log
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowLogDrawer(false)}
                className="text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
              >
                Done
              </button>
            </div>
          </div>
          <textarea
            id="build-log-textarea"
            disabled={isRunning}
            rows={3}
            value={log}
            onChange={(e) => setLog(e.target.value)}
            placeholder="Paste CI build failure log, pytest traceback, or compiler stderr here. DriftZero will ground AST transformation rules and sandbox parity verification against these specific failures..."
            className="w-full bg-[#050811] border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono focus:outline-none focus:border-blue-500/70 focus:ring-1 focus:ring-blue-500/40 resize-y leading-relaxed"
          />
        </div>
      )}
    </header>
  );
}
