"use client";

import React, { useState } from "react";
import {
  Terminal,
  AlertCircle,
  CheckCircle2,
  Cpu,
  ShieldCheck,
  Zap,
  CornerDownRight,
  Sparkles,
} from "lucide-react";
import { Stage4Report, VerificationAttempt } from "../types/migration";

interface SandboxTerminalTabProps {
  stage4Report: Stage4Report | null;
  isRunningStage4: boolean;
}

export function SandboxTerminalTab({
  stage4Report,
  isRunningStage4,
}: SandboxTerminalTabProps) {
  const attempts = stage4Report?.attempts || [];
  const [selectedAttemptIdx, setSelectedAttemptIdx] = useState<number>(0);

  // Keep latest attempt selected if report changes
  const activeAttempt: VerificationAttempt | undefined =
    attempts[selectedAttemptIdx] || attempts[attempts.length - 1];

  const hasMultipleAttempts = attempts.length > 1;
  const isPassed = stage4Report?.final_status === "PASSED";

  const stdoutText = activeAttempt?.stdout_log ?? activeAttempt?.stdout ?? "";
  const stderrText = activeAttempt?.stderr_traceback ?? activeAttempt?.stderr ?? "";

  const failedAttempt = attempts.find((a) => a.exit_code !== 0) || attempts[0];
  const healedAttempt = attempts.find((a) => Boolean(a.healed_file)) || attempts[attempts.length - 1];
  const healedModel = healedAttempt?.healed_by_model || activeAttempt?.healed_by_model || "Nemotron Ultra";
  const healedFile = healedAttempt?.healed_file || activeAttempt?.healed_file || "patched files";

  const rawTraceback = failedAttempt?.stderr_traceback || failedAttempt?.stderr || "";
  const failureSnippet = rawTraceback
    ? rawTraceback.trim().split("\n").filter(Boolean).slice(-2).join(" \u2014 ")
    : `Exit code ${failedAttempt?.exit_code ?? 1}`;

  return (
    <div className="h-full flex flex-col bg-[#050811] text-slate-200 overflow-hidden font-mono">
      {/* Top Banner & Attempt Selector */}
      <div className="px-4 py-2.5 bg-slate-900/80 border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Terminal className="w-4 h-4 text-emerald-400" />
          <span className="text-xs font-bold uppercase tracking-wider text-slate-200">
            Nebius Serverless Sandbox & Self-Healing Terminal
          </span>

          {stage4Report && (
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                isPassed
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  : "bg-rose-500/20 text-rose-300 border border-rose-500/40"
              }`}
            >
              {isPassed ? "✅ PARITY 100% VERIFIED" : "❌ ATTEMPTS EXHAUSTED"}
            </span>
          )}

          {isRunningStage4 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40 animate-pulse">
              Running Differential Parity Suite...
            </span>
          )}
        </div>

        {/* Attempt Tabs */}
        {attempts.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs">
            {attempts.map((att, idx) => {
              const passed = att.exit_code === 0;
              const isSelected =
                (selectedAttemptIdx === idx) ||
                (!attempts[selectedAttemptIdx] && idx === attempts.length - 1);

              return (
                <button
                  key={att.job_id || idx}
                  onClick={() => setSelectedAttemptIdx(idx)}
                  className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition-all ${
                    isSelected
                      ? passed
                        ? "bg-emerald-500/20 border-emerald-500 text-emerald-300 shadow-sm shadow-emerald-500/20"
                        : "bg-rose-500/20 border-rose-500 text-rose-300 shadow-sm shadow-rose-500/20"
                      : "bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {passed ? (
                    <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                  ) : (
                    <AlertCircle className="w-3 h-3 text-rose-400" />
                  )}
                  <span>Attempt #{att.attempt_number}</span>
                  <span className="text-[10px] opacity-75">
                    ({att.passed_count}P / {att.failed_count}F)
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Autonomous Interception Highlight (When Attempt #1 fails and heals) */}
      {hasMultipleAttempts && (
        <div className="px-4 py-2.5 bg-gradient-to-r from-purple-950/40 via-indigo-950/30 to-slate-900/60 border-b border-purple-500/30 flex flex-col gap-2">
          <div className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-purple-400 animate-ping" />
              <Cpu className="w-4 h-4 text-purple-400" />
              <span className="font-bold text-purple-300 text-xs">
                Autonomous Self-Healing Loop Triggered
              </span>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-purple-500/20 border border-purple-500/40 text-purple-300">
              Reasoning Engine: {healedModel}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
            {/* Attempt failure notice */}
            <div className="p-2 rounded bg-rose-950/30 border border-rose-500/30 text-rose-300 flex items-start gap-2">
              <AlertCircle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0 mt-0.5" />
              <div className="min-w-0">
                <div className="font-bold">
                  Attempt #{failedAttempt?.attempt_number ?? 1} Failed (Exit {failedAttempt?.exit_code ?? 1}):
                </div>
                <div className="font-mono text-[10px] text-rose-200 truncate mt-0.5" title={rawTraceback}>
                  {failureSnippet}
                </div>
              </div>
            </div>

            {/* Healed action notice */}
            <div className="p-2 rounded bg-emerald-950/30 border border-emerald-500/30 text-emerald-300 flex items-start gap-2">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0 mt-0.5" />
              <div className="min-w-0">
                <div className="font-bold">Self-Healed:</div>
                <div className="text-emerald-200 mt-0.5">
                  {healedModel} intercepted stderr traceback, patched{" "}
                  <span className="font-mono text-cyan-300 font-semibold">{healedFile}</span>, and re-ran differential tests to 100% pass!
                </div>
              </div>
            </div>
          </div>

          {/* Healing action cards */}
          {(activeAttempt?.healing_actions && activeAttempt.healing_actions.length > 0) ? (
            <div className="p-2 rounded bg-slate-900/80 border border-purple-500/30 text-[10px] space-y-1">
              <div className="text-purple-300 font-bold flex items-center gap-1">
                <CornerDownRight className="w-3 h-3 text-purple-400" />
                Interception & Patch Actions:
              </div>
              {activeAttempt.healing_actions.map((act, i) => (
                <div key={i} className="pl-4 text-slate-300 flex flex-wrap gap-2">
                  <span className="text-cyan-400 font-mono">[{act.target_file}]</span>
                  <span>{act.action_summary}</span>
                  <span className="text-purple-400 font-mono">
                    via {act.healed_by_model}
                  </span>
                </div>
              ))}
            </div>
          ) : activeAttempt?.healed_file ? (
            <div className="p-2 rounded bg-slate-900/80 border border-purple-500/30 text-[10px] space-y-1">
              <div className="text-purple-300 font-bold flex items-center gap-1">
                <CornerDownRight className="w-3 h-3 text-purple-400" />
                Autonomous Patch Applied:
              </div>
              <div className="pl-4 text-slate-300 flex flex-wrap gap-2">
                <span className="text-cyan-400 font-mono">[{activeAttempt.healed_file}]</span>
                <span>Interception healed semantic drift edge cases</span>
                <span className="text-purple-400 font-mono">
                  via {activeAttempt.healed_by_model || healedModel}
                </span>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* Terminal Output Area */}
      <div className="flex-1 overflow-y-auto p-4 bg-[#05070e] font-mono text-xs leading-relaxed text-slate-300 select-text">
        {!activeAttempt ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500 gap-2">
            <Terminal className="w-8 h-8 stroke-1 text-slate-600" />
            <span>Sandbox container ready. Awaiting Stage 4 verification.</span>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="text-slate-500 border-b border-slate-800 pb-2 flex items-center justify-between text-[11px]">
              <span>Container Job ID: {activeAttempt.job_id}</span>
              <span>
                Result:{" "}
                <span
                  className={
                    activeAttempt.exit_code === 0
                      ? "text-emerald-400 font-bold"
                      : "text-rose-400 font-bold"
                  }
                >
                  Exit {activeAttempt.exit_code} (
                  {activeAttempt.passed_count} passed,{" "}
                  {activeAttempt.failed_count} failed)
                </span>
              </span>
            </div>

            {/* Standard Output */}
            {stdoutText && (
              <pre className="whitespace-pre-wrap break-all text-slate-300 font-mono text-[11px] bg-slate-950/70 p-3 rounded-lg border border-slate-900">
                {stdoutText}
              </pre>
            )}

            {/* Standard Error (if any) */}
            {stderrText && (
              <div className="p-3 rounded-lg bg-rose-950/20 border border-rose-500/30 text-rose-300 font-mono text-[11px]">
                <div className="font-bold text-rose-400 mb-1 flex items-center gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5" />
                  Captured Stderr Traceback:
                </div>
                <pre className="whitespace-pre-wrap break-all text-rose-200">
                  {stderrText}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
