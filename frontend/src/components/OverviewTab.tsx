"use client";

import React from "react";
import {
  FileCode,
  Sparkles,
  GitBranch,
  Shield,
  Cpu,
  Layers,
  ArrowRight,
} from "lucide-react";

interface OverviewTabProps {
  repoUrl: string;
  detectedInfo?: {
    library: string;
    targetVersion: string;
    sourceVersion?: string | null;
  } | null;
  isRunning: boolean;
  isCompleted: boolean;
  onSelectFile?: (path: string) => void;
}

export function OverviewTab({
  repoUrl,
  detectedInfo,
  isRunning,
  isCompleted,
  onSelectFile,
}: OverviewTabProps) {
  return (
    <div className="h-full flex flex-col bg-black text-neutral-200 overflow-y-auto p-6 select-none font-sans">
      <div className="max-w-3xl mx-auto w-full space-y-6">
        {/* Notion Page Icon & Title */}
        <div className="space-y-2">
          <div className="text-3xl">⚡</div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            DriftZero Workspace
          </h1>
          <p className="text-sm text-neutral-400">
            Autonomous breaking-change migration engine with zero-token AST routing and differential parity verification.
          </p>
        </div>

        {/* Notion Properties Table */}
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 divide-y divide-neutral-800 text-xs">
          <div className="flex items-center px-3.5 py-2">
            <span className="w-40 text-neutral-500 flex items-center gap-1.5">
              <GitBranch className="w-3.5 h-3.5" /> Repository
            </span>
            <span className="font-mono text-neutral-200 truncate">
              {repoUrl || "./demo_target_repo"}
            </span>
          </div>

          <div className="flex items-center px-3.5 py-2">
            <span className="w-40 text-neutral-500 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5" /> Target Library
            </span>
            <span className="font-mono text-neutral-200">
              {detectedInfo ? detectedInfo.library : "Auto-detected (e.g. pydantic)"}
            </span>
          </div>

          <div className="flex items-center px-3.5 py-2">
            <span className="w-40 text-neutral-500 flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5" /> Target Version
            </span>
            <span className="font-mono text-neutral-200">
              {detectedInfo ? detectedInfo.targetVersion : "Auto-detected (e.g. 2.9.2)"}
            </span>
          </div>

          <div className="flex items-center px-3.5 py-2">
            <span className="w-40 text-neutral-500 flex items-center gap-1.5">
              <Shield className="w-3.5 h-3.5" /> Sandbox Engine
            </span>
            <span className="text-neutral-200">
              Nebius Serverless Sandbox · Differential Parity Suite
            </span>
          </div>

          <div className="flex items-center px-3.5 py-2">
            <span className="w-40 text-neutral-500 flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5" /> Model Tiering
            </span>
            <span className="text-neutral-200">
              NVIDIA Nemotron Super (Scores ≤ 5) / Ultra (Scores &gt; 5)
            </span>
          </div>

          <div className="flex items-center px-3.5 py-2">
            <span className="w-40 text-neutral-500 flex items-center gap-1.5">
              <FileCode className="w-3.5 h-3.5" /> Status
            </span>
            <span className="font-mono text-neutral-200 flex items-center gap-1.5">
              <span
                className={`w-2 h-2 rounded-full ${
                  isRunning
                    ? "bg-white animate-pulse"
                    : isCompleted
                    ? "bg-white"
                    : "bg-neutral-500"
                }`}
              />
              {isRunning
                ? "Migration in Progress"
                : isCompleted
                ? "Parity Verified 100%"
                : "Ready / Idle"}
            </span>
          </div>
        </div>

        {/* Notion Callout Box */}
        <div className="p-3.5 rounded-lg bg-neutral-900/50 border border-neutral-800 text-xs text-neutral-300 leading-relaxed space-y-1">
          <div className="font-semibold text-white">How to use:</div>
          <div>
            1. Select any file from the <strong>AST Blast-Radius tree</strong> on the left to inspect side-by-side code diffs.
          </div>
          <div>
            2. Click <strong>Fix Repository</strong> in the top header to run the full autonomous pipeline across all 5 stages.
          </div>
          <div>
            3. View live execution logs in the <strong>Sandbox</strong> tab, and download context rules in the <strong>Rules</strong> tab.
          </div>
        </div>

        {/* Autonomous Pipeline Overview */}
        <div className="space-y-3 pt-2">
          <h2 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider">
            Pipeline Architecture
          </h2>
          <div className="space-y-2">
            <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-900/20 text-xs space-y-1">
              <div className="font-semibold text-white flex items-center gap-2">
                <span>Stage 1: AST Blast-Radius Analysis</span>
              </div>
              <p className="text-neutral-400 leading-normal">
                Inspects inheritance hierarchies (MRO), calls, and symbols in under 15ms. Computes normalized AST complexity scores (1-10).
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-900/20 text-xs space-y-1">
              <div className="font-semibold text-white flex items-center gap-2">
                <span>Stage 2: Tavily Live Grounding Delta</span>
              </div>
              <p className="text-neutral-400 leading-normal">
                Searches official migration documentation, changelogs, and GitHub releases. Synthesizes before/after syntax rules and silent drift gotchas.
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-900/20 text-xs space-y-1">
              <div className="font-semibold text-white flex items-center gap-2">
                <span>Stage 3: Token Factory Smart Router</span>
              </div>
              <p className="text-neutral-400 leading-normal">
                Routes syntactic refactoring to Nemotron Super ($0.13/M) and complex reasoning to Nemotron Ultra ($0.60/M). Cuts token spend by ~50%.
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-900/20 text-xs space-y-1">
              <div className="font-semibold text-white flex items-center gap-2">
                <span>Stage 4: Nebius Sandbox & Autonomous Self-Healing</span>
              </div>
              <p className="text-neutral-400 leading-normal">
                Executes differential parity test suite under golden and patched versions. Intercepts failures and self-heals until 100% parity is verified.
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-900/20 text-xs space-y-1">
              <div className="font-semibold text-white flex items-center gap-2">
                <span>Stage 5: Context Rules Export</span>
              </div>
              <p className="text-neutral-400 leading-normal">
                Generates .cursorrules, CLAUDE.md, and downloadable zip bundles to permanently prevent future breaking-change regressions.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
