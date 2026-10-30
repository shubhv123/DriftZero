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
  Lock,
  Key,
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
  isAuthenticated?: boolean;
  onOpenAuthModal?: () => void;
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
  isAuthenticated = false,
  onOpenAuthModal,
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

  return (
    <header className="w-full bg-[#0a0a0a] border-b border-neutral-800 px-4 py-2.5 flex flex-col gap-2 z-20">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Brand & Breadcrumb */}
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold tracking-tight text-white">
              DriftZero
            </span>
            <span className="text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded bg-neutral-900 border border-neutral-700 text-neutral-300">
              Autonomous
            </span>
          </div>
          <span className="text-xs text-neutral-500 hidden md:inline">/</span>
          <span className="text-xs text-neutral-400 hidden md:inline">
            Workspace
          </span>
        </div>

        {/* Center: Controls Input Bar */}
        <div className="flex items-center flex-1 max-w-4xl gap-2 flex-wrap sm:flex-nowrap">
          {/* Repo URL Input */}
          <div className="relative flex-1 min-w-[240px]">
            <div className="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-neutral-500">
              <Github className="w-3.5 h-3.5" />
            </div>
            <input
              type="text"
              id="repo-url-input"
              disabled={isRunning}
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="Enter GitHub URL or local path (e.g. ./demo_target_repo)"
              className="w-full bg-neutral-900 border border-neutral-800 rounded-md pl-8 pr-3 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors font-mono"
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
              placeholder="Lib (opt)"
              title="Optional target library (auto-detected if blank)"
              className="w-full bg-neutral-900 border border-neutral-800 rounded-md px-2.5 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors font-mono"
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
              placeholder="Ver (opt)"
              title="Optional target version (auto-detected if blank)"
              className="w-full bg-neutral-900 border border-neutral-800 rounded-md px-2.5 py-1.5 text-xs text-neutral-100 placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors font-mono"
            />
          </div>

          {/* Build Log Toggle Button */}
          <button
            type="button"
            id="toggle-build-log-btn"
            onClick={() => setShowLogDrawer(!showLogDrawer)}
            title="Attach build / test failure traceback for grounded migration"
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border text-xs font-mono transition-colors flex-shrink-0 ${
              log.trim()
                ? "bg-neutral-800 border-neutral-600 text-white"
                : showLogDrawer
                ? "bg-neutral-800 border-neutral-700 text-white"
                : "bg-neutral-900 border-neutral-800 text-neutral-400 hover:text-white"
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Build Log</span>
            {log.trim() ? (
              <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
            ) : showLogDrawer ? (
              <ChevronUp className="w-3 h-3 text-neutral-400" />
            ) : (
              <ChevronDown className="w-3 h-3 text-neutral-400" />
            )}
          </button>

          {/* UI Auth Button */}
          {onOpenAuthModal && (
            <button
              type="button"
              id="ui-auth-btn"
              onClick={onOpenAuthModal}
              title={
                isAuthenticated
                  ? "Authenticated with DRIFTZERO_UI_TOKEN"
                  : "Sign in with DRIFTZERO_UI_TOKEN"
              }
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border text-xs font-mono transition-colors flex-shrink-0 ${
                isAuthenticated
                  ? "bg-neutral-900 border-neutral-700 text-neutral-300 hover:text-white"
                  : "bg-neutral-900 border-neutral-800 text-neutral-400 hover:text-white"
              }`}
            >
              {isAuthenticated ? (
                <Key className="w-3.5 h-3.5 text-neutral-300" />
              ) : (
                <Lock className="w-3.5 h-3.5 text-neutral-400" />
              )}
              <span className="hidden sm:inline">
                {isAuthenticated ? "Auth OK" : "Auth"}
              </span>
            </button>
          )}

          {/* Autonomous Detection Pill */}
          {detectedInfo && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-neutral-900 border border-neutral-700 text-neutral-200 font-mono text-[11px] whitespace-nowrap">
              <Sparkles className="w-3 h-3 text-neutral-300" />
              <span>
                <strong>{detectedInfo.library}</strong> ({detectedInfo.sourceVersion || "legacy"} → {detectedInfo.targetVersion})
              </span>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex items-center gap-2 flex-shrink-0">
            {isRunning ? (
              <button
                onClick={onStopMigration}
                id="stop-migration-btn"
                className="flex items-center gap-1.5 px-3 py-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 border border-neutral-700 rounded-md text-xs font-medium transition-colors"
              >
                <Square className="w-3 h-3 fill-current" />
                <span>Abort</span>
              </button>
            ) : (
              <button
                onClick={handleFixRepository}
                id="run-migration-btn"
                className="flex items-center gap-1.5 px-3.5 py-1.5 bg-white hover:bg-neutral-200 text-black font-semibold rounded-md text-xs shadow-sm transition-colors active:scale-95 whitespace-nowrap"
              >
                <Zap className="w-3.5 h-3.5 fill-current" />
                <span>Fix Repository</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Expandable Build-Log Textarea Drawer */}
      {showLogDrawer && (
        <div className="w-full pt-2 border-t border-neutral-800 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[11px] text-neutral-400">
            <span className="flex items-center gap-1.5 font-medium text-neutral-300">
              <FileText className="w-3.5 h-3.5 text-neutral-400" />
              CI / Build / Test Error Traceback (Optional Grounding Input)
            </span>
            <div className="flex items-center gap-2">
              {log.trim() && (
                <button
                  type="button"
                  onClick={() => setLog("")}
                  className="text-[10px] text-neutral-400 hover:text-white transition-colors"
                >
                  Clear Log
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowLogDrawer(false)}
                className="text-[10px] text-neutral-400 hover:text-neutral-200 transition-colors"
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
            className="w-full bg-[#050505] border border-neutral-800 rounded-md p-2.5 text-xs text-neutral-200 placeholder-neutral-600 font-mono focus:outline-none focus:border-neutral-600 resize-y leading-relaxed"
          />
        </div>
      )}
    </header>
  );
}
