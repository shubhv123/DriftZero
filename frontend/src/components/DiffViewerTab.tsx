"use client";

import React, { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import {
  FileCode,
  Sparkles,
  Cpu,
  Layers,
  ArrowRight,
  Info,
  Clock,
  Coins,
} from "lucide-react";
import { AffectedFile, MigrationDelta, PatchedFileEvent } from "../types/migration";

// Dynamically import Monaco DiffEditor to prevent SSR issues
const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.DiffEditor),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full flex items-center justify-center bg-[#070b13] text-slate-500 font-mono text-xs">
        Initializing Monaco Diff Engine...
      </div>
    ),
  }
);

interface DiffViewerTabProps {
  selectedFile: AffectedFile | null;
  patchedData: PatchedFileEvent | null;
  migrationDelta: MigrationDelta | null;
}

export function DiffViewerTab({
  selectedFile,
  patchedData,
  migrationDelta,
}: DiffViewerTabProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!selectedFile) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-8 text-center text-slate-500">
        <FileCode className="w-12 h-12 stroke-1 text-slate-600 mb-3" />
        <h4 className="text-sm font-semibold text-slate-400">
          No File Selected
        </h4>
        <p className="text-xs text-slate-500 max-w-sm mt-1">
          Select an affected file from the AST Blast-Radius tree on the left to
          inspect side-by-side refactoring diffs and reasoning annotations.
        </p>
      </div>
    );
  }

  const originalCode =
    patchedData?.original_code || selectedFile.source_code || "# Source code loading...";
  const modifiedCode = patchedData ? patchedData.patched_code : originalCode;

  const score = selectedFile.normalized_score ?? selectedFile.complexity_score ?? 1;
  const isSuper = (selectedFile.assigned_tier || "").toUpperCase().includes("SUPER");
  const modelName =
    patchedData?.telemetry?.model_used ||
    patchedData?.model_used ||
    selectedFile.target_model_id ||
    selectedFile.assigned_model ||
    (isSuper ? "Nemotron Super" : "Nemotron Ultra");

  const telemetry = patchedData?.telemetry;
  const latency = telemetry?.latency_ms || 0;
  const cost = telemetry?.actual_cost_usd || 0;
  const tokens =
    (telemetry?.prompt_tokens ?? telemetry?.input_tokens ?? 0) +
    (telemetry?.completion_tokens ?? telemetry?.output_tokens ?? 0);

  // Find relevant Tavily rules matching symbols in this file
  const relevantRules =
    migrationDelta?.rules?.filter((r) => {
      const symList = [
        ...(selectedFile.symbols_detected || []),
        ...(selectedFile.imports_found || []),
        ...(selectedFile.usages?.map((u) => u.symbol_name) || []),
      ];
      return symList.some(
        (sym) =>
          (r.symbol && sym.toLowerCase().includes(r.symbol.toLowerCase())) ||
          (r.old_syntax && sym.toLowerCase().includes(r.old_syntax.toLowerCase()))
      );
    }) || [];

  return (
    <div className="h-full flex flex-col bg-[#070b14] overflow-hidden">
      {/* Diff Header Bar */}
      <div className="px-4 py-2.5 bg-slate-900/60 border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <FileCode className="w-4 h-4 text-blue-400 flex-shrink-0" />
          <span className="font-mono text-xs font-bold text-slate-200 truncate">
            {selectedFile.relative_path}
          </span>

          <span
            className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-bold ${
              score <= 5
                ? "bg-emerald-500/15 border border-emerald-500/30 text-emerald-300"
                : "bg-purple-500/15 border border-purple-500/30 text-purple-300"
            }`}
          >
            AST Complexity {score}/10
          </span>

          <span
            className={`text-[10px] px-2 py-0.5 rounded-md font-semibold flex items-center gap-1 ${
              isSuper
                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                : "bg-purple-500/20 text-purple-300 border border-purple-500/40"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isSuper ? "bg-emerald-400" : "bg-purple-400"
              }`}
            />
            {modelName}
          </span>
        </div>

        {/* Telemetry pill */}
        {telemetry ? (
          <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400">
            <span className="flex items-center gap-1 text-slate-300">
              <Clock className="w-3 h-3 text-cyan-400" />
              {Math.round(latency)}ms
            </span>
            <span className="flex items-center gap-1 text-emerald-400">
              <Coins className="w-3 h-3 text-emerald-400" />
              ${cost.toFixed(5)}
            </span>
            {tokens > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
                {tokens} tokens
              </span>
            )}
          </div>
        ) : (
          <div className="text-[11px] text-slate-500 font-mono italic">
            Awaiting Stage 3 Token Factory Patch...
          </div>
        )}
      </div>

      {/* Main Diff Editor Area */}
      <div className="flex-1 min-h-0 relative">
        {mounted && (
          <DiffEditor
            height="100%"
            language="python"
            original={originalCode}
            modified={modifiedCode}
            theme="vs-dark"
            options={{
              readOnly: true,
              renderSideBySide: true,
              fontSize: 12.5,
              lineNumbers: "on",
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              automaticLayout: true,
              renderOverviewRuler: false,
              padding: { top: 8, bottom: 8 },
            }}
          />
        )}
      </div>

      {/* Tavily Inline Grounding Annotations Banner */}
      <div className="px-4 py-2 bg-slate-950/90 border-t border-slate-800/80 flex flex-col gap-1.5 max-h-32 overflow-y-auto">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-slate-400 font-medium flex items-center gap-1.5">
            <Sparkles className="w-3 h-3 text-cyan-400" />
            Tavily Grounding Annotations ({relevantRules.length} applicable rules)
          </span>
          <span className="text-[10px] text-slate-500 font-mono truncate max-w-md">
            AST Reason:{" "}
            {selectedFile.routing_reasons?.join("; ") ||
              selectedFile.ast_reasoning ||
              "Detected imports & deprecation calls"}
          </span>
        </div>

        {relevantRules.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {relevantRules.map((rule, idx) => (
              <div
                key={idx}
                className="px-2.5 py-1 rounded-md bg-slate-900/80 border border-slate-800 text-[10px] font-mono flex items-center gap-1.5 text-slate-300"
              >
                <span className="text-rose-400 line-through truncate max-w-[120px]">
                  {rule.old_syntax}
                </span>
                <ArrowRight className="w-2.5 h-2.5 text-slate-500 flex-shrink-0" />
                <span className="text-emerald-400 truncate max-w-[120px]">
                  {rule.new_syntax}
                </span>
                <span className="text-slate-500 text-[9px] ml-1">
                  ({rule.symbol || rule.category || "Rule"})
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[10px] text-slate-500 italic">
            Refactored based on multi-endpoint Tavily documentation delta and syntax trees.
          </div>
        )}
      </div>
    </div>
  );
}
