"use client";

import React from "react";
import {
  Layers,
  FileCode2,
  Terminal,
  Cpu,
  Sparkles,
  ShieldCheck,
  ExternalLink,
} from "lucide-react";

interface SidebarNavProps {
  activeView: "workspace" | "rules" | "terminal";
  setActiveView: (view: "workspace" | "rules" | "terminal") => void;
  statusText?: string;
  isStreaming?: boolean;
}

export function SidebarNav({
  activeView,
  setActiveView,
  statusText = "Online",
  isStreaming = false,
}: SidebarNavProps) {
  return (
    <aside className="w-16 flex-shrink-0 bg-[#070b14]/90 border-r border-slate-800/60 flex flex-col items-center py-4 justify-between select-none z-30">
      {/* Top: Logo & Brand Indicator */}
      <div className="flex flex-col items-center gap-6">
        <div className="relative group cursor-pointer" title="DriftZero Engine">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 via-indigo-600 to-cyan-400 p-[1.5px] shadow-lg shadow-blue-500/20">
            <div className="w-full h-full bg-[#090d18] rounded-[10px] flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-cyan-400 animate-pulse" />
            </div>
          </div>
          {isStreaming && (
            <span className="absolute -top-1 -right-1 w-3 h-3 bg-emerald-500 rounded-full border-2 border-[#090d18] animate-ping" />
          )}
        </div>

        {/* Primary Navigation Icons */}
        <nav className="flex flex-col items-center gap-3">
          <button
            onClick={() => setActiveView("workspace")}
            title="Split-Pane Command Center (Diffs & Tree)"
            className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${
              activeView === "workspace"
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/40 shadow-sm shadow-blue-500/30"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
            }`}
          >
            <Layers className="w-5 h-5" />
          </button>

          <button
            onClick={() => setActiveView("terminal")}
            title="Nebius Sandbox Terminal"
            className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${
              activeView === "terminal"
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/40 shadow-sm shadow-blue-500/30"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
            }`}
          >
            <Terminal className="w-5 h-5" />
          </button>

          <button
            onClick={() => setActiveView("rules")}
            title="Context Infrastructure (.cursorrules & CLAUDE.md)"
            className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${
              activeView === "rules"
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/40 shadow-sm shadow-blue-500/30"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
            }`}
          >
            <FileCode2 className="w-5 h-5" />
          </button>
        </nav>
      </div>

      {/* Bottom: Cloud & Model Engine Status Badge */}
      <div className="flex flex-col items-center gap-3">
        <a
          href="https://nebius.com"
          target="_blank"
          rel="noreferrer"
          title="Powered by Nebius Token Factory"
          className="w-10 h-10 rounded-xl bg-slate-900/80 border border-slate-800 hover:border-emerald-500/40 flex items-center justify-center text-slate-400 hover:text-emerald-400 transition-colors"
        >
          <Cpu className="w-4 h-4" />
        </a>

        <div
          className="group relative flex items-center justify-center w-10 h-10 rounded-xl bg-slate-900 border border-slate-800"
          title={`Status: ${statusText}`}
        >
          <div
            className={`w-2.5 h-2.5 rounded-full ${
              isStreaming
                ? "bg-emerald-400 shadow-sm shadow-emerald-400 animate-pulse"
                : "bg-cyan-400"
            }`}
          />
        </div>
      </div>
    </aside>
  );
}
