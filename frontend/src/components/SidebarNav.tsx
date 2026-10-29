"use client";

import React from "react";
import {
  Layers,
  FileCode2,
  Terminal,
  Cpu,
  Sparkles,
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
    <aside className="w-14 flex-shrink-0 bg-[#0a0a0a] border-r border-neutral-800 flex flex-col items-center py-3.5 justify-between select-none z-30">
      {/* Top: Minimalist Logo */}
      <div className="flex flex-col items-center gap-5">
        <div
          className="relative group cursor-pointer w-8 h-8 rounded-md bg-neutral-900 border border-neutral-700 flex items-center justify-center text-white"
          title="DriftZero Engine"
        >
          <span className="font-mono text-xs font-bold tracking-tight">DZ</span>
          {isStreaming && (
            <span className="absolute -top-1 -right-1 w-2 h-2 bg-white rounded-full animate-ping" />
          )}
        </div>

        {/* Primary Navigation Icons */}
        <nav className="flex flex-col items-center gap-2">
          <button
            onClick={() => setActiveView("workspace")}
            title="Split-Pane Command Center (Diffs & Tree)"
            className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
              activeView === "workspace"
                ? "bg-neutral-800 text-white border border-neutral-700"
                : "text-neutral-400 hover:text-white hover:bg-neutral-900"
            }`}
          >
            <Layers className="w-4 h-4" />
          </button>

          <button
            onClick={() => setActiveView("terminal")}
            title="Nebius Sandbox Terminal"
            className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
              activeView === "terminal"
                ? "bg-neutral-800 text-white border border-neutral-700"
                : "text-neutral-400 hover:text-white hover:bg-neutral-900"
            }`}
          >
            <Terminal className="w-4 h-4" />
          </button>

          <button
            onClick={() => setActiveView("rules")}
            title="Context Infrastructure (.cursorrules & CLAUDE.md)"
            className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
              activeView === "rules"
                ? "bg-neutral-800 text-white border border-neutral-700"
                : "text-neutral-400 hover:text-white hover:bg-neutral-900"
            }`}
          >
            <FileCode2 className="w-4 h-4" />
          </button>
        </nav>
      </div>

      {/* Bottom: Cloud & Model Engine Status Badge */}
      <div className="flex flex-col items-center gap-2.5">
        <div
          className="group relative flex items-center justify-center w-8 h-8 rounded-md bg-neutral-900 border border-neutral-800"
          title={`Status: ${statusText}`}
        >
          <div
            className={`w-2 h-2 rounded-full ${
              isStreaming
                ? "bg-white animate-pulse"
                : "bg-neutral-500"
            }`}
          />
        </div>
      </div>
    </aside>
  );
}
