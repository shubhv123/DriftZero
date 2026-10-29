"use client";

import React from "react";
import {
  TrendingDown,
  Cpu,
  Coins,
  ShieldCheck,
  Zap,
  Globe2,
  Percent,
} from "lucide-react";

interface TelemetryBarProps {
  superCalls: number;
  ultraCalls: number;
  actualCostUsd: number;
  hypotheticalCostUsd: number;
  savingsPercentage: number;
  executionMode: "NEBIUS_TOKEN_FACTORY" | "LIBCST_DETERMINISTIC_CODEMOD" | "LIVE_NEBIUS" | "DETERMINISTIC_FALLBACK" | "AWAITING" | string;
  superModelLabel?: string;
  ultraModelLabel?: string;
}

export function TelemetryBar({
  superCalls,
  ultraCalls,
  actualCostUsd,
  hypotheticalCostUsd,
  savingsPercentage,
  executionMode,
  superModelLabel,
  ultraModelLabel,
}: TelemetryBarProps) {
  const totalCalls = superCalls + ultraCalls;
  const superPct = totalCalls > 0 ? Math.round((superCalls / totalCalls) * 100) : 0;
  const ultraPct = totalCalls > 0 ? 100 - superPct : 0;
  const superLabel = superModelLabel || "Super";
  const ultraLabel = ultraModelLabel || "Ultra";

  return (
    <div className="w-full bg-[#080d19]/90 border-b border-slate-800/80 px-4 py-2.5">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Card 1: Tier Split Progress */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl px-3.5 py-2.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5 text-blue-400" />
              Tier Split (AST Routing)
            </span>
            <span className="font-mono text-[11px] text-slate-300">
              {totalCalls > 0 ? `${superCalls}S / ${ultraCalls}U` : "Ready"}
            </span>
          </div>

          <div className="flex items-center gap-2 mb-1">
            <div className="flex-1 h-2 bg-slate-800 rounded-full overflow-hidden flex">
              <div
                style={{ width: `${totalCalls > 0 ? superPct : 50}%` }}
                className="bg-emerald-500 h-full transition-all duration-500"
                title={`${superLabel}: ${superPct}%`}
              />
              <div
                style={{ width: `${totalCalls > 0 ? ultraPct : 50}%` }}
                className="bg-purple-500 h-full transition-all duration-500"
                title={`${ultraLabel}: ${ultraPct}%`}
              />
            </div>
          </div>

          <div className="flex items-center justify-between text-[10px] font-mono text-slate-400">
            <span className="flex items-center gap-1 text-emerald-400 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
              {superLabel}: {totalCalls > 0 ? `${superPct}%` : "--"}
            </span>
            <span className="flex items-center gap-1 text-purple-400 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400 inline-block" />
              {ultraLabel}: {totalCalls > 0 ? `${ultraPct}%` : "--"}
            </span>
          </div>
        </div>

        {/* Card 2: Token Economics Counter */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl px-3.5 py-2.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <Coins className="w-3.5 h-3.5 text-amber-400" />
              Token Economics
            </span>
            <span className="text-[10px] text-slate-500 font-mono">
              $0.13 vs $0.60/M
            </span>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="text-sm font-bold font-mono text-emerald-400">
              ${actualCostUsd > 0 ? actualCostUsd.toFixed(4) : "0.0000"}
            </span>
            <span className="text-[11px] text-slate-400">actual spend</span>
            <span className="text-[11px] text-slate-500 line-through font-mono ml-auto">
              ${hypotheticalCostUsd > 0 ? hypotheticalCostUsd.toFixed(4) : "0.0000"}
            </span>
          </div>

          <div className="text-[10px] text-slate-400 flex items-center justify-between pt-0.5">
            <span>Verified Nebius Rates</span>
            <span className="text-slate-500">Hypothetical All-Ultra</span>
          </div>
        </div>

        {/* Card 3: Total Cost Savings Badge */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl px-3.5 py-2.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <TrendingDown className="w-3.5 h-3.5 text-emerald-400" />
              Total Token Savings
            </span>
            <span className="text-[10px] text-emerald-400 font-semibold uppercase">
              Optimized
            </span>
          </div>

          <div className="flex items-center gap-2">
            <div className="px-2.5 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 font-mono text-xs font-bold flex items-center gap-1.5 shadow-sm shadow-emerald-500/10">
              <Percent className="w-3 h-3 text-emerald-400" />
              <span>
                {savingsPercentage > 0 ? `${savingsPercentage.toFixed(1)}%` : "0.0%"} Saved
              </span>
            </div>
            <span className="text-[11px] text-slate-400 leading-tight">
              via AST Complexity Routing
            </span>
          </div>

          <div className="text-[10px] text-slate-500">
            {totalCalls > 0
              ? `Saved $${(hypotheticalCostUsd - actualCostUsd).toFixed(4)} vs All-Ultra`
              : `Scores <= 5 route to ${superLabel}`}
          </div>
        </div>

        {/* Card 4: Execution Mode Badge */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-xl px-3.5 py-2.5 flex flex-col justify-between shadow-sm">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
              Inference Engine
            </span>
            <span className="text-[10px] text-slate-500 font-mono">
              {executionMode === "LIBCST_DETERMINISTIC_CODEMOD" || executionMode === "DETERMINISTIC_FALLBACK"
                ? "Deterministic"
                : "Nemotron-3"}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {executionMode === "NEBIUS_TOKEN_FACTORY" || executionMode === "LIVE_NEBIUS" ? (
              <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 text-xs font-semibold shadow-sm shadow-emerald-500/20">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping inline-block" />
                <Globe2 className="w-3.5 h-3.5 text-emerald-400" />
                <span>🌐 NEBIUS TOKEN FACTORY</span>
              </div>
            ) : executionMode === "LIBCST_DETERMINISTIC_CODEMOD" || executionMode === "DETERMINISTIC_FALLBACK" ? (
              <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-300 text-xs font-semibold shadow-sm shadow-amber-500/20">
                <Zap className="w-3.5 h-3.5 text-amber-400" />
                <span>⚡ LIBCST DETERMINISTIC CODEMOD</span>
              </div>
            ) : (
              <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-blue-500/15 border border-blue-500/40 text-blue-300 text-xs font-semibold">
                <Globe2 className="w-3.5 h-3.5 text-blue-400" />
                <span>🌐 NEBIUS TOKEN FACTORY READY</span>
              </div>
            )}
          </div>

          <div className="text-[10px] text-slate-500 flex items-center justify-between">
            <span>High-Throughput Streaming</span>
            <span>
              {executionMode === "LIBCST_DETERMINISTIC_CODEMOD"
                ? "Zero Tokens Used"
                : "JSON Mode Active"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
