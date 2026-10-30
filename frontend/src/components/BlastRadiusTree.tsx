"use client";

import React, { useState, useMemo } from "react";
import {
  FolderTree,
  FileCode,
  Folder,
  FolderOpen,
  ChevronDown,
  ChevronRight,
  Check,
  AlertTriangle,
  ExternalLink,
  Search,
  BookOpen,
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

// Fallback preview files for initial workspace state before scan
const DEFAULT_PREVIEW_FILES: AffectedFile[] = [
  {
    relative_path: "src/config.py",
    normalized_score: 4,
    complexity_score: 4,
    assigned_tier: "SUPER",
    target_model_id: "nvidia/nemotron-4-340b-instruct",
    symbols_detected: ["AppSettings", "BaseSettings", "Field(env)"],
  },
  {
    relative_path: "src/customer_models.py",
    normalized_score: 3,
    complexity_score: 3,
    assigned_tier: "SUPER",
    target_model_id: "nvidia/nemotron-4-340b-instruct",
    symbols_detected: ["BaseModel", "@validator", "class Config"],
  },
  {
    relative_path: "src/inheritance_pipeline.py",
    normalized_score: 7,
    complexity_score: 7,
    assigned_tier: "ULTRA",
    target_model_id: "nvidia/llama-3.1-nemotron-70b-instruct",
    symbols_detected: ["GenericModel", "BaseFinancialModel", "parse_obj_as"],
  },
  {
    relative_path: "src/llm_summarizer.py",
    normalized_score: 2,
    complexity_score: 2,
    assigned_tier: "SUPER",
    target_model_id: "nvidia/nemotron-4-340b-instruct",
    symbols_detected: ["BaseModel", "schema_json", ".dict()"],
  },
  {
    relative_path: "src/order_schemas.py",
    normalized_score: 3,
    complexity_score: 3,
    assigned_tier: "SUPER",
    target_model_id: "nvidia/nemotron-4-340b-instruct",
    symbols_detected: ["BaseModel", "root_validator(pre=True)"],
  },
  {
    relative_path: "src/risk_validator_engine.py",
    normalized_score: 8,
    complexity_score: 8,
    assigned_tier: "ULTRA",
    target_model_id: "nvidia/llama-3.1-nemotron-70b-instruct",
    symbols_detected: ["validator(always=True)", "root_validator", "parse_raw"],
  },
  {
    relative_path: "src/serializers.py",
    normalized_score: 4,
    complexity_score: 4,
    assigned_tier: "SUPER",
    target_model_id: "nvidia/nemotron-4-340b-instruct",
    symbols_detected: [".json()", ".copy()", ".parse_obj()"],
  },
  {
    relative_path: "src/webhook_events.py",
    normalized_score: 7,
    complexity_score: 7,
    assigned_tier: "ULTRA",
    target_model_id: "nvidia/llama-3.1-nemotron-70b-instruct",
    symbols_detected: ["__root__", "validator(each_item=True)"],
  },
];

interface TreeNode {
  name: string;
  path: string;
  isFolder: boolean;
  children: TreeNode[];
  file?: AffectedFile;
}

export function BlastRadiusTree({
  blastReport,
  migrationDelta,
  patchedFiles,
  selectedFilePath,
  onSelectFile,
}: BlastRadiusTreeProps) {
  const [tavilyOpen, setTavilyOpen] = useState(false);
  const [activeTavilyTab, setActiveTavilyTab] = useState<"rules" | "citations" | "drift">("rules");
  const [searchFilter, setSearchFilter] = useState("");
  const [collapsedFolders, setCollapsedFolders] = useState<Record<string, boolean>>({});

  const affectedFiles = blastReport?.affected_files && blastReport.affected_files.length > 0
    ? blastReport.affected_files
    : [];

  const filteredFiles = useMemo(() => {
    return affectedFiles.filter((f) =>
      f.relative_path.toLowerCase().includes(searchFilter.toLowerCase())
    );
  }, [affectedFiles, searchFilter]);

  // Build hierarchical tree structure from paths
  const treeRoot = useMemo(() => {
    const root: TreeNode = { name: "root", path: "", isFolder: true, children: [] };

    for (const file of filteredFiles) {
      const parts = file.relative_path.split("/");
      let current = root;

      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        const isLast = i === parts.length - 1;
        const currentPath = parts.slice(0, i + 1).join("/");

        let child = current.children.find((c) => c.name === part);
        if (!child) {
          child = {
            name: part,
            path: currentPath,
            isFolder: !isLast,
            children: [],
            file: isLast ? file : undefined,
          };
          current.children.push(child);
        }
        current = child;
      }
    }

    // Sort folders first, then alphabetically
    const sortNodes = (node: TreeNode) => {
      node.children.sort((a, b) => {
        if (a.isFolder === b.isFolder) {
          return a.name.localeCompare(b.name);
        }
        return a.isFolder ? -1 : 1;
      });
      node.children.forEach(sortNodes);
    };
    sortNodes(root);

    return root;
  }, [filteredFiles]);

  const toggleFolder = (folderPath: string) => {
    setCollapsedFolders((prev) => ({
      ...prev,
      [folderPath]: !prev[folderPath],
    }));
  };

  const driftWarnings: string[] =
    migrationDelta?.silent_drift_warnings && migrationDelta.silent_drift_warnings.length > 0
      ? migrationDelta.silent_drift_warnings
      : migrationDelta?.rules
          ?.filter((r) => r.breaking_gotcha && r.breaking_gotcha.length > 0)
          .map((r) => `${r.symbol || "Semantic Edge"}: ${r.breaking_gotcha}`) || [];

  // Recursive Tree Node Renderer
  const renderNode = (node: TreeNode, depth: number = 0) => {
    if (node.isFolder) {
      const isCollapsed = Boolean(collapsedFolders[node.path]);
      return (
        <div key={node.path} className="flex flex-col">
          <div
            onClick={() => toggleFolder(node.path)}
            style={{ paddingLeft: `${depth * 12 + 8}px` }}
            className="flex items-center gap-1.5 py-1 px-2 rounded-md hover:bg-neutral-900 text-neutral-300 cursor-pointer text-xs select-none group transition-colors"
          >
            {isCollapsed ? (
              <ChevronRight className="w-3.5 h-3.5 text-neutral-500 group-hover:text-neutral-300" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-neutral-500 group-hover:text-neutral-300" />
            )}
            {isCollapsed ? (
              <Folder className="w-3.5 h-3.5 text-neutral-400" />
            ) : (
              <FolderOpen className="w-3.5 h-3.5 text-neutral-300" />
            )}
            <span className="font-mono font-medium text-neutral-200">{node.name}</span>
            <span className="text-[10px] text-neutral-500 font-mono ml-auto">
              ({node.children.length})
            </span>
          </div>

          {!isCollapsed && (
            <div className="flex flex-col border-l border-neutral-800/60 ml-3.5">
              {node.children.map((child) => renderNode(child, depth + 1))}
            </div>
          )}
        </div>
      );
    }

    // File Node
    const file = node.file!;
    const isSelected = selectedFilePath === file.relative_path;
    const patched = patchedFiles.get(file.relative_path);
    const score = file.normalized_score ?? file.complexity_score ?? 1;
    const isSuper = (file.assigned_tier || "").toUpperCase().includes("SUPER");

    return (
      <div
        key={node.path}
        onClick={() => onSelectFile(file.relative_path)}
        style={{ paddingLeft: `${depth * 12 + 6}px` }}
        className={`flex items-center justify-between gap-2 py-1.5 px-2 rounded-md cursor-pointer text-xs transition-colors group ${
          isSelected
            ? "bg-neutral-800 text-white font-medium"
            : "hover:bg-neutral-900/80 text-neutral-300"
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <FileCode
            className={`w-3.5 h-3.5 flex-shrink-0 ${
              isSelected ? "text-white" : "text-neutral-500 group-hover:text-neutral-400"
            }`}
          />
          <span className="font-mono text-xs truncate">{node.name}</span>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          {patched && (
            <span
              title={`Patched in ${Math.round(patched.telemetry?.latency_ms || 0)}ms`}
              className="flex items-center gap-0.5 text-[10px] text-neutral-300 bg-neutral-800 px-1 rounded font-mono"
            >
              <Check className="w-2.5 h-2.5 text-white" />
              <span>{Math.round(patched.telemetry?.latency_ms || 0)}ms</span>
            </span>
          )}

          <span
            className="text-[9px] font-mono px-1 py-0.2 rounded bg-neutral-900 border border-neutral-800 text-neutral-400"
            title={`AST Score: ${score}/10`}
          >
            {score}/10
          </span>

          <span
            className="text-[9px] font-mono font-medium px-1 py-0.2 rounded bg-neutral-900 border border-neutral-800 text-neutral-400"
            title={`Model tier: ${isSuper ? "Super" : "Ultra"}`}
          >
            {isSuper ? "S" : "U"}
          </span>
        </div>
      </div>
    );
  };

  return (
    <div className="w-full h-full flex flex-col bg-[#0c0c0c] border-r border-neutral-800 overflow-hidden select-none">
      {/* Pane Top Header */}
      <div className="p-3 border-b border-neutral-800 bg-[#0a0a0a]">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <FolderTree className="w-4 h-4 text-neutral-400" />
            <h3 className="text-xs font-semibold text-neutral-200 tracking-tight">
              AST Blast-Radius Tree
            </h3>
          </div>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-neutral-900 border border-neutral-800 text-neutral-400">
            {blastReport ? `${blastReport.affected_files_count} files` : "0 files"}
          </span>
        </div>

        {/* Filter Input */}
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-neutral-500" />
          <input
            type="text"
            placeholder="Filter files..."
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            className="w-full bg-neutral-900 border border-neutral-800 rounded pl-7 pr-2 py-1 text-xs text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-neutral-600 transition-colors font-mono"
          />
        </div>
      </div>

      {/* Notion Hierarchical File List Section */}
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {treeRoot.children.length === 0 ? (
          <div className="p-6 text-center text-neutral-500 text-xs flex flex-col items-center justify-center gap-2 h-full">
            {!blastReport ? (
              <>
                <FolderTree className="w-6 h-6 text-neutral-600 mb-1" />
                <span className="font-medium text-neutral-400">No repository scanned yet</span>
                <span className="text-[11px] text-neutral-600 max-w-[200px] leading-relaxed">
                  Enter a repository URL above and click Fix Repository to discover affected files.
                </span>
              </>
            ) : searchFilter ? (
              <span>No files match &quot;{searchFilter}&quot;</span>
            ) : (
              <span>No affected files found for target migration</span>
            )}
          </div>
        ) : (
          treeRoot.children.map((child) => renderNode(child, 0))
        )}
      </div>

      {/* Tavily Live Grounding Accordion Drawer */}
      <div className="border-t border-neutral-800 bg-[#0a0a0a] flex flex-col max-h-[46%] transition-all">
        <button
          onClick={() => setTavilyOpen(!tavilyOpen)}
          className="w-full px-3 py-2 flex items-center justify-between hover:bg-neutral-900 transition-colors border-b border-neutral-800/80"
        >
          <div className="flex items-center gap-2">
            <BookOpen className="w-3.5 h-3.5 text-neutral-400" />
            <span className="text-xs font-semibold text-neutral-300">
              Tavily Grounding Delta
            </span>
          </div>

          <div className="flex items-center gap-2">
            {migrationDelta && (
              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-neutral-900 border border-neutral-800 text-neutral-400">
                {migrationDelta.rules?.length || 0} rules
              </span>
            )}
            {tavilyOpen ? (
              <ChevronDown className="w-3.5 h-3.5 text-neutral-400" />
            ) : (
              <ChevronRight className="w-3.5 h-3.5 text-neutral-400" />
            )}
          </div>
        </button>

        {tavilyOpen && (
          <div className="flex-1 overflow-hidden flex flex-col">
            {/* Sub Tabs */}
            <div className="flex items-center border-b border-neutral-800 px-2 bg-neutral-950 text-[11px]">
              <button
                onClick={() => setActiveTavilyTab("rules")}
                className={`py-1.5 px-2 font-medium border-b-2 transition-colors ${
                  activeTavilyTab === "rules"
                    ? "border-white text-white"
                    : "border-transparent text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Rules ({migrationDelta?.rules?.length || 0})
              </button>
              <button
                onClick={() => setActiveTavilyTab("citations")}
                className={`py-1.5 px-2 font-medium border-b-2 transition-colors ${
                  activeTavilyTab === "citations"
                    ? "border-white text-white"
                    : "border-transparent text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Citations ({migrationDelta?.citations?.length || 0})
              </button>
              <button
                onClick={() => setActiveTavilyTab("drift")}
                className={`py-1.5 px-2 font-medium border-b-2 transition-colors ${
                  activeTavilyTab === "drift"
                    ? "border-white text-white"
                    : "border-transparent text-neutral-400 hover:text-neutral-200"
                }`}
              >
                Drift ({driftWarnings.length})
              </button>
            </div>

            {/* Sub Tab Content */}
            <div className="flex-1 overflow-y-auto p-2 space-y-2 text-xs">
              {!migrationDelta ? (
                <div className="p-4 text-center text-neutral-500 text-xs">
                  Awaiting Stage 2 Tavily Delta...
                </div>
              ) : activeTavilyTab === "rules" ? (
                migrationDelta.rules?.map((rule, idx) => {
                  const symbolLabel = rule.symbol || rule.category || "Rule";
                  const explanationText = rule.breaking_gotcha || rule.explanation || "";
                  const severityBadge = rule.severity || (rule.breaking_gotcha ? "BREAKING" : "DEPRECATED");

                  return (
                    <div
                      key={idx}
                      className="p-2 rounded bg-neutral-900/60 border border-neutral-800 text-xs"
                    >
                      <div className="flex items-center justify-between gap-1 mb-1">
                        <span className="font-semibold text-neutral-200 text-[11px] truncate">
                          {symbolLabel}
                        </span>
                        <span className="text-[9px] px-1 py-0.2 rounded font-mono font-medium bg-neutral-800 text-neutral-300 border border-neutral-700">
                          {severityBadge}
                        </span>
                      </div>

                      <div className="font-mono text-[10px] space-y-0.5 bg-neutral-950 p-1.5 rounded border border-neutral-800/60 mb-1">
                        <div className="text-neutral-400 whitespace-pre-wrap">
                          - {rule.old_syntax}
                        </div>
                        <div className="text-white whitespace-pre-wrap">
                          + {rule.new_syntax}
                        </div>
                      </div>

                      {explanationText && (
                        <p className="text-[10px] text-neutral-400 leading-snug">
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
                    className="group flex items-center justify-between p-1.5 rounded bg-neutral-900 border border-neutral-800 hover:border-neutral-600 transition-colors"
                  >
                    <span className="font-mono text-[10px] text-neutral-300 truncate group-hover:underline">
                      {url}
                    </span>
                    <ExternalLink className="w-3 h-3 text-neutral-500 group-hover:text-white flex-shrink-0 ml-1.5" />
                  </a>
                ))
              ) : (
                driftWarnings.map((warn, idx) => (
                  <div
                    key={idx}
                    className="p-2 rounded bg-neutral-900 border border-neutral-800 text-neutral-300 text-[11px] flex gap-2 items-start"
                  >
                    <AlertTriangle className="w-3.5 h-3.5 text-neutral-400 flex-shrink-0 mt-0.5" />
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
