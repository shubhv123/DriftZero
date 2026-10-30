"use client";

import React, { useState } from "react";
import {
  BookOpen,
  ExternalLink,
  AlertTriangle,
  Search,
} from "lucide-react";
import { MigrationDelta } from "../types/migration";

interface TavilyDeltaTabProps {
  migrationDelta: MigrationDelta | null;
}

export function TavilyDeltaTab({ migrationDelta }: TavilyDeltaTabProps) {
  const [filter, setFilter] = useState("");
  const [activeSubTab, setActiveSubTab] = useState<"all" | "breaking" | "citations">("all");

  const rules = migrationDelta?.rules || [];
  const citations = migrationDelta?.citations || [];
  const driftWarnings = migrationDelta?.silent_drift_warnings || [];

  const filteredRules = rules.filter((r) => {
    const text = `${r.symbol || ""} ${r.category || ""} ${r.explanation || ""} ${r.breaking_gotcha || ""}`.toLowerCase();
    const matchesQuery = text.includes(filter.toLowerCase());
    if (activeSubTab === "breaking") {
      return matchesQuery && (r.severity === "BREAKING" || Boolean(r.breaking_gotcha));
    }
    return matchesQuery;
  });

  return (
    <div className="h-full flex flex-col bg-black text-neutral-200 overflow-hidden font-mono select-none">
      {/* Header Bar */}
      <div className="px-4 py-2 bg-[#0c0c0c] border-b border-neutral-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1.5 text-xs">
          <button
            onClick={() => setActiveSubTab("all")}
            className={`px-2.5 py-1 rounded border transition-colors ${
              activeSubTab === "all"
                ? "bg-neutral-800 border-neutral-600 text-white font-medium"
                : "bg-neutral-950 border-neutral-800 text-neutral-400 hover:text-white"
            }`}
          >
            All Rules ({rules.length})
          </button>
          <button
            onClick={() => setActiveSubTab("breaking")}
            className={`px-2.5 py-1 rounded border transition-colors ${
              activeSubTab === "breaking"
                ? "bg-neutral-800 border-neutral-600 text-white font-medium"
                : "bg-neutral-950 border-neutral-800 text-neutral-400 hover:text-white"
            }`}
          >
            Breaking Changes
          </button>
          <button
            onClick={() => setActiveSubTab("citations")}
            className={`px-2.5 py-1 rounded border transition-colors ${
              activeSubTab === "citations"
                ? "bg-neutral-800 border-neutral-600 text-white font-medium"
                : "bg-neutral-950 border-neutral-800 text-neutral-400 hover:text-white"
            }`}
          >
            Citations ({citations.length})
          </button>
        </div>

        {/* Filter */}
        <div className="relative w-48">
          <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-neutral-500" />
          <input
            type="text"
            placeholder="Search delta..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-full bg-neutral-900 border border-neutral-800 rounded pl-7 pr-2 py-1 text-xs text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors font-mono"
          />
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {!migrationDelta ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-neutral-500 select-none">
            <BookOpen className="w-10 h-10 stroke-1 text-neutral-600 mb-2.5" />
            <h4 className="text-sm font-semibold text-neutral-300">
              Tavily Live Grounding Delta
            </h4>
            <p className="text-xs text-neutral-500 max-w-sm mt-1">
              Stage 2 searches live documentation, changelogs, and official migration guides to synthesize syntax transformation rules.
            </p>
          </div>
        ) : activeSubTab === "citations" ? (
          <div className="space-y-2">
            <div className="text-xs font-semibold text-neutral-300 uppercase tracking-wider mb-2">
              Verified Documentation Sources ({citations.length})
            </div>
            {citations.map((url, idx) => (
              <a
                key={idx}
                href={url}
                target="_blank"
                rel="noreferrer"
                className="group flex items-center justify-between p-2.5 rounded bg-neutral-900/60 border border-neutral-800 hover:border-neutral-600 transition-colors"
              >
                <span className="font-mono text-xs text-neutral-200 truncate group-hover:underline">
                  {url}
                </span>
                <ExternalLink className="w-3.5 h-3.5 text-neutral-500 group-hover:text-white flex-shrink-0 ml-2" />
              </a>
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            {driftWarnings.length > 0 && (
              <div className="p-3 rounded bg-neutral-900/60 border border-neutral-800 text-neutral-300 text-xs space-y-1.5">
                <div className="font-semibold text-white flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-neutral-400" />
                  <span>Silent Semantic Drift Warnings</span>
                </div>
                {driftWarnings.map((w, idx) => (
                  <div key={idx} className="text-neutral-400 text-[11px] pl-5">
                    • {w}
                  </div>
                ))}
              </div>
            )}

            {filteredRules.length === 0 ? (
              <div className="p-6 text-center text-neutral-500 text-xs">
                No rules match the current filter.
              </div>
            ) : (
              filteredRules.map((rule, idx) => {
                const symbolLabel = rule.symbol || rule.category || "Rule";
                const explanationText = rule.breaking_gotcha || rule.explanation || "";
                const severity = rule.severity || (rule.breaking_gotcha ? "BREAKING" : "DEPRECATED");

                return (
                  <div
                    key={idx}
                    className="p-3 rounded bg-neutral-900/40 border border-neutral-800 space-y-2 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-white text-xs">
                        {symbolLabel}
                      </span>
                      <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-neutral-800 border border-neutral-700 text-neutral-300">
                        {severity}
                      </span>
                    </div>

                    <div className="font-mono text-xs bg-neutral-950 p-2.5 rounded border border-neutral-800/80 space-y-1">
                      <div className="text-neutral-500 whitespace-pre-wrap">
                        - {rule.old_syntax}
                      </div>
                      <div className="text-white whitespace-pre-wrap font-medium">
                        + {rule.new_syntax}
                      </div>
                    </div>

                    {explanationText && (
                      <p className="text-neutral-400 text-xs leading-relaxed">
                        {explanationText}
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}

