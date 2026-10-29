"use client";

import React, { useState } from "react";
import {
  Terminal,
  AlertCircle,
  Check,
  Cpu,
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

  const isPassed = stage4Report?.final_status === "PASSED";
  const stdoutText = activeAttempt?.stdout_log ?? activeAttempt?.stdout ?? "";
  const stderrText = activeAttempt?.stderr_traceback ?? activeAttempt?.stderr ?? "";

  const finalAttempt = attempts[attempts.length - 1];
  const finalAttemptPassed = finalAttempt ? finalAttempt.exit_code === 0 : stage4Report?.final_status === "PASSED";
  const healedAttempt = attempts.find((a) => Boolean(a.healed_file));
  const showSelfHealBanner = Boolean(healedAttempt && healedAttempt.healed_file && finalAttemptPassed);

  const failedAttempt = attempts.find((a) => a.exit_code !== 0) || attempts[0];
  const healedModel = healedAttempt?.healed_by_model || activeAttempt?.healed_by_model || "Nemotron Ultra";
  const healedFile = healedAttempt?.healed_file || activeAttempt?.healed_file || "patched files";

  const rawTraceback = failedAttempt?.stderr_traceback || failedAttempt?.stderr || "";
  const failureSnippet = rawTraceback
    ? rawTraceback.trim().split("\n").filter(Boolean).slice(-2).join(" \u2014 ")
    : `Exit code ${failedAttempt?.exit_code ?? 1}`;

  return (
    <div className="h-full flex flex-col bg-black text-neutral-200 overflow-hidden font-mono select-none">
      {/* Top Banner & Attempt Selector */}
      <div className="px-4 py-2 bg-[#0c0c0c] border-b border-neutral-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-neutral-400" />
          <span className="text-xs font-semibold uppercase tracking-wider text-neutral-200">
            Nebius Sandbox & Verification Terminal
          </span>

          {stage4Report && (
            <span
              className={`text-[10px] font-mono font-medium px-2 py-0.5 rounded ${
                isPassed
                  ? "bg-neutral-800 text-white border border-neutral-700"
                  : "bg-neutral-900 text-neutral-400 border border-neutral-800"
              }`}
            >
              {isPassed ? "PARITY VERIFIED (100%)" : "ATTEMPTS EXHAUSTED"}
            </span>
          )}

          {isRunningStage4 && (
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-neutral-800 text-neutral-300 animate-pulse">
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
                  className={`px-2 py-0.5 rounded border text-[11px] font-mono flex items-center gap-1.5 transition-colors ${
                    isSelected
                      ? "bg-neutral-800 border-neutral-600 text-white font-medium"
                      : "bg-neutral-950 border-neutral-800 text-neutral-400 hover:text-white"
                  }`}
                >
                  {passed ? (
                    <Check className="w-3 h-3 text-white" />
                  ) : (
                    <AlertCircle className="w-3 h-3 text-neutral-400" />
                  )}
                  <span>Attempt #{att.attempt_number}</span>
                  <span className="text-[10px] text-neutral-500">
                    ({att.passed_count}P / {att.failed_count}F)
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Autonomous Interception Highlight */}
      {showSelfHealBanner && (
        <div className="px-4 py-2 bg-neutral-900/60 border-b border-neutral-800 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <Cpu className="w-3.5 h-3.5 text-neutral-300" />
              <span className="font-semibold text-neutral-200 text-xs">
                Autonomous Self-Healing Loop Triggered
              </span>
            </div>
            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-neutral-800 border border-neutral-700 text-neutral-300">
              Model: {healedModel}
            </span>
          </div>

          <div className="text-[11px] text-neutral-400 flex items-center gap-2">
            <span>Diagnosed traceback & re-patched:</span>
            <span className="text-white font-mono font-medium underline">
              {healedFile}
            </span>
            <span className="text-neutral-500 text-[10px]">
              (Exit code: 1 → 0)
            </span>
          </div>

          <div className="text-[10px] text-neutral-500 font-mono truncate bg-black/60 px-2 py-1 rounded border border-neutral-800/60">
            Fix: {failureSnippet}
          </div>
        </div>
      )}

      {/* Terminal Output Body */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3 font-mono text-xs select-text bg-black">
        {!stage4Report && !isRunningStage4 ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-neutral-500 select-none">
            <Terminal className="w-10 h-10 stroke-1 text-neutral-600 mb-2.5" />
            <h4 className="text-sm font-semibold text-neutral-300">
              Nebius Sandbox Ready
            </h4>
            <p className="text-xs text-neutral-500 max-w-sm mt-1">
              Stage 4 differential parity verification executes in an isolated Nebius sandbox container, capturing stdout, pytest logs, and compiler tracebacks.
            </p>
          </div>
        ) : (
          <>
            {/* Command Header */}
            <div className="text-neutral-500 text-xs">
              <span className="text-neutral-400">$</span> pytest test_migration_parity.py -v --tb=short
            </div>

            {/* Stdout Stream */}
            {stdoutText && (
              <pre className="text-neutral-300 whitespace-pre-wrap leading-relaxed">
                {stdoutText}
              </pre>
            )}

            {/* Stderr Traceback */}
            {stderrText && (
              <pre className="text-neutral-400 whitespace-pre-wrap leading-relaxed border-l-2 border-neutral-700 pl-3 py-1 bg-neutral-950">
                {stderrText}
              </pre>
            )}

            {/* Parity Status Summary */}
            {activeAttempt && (
              <div className="mt-4 pt-3 border-t border-neutral-800 flex items-center justify-between text-xs text-neutral-400">
                <div className="flex items-center gap-3">
                  <span>
                    Exit Code:{" "}
                    <strong className={activeAttempt.exit_code === 0 ? "text-white" : "text-neutral-300"}>
                      {activeAttempt.exit_code}
                    </strong>
                  </span>
                  <span>Duration: {Math.round(activeAttempt.duration_ms)}ms</span>
                  <span>Passed: {activeAttempt.passed_count}</span>
                  <span>Failed: {activeAttempt.failed_count}</span>
                </div>
                <div className="text-neutral-500 text-[11px]">
                  Job: {activeAttempt.job_id}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
