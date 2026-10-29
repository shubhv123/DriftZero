"use client";

import React from "react";
import {
  TrendingDown,
  Cpu,
  Coins,
  ShieldCheck,
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
    <div className="w-full bg-[#0a0a0a] border-b border-neutral-800 px-4 py-2 select-none">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
        {/* Card 1: Tier Split Progress */}
        <div className="bg-neutral-900/40 border border-neutral-800 rounded-md px-3 py-2 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-neutral-400 font-medium flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5 text-neutral-400" />
              Tier Split (AST Routing)
            </span>
            <span className="font-mono text-[11px] text-neutral-300">
              {totalCalls > 0 ? `${superCalls}S / ${ultraCalls}U` : "Ready"}
            </span>
          </div>

          <div className="flex items-center gap-2 mb-1">
            <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden flex">
              <div
                style={{ width: `${totalCalls > 0 ? superPct : 50}%` }}
                className="bg-neutral-300 h-full transition-all duration-300"
                title={`${superLabel}: ${superPct}%`}
              />
              <div
                style={{ width: `${totalCalls > 0 ? ultraPct : 50}%` }}
                className="bg-neutral-600 h-full transition-all duration-300"
                title={`${ultraLabel}: ${ultraPct}%`}
              />
            </div>
          </div>

          <div className="flex items-center justify-between text-[10px] font-mono text-neutral-400">
            <span className="flex items-center gap-1 text-neutral-300">
              <span className="w-1.5 h-1.5 rounded-full bg-neutral-300 inline-block" />
              {superLabel}: {totalCalls > 0 ? `${superPct}%` : "--"}
            </span>
            <span className="flex items-center gap-1 text-neutral-400">
              <span className="w-1.5 h-1.5 rounded-full bg-neutral-500 inline-block" />
              {ultraLabel}: {totalCalls > 0 ? `${ultraPct}%` : "--"}
            </span>
          </div>
        </div>

        {/* Card 2: Token Economics Counter */}
        <div className="bg-neutral-900/40 border border-neutral-800 rounded-md px-3 py-2 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-neutral-400 font-medium flex items-center gap-1.5">
              <Coins className="w-3.5 h-3.5 text-neutral-400" />
              Token Economics
            </span>
            <span className="text-[10px] text-neutral-500 font-mono">
              $0.13 vs $0.60/M
            </span>
          </div>

          <div className="flex items-baseline gap-2">
            <span className="text-sm font-bold font-mono text-white">
              ${actualCostUsd > 0 ? actualCostUsd.toFixed(4) : "0.0000"}
            </span>
            <span className="text-[11px] text-neutral-400">actual spend</span>
            <span className="text-[11px] text-neutral-500 line-through font-mono ml-auto">
              ${hypotheticalCostUsd > 0 ? hypotheticalCostUsd.toFixed(4) : "0.0000"}
            </span>
          </div>

          <div className="text-[10px] text-neutral-500 flex items-center justify-between pt-0.5">
            <span>Verified Nebius Rates</span>
            <span>All-Ultra Baseline</span>
          </div>
        </div>

        {/* Card 3: Total Cost Savings Badge */}
        <div className="bg-neutral-900/40 border border-neutral-800 rounded-md px-3 py-2 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-neutral-400 font-medium flex items-center gap-1.5">
              <TrendingDown className="w-3.5 h-3.5 text-neutral-400" />
              Token Savings
            </span>
            <span className="text-[10px] text-neutral-400 font-mono uppercase">
              Optimized
            </span>
          </div>

          <div className="flex items-center gap-2">
            <div className="px-2 py-0.5 rounded bg-neutral-800 border border-neutral-700 text-neutral-200 font-mono text-xs font-bold flex items-center gap-1">
              <Percent className="w-3 h-3 text-neutral-400" />
              <span>
                {savingsPercentage > 0 ? `${savingsPercentage.toFixed(1)}%` : "0.0%"} Saved
              </span>
            </div>
            <span className="text-[11px] text-neutral-400 leading-tight">
              via AST Routing
            </span>
          </div>

          <div className="text-[10px] text-neutral-500">
            {totalCalls > 0
              ? `Saved $${(hypotheticalCostUsd - actualCostUsd).toFixed(4)} vs All-Ultra`
              : `Scores <= 5 route to ${superLabel}`}
          </div>
        </div>

        {/* Card 4: Execution Mode Badge */}
        <div className="bg-neutral-900/40 border border-neutral-800 rounded-md px-3 py-2 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-neutral-400 font-medium flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-neutral-400" />
              Inference Engine
            </span>
            <span className="text-[10px] text-neutral-500 font-mono">
              {executionMode === "LIBCST_DETERMINISTIC_CODEMOD" || executionMode === "DETERMINISTIC_FALLBACK"
                ? "Deterministic"
                : "Nemotron-3"}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-medium text-white px-2 py-0.5 rounded bg-neutral-800 border border-neutral-700">
              {executionMode === "LIVE_NEBIUS" || executionMode === "NEBIUS_TOKEN_FACTORY"
                ? "NEBIUS TOKEN FACTORY"
                : "TOKEN FACTORY READY"}
            </span>
          </div>

          <div className="text-[10px] text-neutral-500">
            High-Throughput Streaming · JSON Mode
          </div>
        </div>
      </div>
    </div>
  );
}
