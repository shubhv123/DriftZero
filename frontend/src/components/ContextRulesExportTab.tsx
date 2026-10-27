"use client";

import React, { useState } from "react";
import JSZip from "jszip";
import {
  FileCode2,
  Download,
  Copy,
  Check,
  GitPullRequest,
  ExternalLink,
  ShieldCheck,
  FolderArchive,
} from "lucide-react";
import { PatchedFileEvent, Stage5Bundle } from "../types/migration";

interface ContextRulesExportTabProps {
  stage5Bundle: Stage5Bundle | null;
  patchedFiles: Map<string, PatchedFileEvent>;
  repoUrl: string;
}

export function ContextRulesExportTab({
  stage5Bundle,
  patchedFiles,
  repoUrl,
}: ContextRulesExportTabProps) {
  const [selectedFile, setSelectedFile] = useState<"cursorrules" | "claude">("cursorrules");
  const [copied, setCopied] = useState(false);
  const [zipping, setZipping] = useState(false);

  const cursorContent = stage5Bundle?.cursorrules_content || "";
  const claudeContent = stage5Bundle?.claude_md_content || "";
  const prUrl = stage5Bundle?.pr_url;
  const prBranch = stage5Bundle?.pr_branch_name || "driftzero/migration";

  const activeContent =
    selectedFile === "cursorrules" ? cursorContent : claudeContent;

  const handleCopy = async () => {
    if (!activeContent) return;
    try {
      await navigator.clipboard.writeText(activeContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy:", err);
    }
  };

  const handleDownloadZip = async () => {
    if (!stage5Bundle && patchedFiles.size === 0) return;
    setZipping(true);
    try {
      const zip = new JSZip();

      // Add .cursorrules and CLAUDE.md
      if (cursorContent) {
        zip.file(".cursorrules", cursorContent);
      }
      if (claudeContent) {
        zip.file("CLAUDE.md", claudeContent);
      }

      // Add all patched files
      const srcFolder = zip.folder("patched_source");
      patchedFiles.forEach((fileEvent, relPath) => {
        srcFolder?.file(relPath, fileEvent.patched_code);
      });

      const blob = await zip.generateAsync({ type: "blob" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `driftzero-migration-bundle.zip`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Failed to generate zip:", err);
    } finally {
      setZipping(false);
    }
  };

  const handleOpenPR = () => {
    if (prUrl) {
      window.open(prUrl, "_blank");
    } else {
      alert(
        `PR Branch '${prBranch}' is prepared locally. To push to GitHub, supply a GITHUB_TOKEN or use git push origin ${prBranch}.`
      );
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#060912] text-slate-200 overflow-hidden font-mono">
      {/* Header Bar */}
      <div className="px-4 py-2.5 bg-slate-900/80 border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
        {/* Sub tabs */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSelectedFile("cursorrules")}
            className={`px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition-all ${
              selectedFile === "cursorrules"
                ? "bg-blue-600/20 border-blue-500/50 text-blue-300 shadow-sm shadow-blue-500/20"
                : "bg-slate-900/50 border-slate-800 text-slate-400 hover:text-slate-200"
            }`}
          >
            <FileCode2 className="w-3.5 h-3.5 text-blue-400" />
            <span>.cursorrules</span>
          </button>

          <button
            onClick={() => setSelectedFile("claude")}
            className={`px-3 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-1.5 transition-all ${
              selectedFile === "claude"
                ? "bg-blue-600/20 border-blue-500/50 text-blue-300 shadow-sm shadow-blue-500/20"
                : "bg-slate-900/50 border-slate-800 text-slate-400 hover:text-slate-200"
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 text-indigo-400" />
            <span>CLAUDE.md</span>
          </button>
        </div>

        {/* 1-Click Action Buttons */}
        <div className="flex items-center gap-2">
          {/* Copy Button */}
          <button
            onClick={handleCopy}
            disabled={!activeContent}
            className="flex items-center gap-1 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition-all active:scale-95 disabled:opacity-40"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <Copy className="w-3.5 h-3.5 text-slate-400" />
            )}
            <span>{copied ? "Copied!" : `Copy ${selectedFile === "cursorrules" ? ".cursorrules" : "CLAUDE.md"}`}</span>
          </button>

          {/* Download Bundle (.zip) */}
          <button
            onClick={handleDownloadZip}
            disabled={!stage5Bundle && patchedFiles.size === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 rounded-lg text-xs font-semibold border border-blue-500/40 transition-all active:scale-95 disabled:opacity-40 shadow-sm shadow-blue-500/10"
          >
            <FolderArchive className="w-3.5 h-3.5 text-blue-400" />
            <span>{zipping ? "Creating Zip..." : "Download Bundle (.zip)"}</span>
          </button>

          {/* Open GitHub PR */}
          <button
            onClick={handleOpenPR}
            disabled={!stage5Bundle}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 rounded-lg text-xs font-semibold border border-emerald-500/40 transition-all active:scale-95 disabled:opacity-40 shadow-sm shadow-emerald-500/10"
          >
            <GitPullRequest className="w-3.5 h-3.5 text-emerald-400" />
            <span>Open GitHub Pull Request</span>
            {prUrl && <ExternalLink className="w-3 h-3 text-emerald-400 ml-0.5" />}
          </button>
        </div>
      </div>

      {/* Info Pill */}
      <div className="px-4 py-2 bg-slate-950/60 border-b border-slate-800/60 text-[11px] text-slate-400 flex items-center justify-between">
        <span>
          Living Rules prevent AI regression in IDEs by enforcing verified AST syntax & migration patterns.
        </span>
        {prBranch && (
          <span className="font-mono text-cyan-300 text-[10px] bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-800/40">
            Branch: {prBranch}
          </span>
        )}
      </div>

      {/* File Preview */}
      <div className="flex-1 overflow-y-auto p-4 bg-[#05070e] font-mono text-xs text-slate-300 select-text">
        {!activeContent ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500 gap-2">
            <FileCode2 className="w-8 h-8 stroke-1 text-slate-600" />
            <span>Awaiting Stage 5 Context Infrastructure generation...</span>
          </div>
        ) : (
          <pre className="whitespace-pre-wrap leading-relaxed text-slate-200">
            {activeContent}
          </pre>
        )}
      </div>
    </div>
  );
}
