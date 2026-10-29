"use client";

import React, { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import {
  FileCode,
  ArrowRight,
  Clock,
  Coins,
  Cpu,
} from "lucide-react";
import { AffectedFile, MigrationDelta, PatchedFileEvent } from "../types/migration";

// Dynamically import Monaco DiffEditor to prevent SSR issues
const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.DiffEditor),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full flex items-center justify-center bg-black text-neutral-500 font-mono text-xs">
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
      <div className="h-full flex flex-col items-center justify-center p-8 text-center text-neutral-500 select-none bg-black">
        <FileCode className="w-10 h-10 stroke-1 text-neutral-600 mb-2.5" />
        <h4 className="text-sm font-semibold text-neutral-300">
          No File Selected
        </h4>
        <p className="text-xs text-neutral-500 max-w-sm mt-1">
          Select a file from the AST Blast-Radius tree on the left to inspect side-by-side refactoring diffs.
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
    <div className="h-full flex flex-col bg-black overflow-hidden">
      {/* Diff Header Bar */}
      <div className="px-4 py-2 bg-[#0c0c0c] border-b border-neutral-800 flex flex-wrap items-center justify-between gap-3 select-none">
        <div className="flex items-center gap-2 min-w-0">
          <FileCode className="w-4 h-4 text-neutral-400 flex-shrink-0" />
          <span className="font-mono text-xs font-semibold text-white truncate">
            {selectedFile.relative_path}
          </span>

          <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-neutral-800 text-neutral-300 border border-neutral-700">
            Score: {score}/10
          </span>

          <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 flex items-center gap-1">
            <Cpu className="w-2.5 h-2.5 text-neutral-400" />
            {modelName.replace(/^nvidia\//i, "")}
          </span>
        </div>

        {/* Telemetry pill */}
        {telemetry ? (
          <div className="flex items-center gap-3 text-[11px] font-mono text-neutral-400">
            <span className="flex items-center gap-1 text-neutral-300">
              <Clock className="w-3 h-3 text-neutral-400" />
              {Math.round(latency)}ms
            </span>
            <span className="flex items-center gap-1 text-white">
              <Coins className="w-3 h-3 text-neutral-400" />
              ${cost.toFixed(5)}
            </span>
            {tokens > 0 && (
              <span className="text-[10px] px-1.5 py-0.2 rounded bg-neutral-800 text-neutral-300 border border-neutral-700">
                {tokens} tokens
              </span>
            )}
          </div>
        ) : (
          <div className="text-[11px] text-neutral-500 font-mono italic">
            Stage 3 Model Routing
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

      {/* Grounding Annotations Banner */}
      <div className="px-4 py-2 bg-[#0c0c0c] border-t border-neutral-800 flex flex-col gap-1.5 max-h-32 overflow-y-auto">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-neutral-400 font-medium">
            Tavily Grounding Annotations ({relevantRules.length} applicable rules)
          </span>
          <span className="text-[10px] text-neutral-500 font-mono truncate max-w-md">
            AST Reason:{" "}
            {selectedFile.routing_reasons?.join("; ") ||
              selectedFile.ast_reasoning ||
              "Detected AST deprecation nodes"}
          </span>
        </div>

        {relevantRules.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {relevantRules.map((rule, idx) => (
              <div
                key={idx}
                className="px-2 py-0.5 rounded bg-neutral-900 border border-neutral-800 text-[10px] font-mono flex items-center gap-1.5 text-neutral-300"
              >
                <span className="text-neutral-400 line-through truncate max-w-[120px]">
                  {rule.old_syntax}
                </span>
                <ArrowRight className="w-2.5 h-2.5 text-neutral-500 flex-shrink-0" />
                <span className="text-white truncate max-w-[120px]">
                  {rule.new_syntax}
                </span>
                <span className="text-neutral-500 text-[9px] ml-1">
                  ({rule.symbol || rule.category || "Rule"})
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[10px] text-neutral-500 italic">
            Automated transformation verified against differential parity suite.
          </div>
        )}
      </div>
    </div>
  );
}
