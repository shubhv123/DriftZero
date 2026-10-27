"use client";

import React, { useState } from "react";
import {
  FolderTree,
  FileCode,
  Sparkles,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Layers,
  Search,
} from "lucide-react";
import {
  AffectedFile,
  BlastRadiusReport,
  MigrationDelta,
  PatchedFileEvent,
} from "../types/migration";

interface BlastRadiusTreeProps {
  blastReport: BlastRadiusReport | null;
  migrationDelta: MigrationDelta | null;
  patchedFiles: Map<string, PatchedFileEvent>;
  selectedFilePath: string | null;
  onSelectFile: (filePath: string) => void;
}

export function BlastRadiusTree({
  blastReport,
  migrationDelta,
  patchedFiles,
  selectedFilePath,
  onSelectFile,
}: BlastRadiusTreeProps) {
  const [tavilyOpen, setTavilyOpen] = useState(true);
  const [activeTavilyTab, setActiveTavilyTab] = useState<"rules" | "citations" | "drift">("rules");
  const [searchFilter, setSearchFilter] = useState("");

  const affectedFiles = blastReport?.affected_files || [];
  const filteredFiles = affectedFiles.filter((f) =>
    f.relative_path.toLowerCase().includes(searchFilter.toLowerCase())
  );

  // Derive silent drift warnings if explicit list or from breaking gotchas
  const driftWarnings: string[] =
    migrationDelta?.silent_drift_warnings && migrationDelta.silent_drift_warnings.length > 0
      ? migrationDelta.silent_drift_warnings
      : migrationDelta?.rules
          ?.filter((r) => r.breaking_gotcha && r.breaking_gotcha.length > 0)
          .map((r) => `${r.symbol || "Semantic Edge"}: ${r.breaking_gotcha}`) || [];

  return (
    <div className="w-full h-full flex flex-col bg-[#080d17] border-r border-slate-800/80 overflow-hidden select-none">
      {/* Pane Top Header */}
      <div className="p-3.5 border-b border-slate-800/80 bg-slate-900/40">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <FolderTree className="w-4 h-4 text-blue-400" />
            <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider">
              AST Blast-Radius Tree
            </h3>
          </div>
          {blastReport && (
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-500/10 border border-blue-500/30 text-blue-300">
              {blastReport.affected_files_count} / {blastReport.total_files_scanned} files ({Math.round(blastReport.scan_duration_ms)}ms)
            </span>
          )}
        </div>

        {/* Filter Input */}
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Filter affected files..."
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            className="w-full bg-slate-950/60 border border-slate-800 rounded-lg pl-8 pr-2.5 py-1 text-xs text-slate-300 placeholder-slate-500 focus:outline-none focus:border-blue-500/60 transition-colors font-mono"
          />
        </div>
      </div>

      {/* File List Section */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {filteredFiles.length === 0 ? (
          <div className="p-6 text-center text-slate-500 text-xs flex flex-col items-center justify-center gap-2 h-full">
            <Layers className="w-8 h-8 text-slate-600/60 stroke-1" />
            <span>No files detected yet. Run pipeline or click Instant Judge Demo.</span>
          </div>
        ) : (
          filteredFiles.map((file) => {
            const isSelected = selectedFilePath === file.relative_path;
            const patched = patchedFiles.get(file.relative_path);
            const score = file.normalized_score ?? file.complexity_score ?? 1;
            const isSuper = (file.assigned_tier || "").toUpperCase().includes("SUPER");
            const symbolsCount =
              file.usages?.length ??
              file.symbols_detected?.length ??
              file.imports_found?.length ??
              0;

            return (
              <div
                key={file.relative_path}
                onClick={() => onSelectFile(file.relative_path)}
                className={`group px-3 py-2 rounded-xl cursor-pointer border transition-all text-xs ${
                  isSelected
                    ? "bg-blue-600/15 border-blue-500/60 shadow-sm shadow-blue-500/20 text-white"
                    : "bg-slate-900/30 border-slate-800/50 hover:bg-slate-800/50 hover:border-slate-700/80 text-slate-300"
                }`}
              >
                <div className="flex items-center justify-between gap-1.5 mb-1">
                  <div className="flex items-center gap-2 min-w-0">
                    <FileCode
                      className={`w-3.5 h-3.5 flex-shrink-0 ${
                        isSelected ? "text-blue-400" : "text-slate-400"
                      }`}
                    />
                    <span className="font-mono text-xs truncate font-medium">
                      {file.relative_path}
                    </span>
                  </div>

                  {patched ? (
                    <span
                      title="Patched successfully"
                      className="flex-shrink-0 flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-500/10 px-1.5 py-0.2 rounded"
                    >
                      <CheckCircle2 className="w-3 h-3" />
                      <span>{Math.round(patched.telemetry?.latency_ms || 0)}ms</span>
                    </span>
                  ) : null}
                </div>

                {/* Score & Model Tier Badges */}
                <div className="flex items-center justify-between text-[10px] pl-5">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`px-1.5 py-0.2 rounded font-mono font-bold ${
                        score <= 5
                          ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                          : "bg-purple-500/15 text-purple-300 border border-purple-500/30"
                      }`}
                      title={`AST Complexity: ${score}/10`}
                    >
                      Score: {score}/10
                    </span>

                    {/* Model Tier Badge */}
                    <span
                      className={`px-1.5 py-0.2 rounded font-semibold flex items-center gap-1 ${
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
                      {isSuper ? "SUPER 49B" : "ULTRA 253B"}
                    </span>
                  </div>

                  {/* Symbols count */}
                  {symbolsCount > 0 && (
                    <span className="text-[10px] text-slate-500 font-mono">
                      {symbolsCount} sites
                    </span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Tavily Live Grounding Drawer */}
      <div className="border-t border-slate-800/80 bg-[#060a12]/95 flex flex-col max-h-[46%] transition-all">
        {/* Drawer Header Toggle */}
        <button
          onClick={() => setTavilyOpen(!tavilyOpen)}
          className="w-full px-3.5 py-2.5 flex items-center justify-between hover:bg-slate-900/60 transition-colors border-b border-slate-800/60"
        >
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-cyan-400 animate-pulse" />
            <span className="text-xs font-bold text-slate-200 uppercase tracking-wider">
              Tavily Live Grounding
            </span>
          </div>

          <div className="flex items-center gap-2">
            {migrationDelta && (
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">
                {migrationDelta.rules?.length || 0} rules · {migrationDelta.citations?.length || 0} URLs
              </span>
            )}
            {tavilyOpen ? (
              <ChevronDown className="w-4 h-4 text-slate-400" />
            ) : (
              <ChevronRight className="w-4 h-4 text-slate-400" />
            )}
          </div>
        </button>

        {tavilyOpen && (
          <div className="flex-1 overflow-hidden flex flex-col">
            {/* Sub Tabs */}
            <div className="flex items-center border-b border-slate-800/60 px-3 bg-slate-950/40 text-[11px]">
              <button
                onClick={() => setActiveTavilyTab("rules")}
                className={`py-1.5 px-2.5 font-medium border-b-2 transition-all ${
                  activeTavilyTab === "rules"
                    ? "border-cyan-400 text-cyan-300"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                Synthesized Rules ({migrationDelta?.rules?.length || 0})
              </button>
              <button
                onClick={() => setActiveTavilyTab("citations")}
                className={`py-1.5 px-2.5 font-medium border-b-2 transition-all ${
                  activeTavilyTab === "citations"
                    ? "border-cyan-400 text-cyan-300"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                Live Citations ({migrationDelta?.citations?.length || 0})
              </button>
              <button
                onClick={() => setActiveTavilyTab("drift")}
                className={`py-1.5 px-2.5 font-medium border-b-2 transition-all ${
                  activeTavilyTab === "drift"
                    ? "border-amber-400 text-amber-300"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                Drift Warnings ({driftWarnings.length})
              </button>
            </div>

            {/* Sub Tab Content */}
            <div className="flex-1 overflow-y-auto p-2.5 space-y-2 text-xs">
              {!migrationDelta ? (
                <div className="p-4 text-center text-slate-500 text-xs">
                  Awaiting Stage 2 Tavily Live Delta...
                </div>
              ) : activeTavilyTab === "rules" ? (
                migrationDelta.rules?.map((rule, idx) => {
                  const symbolLabel = rule.symbol || rule.category || "Migration Rule";
                  const explanationText = rule.breaking_gotcha || rule.explanation || "";
                  const severityBadge = rule.severity || (rule.breaking_gotcha ? "BREAKING" : "DEPRECATED");

                  return (
                    <div
                      key={idx}
                      className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80 hover:border-slate-700 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-1 mb-1">
                        <span className="font-semibold text-slate-200 text-[11px] truncate">
                          {symbolLabel}
                        </span>
                        <span
                          className={`text-[9px] px-1.5 py-0.2 rounded font-mono font-bold flex-shrink-0 ${
                            severityBadge === "BREAKING"
                              ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                              : "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                          }`}
                        >
                          {severityBadge}
                        </span>
                      </div>

                      <div className="font-mono text-[10px] space-y-0.5 bg-slate-950/70 p-1.5 rounded border border-slate-800/50 mb-1">
                        <div className="text-rose-400/90 whitespace-pre-wrap">
                          - {rule.old_syntax}
                        </div>
                        <div className="text-emerald-400 whitespace-pre-wrap">
                          + {rule.new_syntax}
                        </div>
                      </div>

                      {explanationText && (
                        <p className="text-[10px] text-slate-400 leading-snug">
                          {explanationText}
                        </p>
                      )}
                    </div>
                  );
                })
              ) : activeTavilyTab === "citations" ? (
                migrationDelta.citations?.map((url, idx) => (
                  <a
                    key={idx}
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="group flex items-center justify-between p-2 rounded-lg bg-slate-900/60 border border-slate-800 hover:border-cyan-500/50 transition-colors"
                  >
                    <span className="font-mono text-[10px] text-cyan-300 truncate group-hover:underline">
                      {url}
                    </span>
                    <ExternalLink className="w-3 h-3 text-slate-500 group-hover:text-cyan-400 flex-shrink-0 ml-1.5" />
                  </a>
                ))
              ) : (
                driftWarnings.map((warn, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 rounded-lg bg-amber-950/20 border border-amber-500/30 text-amber-200/90 text-[11px] flex gap-2 items-start"
                  >
                    <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                    <span>{warn}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
