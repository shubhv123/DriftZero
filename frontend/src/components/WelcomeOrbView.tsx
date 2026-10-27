"use client";

import React from "react";
import {
  Sparkles,
  Zap,
  Cpu,
  Search,
} from "lucide-react";

interface WelcomeOrbViewProps {
  onQuickSelect: (repo: string) => void;
  onLaunchJudgeDemo: () => void;
}

export function WelcomeOrbView({
  onQuickSelect,
  onLaunchJudgeDemo,
}: WelcomeOrbViewProps) {
  return (
    <div className="h-full w-full flex flex-col items-center justify-center p-6 text-center select-none relative overflow-hidden bg-gradient-to-b from-[#080d1a] to-[#04060c]">
      {/* Background ambient lighting */}
      <div className="absolute w-[500px] h-[500px] bg-blue-600/10 rounded-full blur-3xl pointer-events-none -top-20" />
      <div className="absolute w-[400px] h-[400px] bg-cyan-500/10 rounded-full blur-3xl pointer-events-none -bottom-20" />

      {/* Center glowing vortex orb */}
      <div className="relative mb-8 group cursor-pointer" onClick={onLaunchJudgeDemo}>
        {/* Outer radial aura */}
        <div className="w-56 h-56 rounded-full bg-gradient-to-tr from-blue-500/20 via-cyan-400/25 to-indigo-500/20 blur-xl animate-orb-pulse pointer-events-none" />

        {/* Concentric spiral graphic */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-44 h-44 rounded-full border border-cyan-500/20 animate-orb-spin flex items-center justify-center">
            <div className="w-36 h-36 rounded-full border border-blue-400/30 flex items-center justify-center border-dashed">
              <div className="w-28 h-28 rounded-full border border-indigo-400/40 flex items-center justify-center">
                <div className="w-20 h-20 rounded-full bg-gradient-to-tr from-blue-600 via-indigo-600 to-cyan-400 p-[2px] shadow-2xl shadow-cyan-500/50 flex items-center justify-center group-hover:scale-105 transition-transform duration-300">
                  <div className="w-full h-full bg-[#080d1c] rounded-full flex items-center justify-center">
                    <Sparkles className="w-8 h-8 text-cyan-400 animate-pulse" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Title & Tagline */}
      <div className="max-w-xl z-10 space-y-2 mb-8">
        <h2 className="text-2xl font-bold tracking-tight text-white flex items-center justify-center gap-2">
          <span>DriftZero Engine is ready</span>
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 inline-block animate-pulse" />
        </h2>
        <p className="text-sm text-slate-400 leading-relaxed">
          Zero user input required. Enter any repository URL and DriftZero will
          autonomously inspect AST imports, check PyPI for outdated dependencies,
          synthesize live breaking changes via Tavily, and patch code using NVIDIA Nemotron on Nebius Token Factory.
        </p>
      </div>

      {/* Quick Launch Preset Badges - purely repository URLs, zero hardcoded libs */}
      <div className="z-10 flex flex-wrap items-center justify-center gap-2.5 max-w-2xl mb-8">
        <button
          onClick={onLaunchJudgeDemo}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-amber-500/15 border border-amber-500/40 text-amber-300 text-xs font-semibold hover:bg-amber-500/25 transition-all shadow-sm shadow-amber-500/20 active:scale-95"
        >
          <Zap className="w-3.5 h-3.5 text-amber-400" />
          <span>⚡ Fast 1-Click Judge Demo</span>
        </button>

        <button
          onClick={() => onQuickSelect("https://github.com/shubhv123/pdf-rag")}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-blue-600/15 border border-blue-500/40 text-blue-300 text-xs font-semibold hover:bg-blue-600/25 transition-all shadow-sm shadow-blue-500/20 active:scale-95"
        >
          <Sparkles className="w-3.5 h-3.5 text-blue-400" />
          <span>⚡ Test shubhv123/pdf-rag</span>
        </button>

        <button
          onClick={() => onQuickSelect("https://github.com/shubhv123/autonomous-browser-agent")}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-purple-600/15 border border-purple-500/40 text-purple-300 text-xs font-semibold hover:bg-purple-600/25 transition-all shadow-sm shadow-purple-500/20 active:scale-95"
        >
          <Cpu className="w-3.5 h-3.5 text-purple-400" />
          <span>⚡ Test shubhv123/autonomous-browser-agent</span>
        </button>
      </div>

      {/* Architecture Highlights Pill */}
      <div className="z-10 grid grid-cols-1 sm:grid-cols-3 gap-3 max-w-xl text-left">
        <div className="p-3 rounded-xl bg-slate-900/50 border border-slate-800/80">
          <div className="text-[11px] font-semibold text-blue-400 mb-1 flex items-center gap-1">
            <Search className="w-3 h-3" /> Zero-Token AST
          </div>
          <p className="text-[10px] text-slate-400">
            Scans imports, base class MRO hierarchies, and calls in under 15ms.
          </p>
        </div>

        <div className="p-3 rounded-xl bg-slate-900/50 border border-slate-800/80">
          <div className="text-[11px] font-semibold text-cyan-400 mb-1 flex items-center gap-1">
            <Sparkles className="w-3 h-3" /> Tavily Live Delta
          </div>
          <p className="text-[10px] text-slate-400">
            Multi-endpoint live search and documentation synthesis.
          </p>
        </div>

        <div className="p-3 rounded-xl bg-slate-900/50 border border-slate-800/80">
          <div className="text-[11px] font-semibold text-purple-400 mb-1 flex items-center gap-1">
            <Cpu className="w-3 h-3" /> Smart Router
          </div>
          <p className="text-[10px] text-slate-400">
            Routes syntactic files to Nemotron Super and complex reasoning to Nemotron Ultra.
          </p>
        </div>
      </div>
    </div>
  );
}
