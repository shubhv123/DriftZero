"use client";

import React, { useState } from "react";
import JSZip from "jszip";
import {
  FileCode2,
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
  const isRealGitHubPR = Boolean(
    prUrl && /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+/.test(prUrl.trim())
  );

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

      // 1. Add context rules (.cursorrules and CLAUDE.md)
      if (cursorContent) {
        zip.file(".cursorrules", cursorContent);
      }
      if (claudeContent) {
        zip.file("CLAUDE.md", claudeContent);
      }

      // 2. Add updated manifest file (requirements.txt or pyproject.toml)
      if (stage5Bundle?.manifest_filename && stage5Bundle?.manifest_content) {
        zip.file(stage5Bundle.manifest_filename, stage5Bundle.manifest_content);
      }

      // 3. Add differential parity test suite
      const parityTest =
        stage5Bundle?.parity_test_content ||
        patchedFiles.get("test_migration_parity.py")?.patched_code ||
        patchedFiles.get("tests/test_migration_parity.py")?.patched_code;
      if (parityTest) {
        zip.file("test_migration_parity.py", parityTest);
      }

      // 4. Add unified diff patch
      let fullUnifiedDiff = "";
      patchedFiles.forEach((fileEvent, relPath) => {
        if (fileEvent.unified_diff && fileEvent.unified_diff.trim()) {
          fullUnifiedDiff += `--- a/${relPath}\n+++ b/${relPath}\n${fileEvent.unified_diff.trim()}\n\n`;
        }
      });
      if (fullUnifiedDiff.trim()) {
        zip.file("unified_diff.patch", fullUnifiedDiff);
      }

      // 5. Add all patched source files
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
    if (isRealGitHubPR && prUrl) {
      window.open(prUrl, "_blank");
    }
  };

  return (
    <div className="h-full flex flex-col bg-black text-neutral-200 overflow-hidden font-mono select-none">
      {/* Header Bar */}
      <div className="px-4 py-2 bg-[#0c0c0c] border-b border-neutral-800 flex flex-wrap items-center justify-between gap-3">
        {/* Sub tabs */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setSelectedFile("cursorrules")}
            className={`px-2.5 py-1 rounded border text-xs font-medium flex items-center gap-1.5 transition-colors ${
              selectedFile === "cursorrules"
                ? "bg-neutral-800 border-neutral-600 text-white"
                : "bg-neutral-950 border-neutral-800 text-neutral-400 hover:text-white"
            }`}
          >
            <FileCode2 className="w-3.5 h-3.5 text-neutral-400" />
            <span>.cursorrules</span>
          </button>

          <button
            onClick={() => setSelectedFile("claude")}
            className={`px-2.5 py-1 rounded border text-xs font-medium flex items-center gap-1.5 transition-colors ${
              selectedFile === "claude"
                ? "bg-neutral-800 border-neutral-600 text-white"
                : "bg-neutral-950 border-neutral-800 text-neutral-400 hover:text-white"
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5 text-neutral-400" />
            <span>CLAUDE.md</span>
          </button>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {/* Copy Button */}
          <button
            onClick={handleCopy}
            disabled={!activeContent}
            className="flex items-center gap-1 px-2.5 py-1 bg-neutral-900 hover:bg-neutral-800 text-neutral-200 rounded text-xs font-medium border border-neutral-700 transition-colors disabled:opacity-40"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-white" />
            ) : (
              <Copy className="w-3.5 h-3.5 text-neutral-400" />
            )}
            <span>{copied ? "Copied" : "Copy"}</span>
          </button>

          {/* Download Bundle (.zip) */}
          <button
            onClick={handleDownloadZip}
            disabled={!stage5Bundle && patchedFiles.size === 0}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-neutral-900 hover:bg-neutral-800 text-neutral-200 rounded text-xs font-medium border border-neutral-700 transition-colors disabled:opacity-40"
          >
            <FolderArchive className="w-3.5 h-3.5 text-neutral-400" />
            <span>{zipping ? "Creating..." : "Download (.zip)"}</span>
          </button>

          {/* Open GitHub PR */}
          <button
            onClick={handleOpenPR}
            disabled={!isRealGitHubPR}
            title={
              isRealGitHubPR
                ? `Open real Pull Request on GitHub: ${prUrl}`
                : "Real GitHub PR requires a public/authed GitHub repository"
            }
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium border transition-colors ${
              isRealGitHubPR
                ? "bg-white hover:bg-neutral-200 text-black border-white shadow-sm"
                : "bg-neutral-900 border-neutral-800 text-neutral-600 cursor-not-allowed"
            }`}
          >
            <GitPullRequest className="w-3.5 h-3.5" />
            <span>{isRealGitHubPR ? "Open PR" : "PR (Local)"}</span>
            {isRealGitHubPR && <ExternalLink className="w-3 h-3 text-black ml-0.5" />}
          </button>
        </div>
      </div>

      {/* Editor / File Content Display */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 select-text bg-black">
        {!activeContent ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-neutral-500 select-none">
            <FileCode2 className="w-10 h-10 stroke-1 text-neutral-600 mb-2.5" />
            <h4 className="text-sm font-semibold text-neutral-300">
              Context Rules Ready
            </h4>
            <p className="text-xs text-neutral-500 max-w-sm mt-1">
              Stage 5 generates repository-specific .cursorrules and CLAUDE.md files to prevent future regressions.
            </p>
          </div>
        ) : (
          <pre className="text-neutral-300 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {activeContent}
          </pre>
        )}
      </div>

      {/* Footer Info */}
      <div className="px-4 py-2 bg-[#0c0c0c] border-t border-neutral-800 flex items-center justify-between text-[11px] text-neutral-500 font-mono">
        <span>Target Branch: {prBranch}</span>
        <span>Repository: {repoUrl || "./demo_target_repo"}</span>
      </div>
    </div>
  );
}
