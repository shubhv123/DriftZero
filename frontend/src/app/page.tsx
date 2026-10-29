"use client";

import React, { useState, useRef, useEffect } from "react";
import { SidebarNav } from "../components/SidebarNav";
import { TopControlBar } from "../components/TopControlBar";
import { PipelineStepper } from "../components/PipelineStepper";
import { TelemetryBar } from "../components/TelemetryBar";
import { BlastRadiusTree } from "../components/BlastRadiusTree";
import { DiffViewerTab } from "../components/DiffViewerTab";
import { SandboxTerminalTab } from "../components/SandboxTerminalTab";
import { ContextRulesExportTab } from "../components/ContextRulesExportTab";
import { TavilyDeltaTab } from "../components/TavilyDeltaTab";
import { OverviewTab } from "../components/OverviewTab";
import {
  AffectedFile,
  BlastRadiusReport,
  MigrationDelta,
  PatchedFileEvent,
  Stage3Report,
  Stage4Report,
  Stage5Bundle,
  StageTiming,
  ManifestConflictsData,
} from "../types/migration";
import { FileCode, Terminal, FileCode2, AlertCircle, AlertTriangle, BookOpen, FileText } from "lucide-react";

export default function DriftZeroCommandCenter() {
  // Input parameters (Autonomous Discovery Engine handles library & version detection)
  const [repoUrl, setRepoUrl] = useState<string>("./demo_target_repo");
  const [targetLibrary, setTargetLibrary] = useState<string>("");
  const [targetVersion, setTargetVersion] = useState<string>("");
  const [buildLog, setBuildLog] = useState<string>("");

  // Navigation & Workspace tabs
  const [activeSidebarView, setActiveSidebarView] = useState<
    "workspace" | "rules" | "terminal"
  >("workspace");
  const [rightPaneTab, setRightPaneTab] = useState<
    "overview" | "diff" | "sandbox" | "rules" | "delta"
  >("diff");

  // Pipeline Execution State
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [currentStage, setCurrentStage] = useState<number>(0);
  const [isCompleted, setIsCompleted] = useState<boolean>(false);
  const [isError, setIsError] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [verificationWarning, setVerificationWarning] = useState<string | null>(null);
  const [manifestConflicts, setManifestConflicts] = useState<ManifestConflictsData | null>(null);
  const [stageTimings, setStageTimings] = useState<Record<number, StageTiming>>(
    {}
  );

  // Live SSE Data Containers
  const [blastReport, setBlastReport] = useState<BlastRadiusReport | null>(null);
  const [migrationDelta, setMigrationDelta] =
    useState<MigrationDelta | null>(null);
  const [patchedFiles, setPatchedFiles] = useState<Map<string, PatchedFileEvent>>(
    new Map()
  );
  const [stage3Report, setStage3Report] = useState<Stage3Report | null>(null);
  const [stage4Report, setStage4Report] = useState<Stage4Report | null>(null);
  const [stage5Bundle, setStage5Bundle] = useState<Stage5Bundle | null>(null);

  // Active Selected File for Monaco Diff
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);

  // Telemetry Aggregates & Dynamic Model Labels
  const [superCalls, setSuperCalls] = useState<number>(0);
  const [ultraCalls, setUltraCalls] = useState<number>(0);
  const [actualCostUsd, setActualCostUsd] = useState<number>(0);
  const [hypotheticalCostUsd, setHypotheticalCostUsd] = useState<number>(0);
  const [savingsPercentage, setSavingsPercentage] = useState<number>(0);
  const [executionMode, setExecutionMode] = useState<string>("AWAITING");
  const [superModelLabel, setSuperModelLabel] = useState<string>("Nemotron Super");
  const [ultraModelLabel, setUltraModelLabel] = useState<string>("Nemotron Ultra");

  // Abort controller ref for active stream
  const abortControllerRef = useRef<AbortController | null>(null);

  // Synchronize sidebar icon clicks with right pane tabs
  const handleSidebarNavChange = (view: "workspace" | "rules" | "terminal") => {
    setActiveSidebarView(view);
    if (view === "rules") setRightPaneTab("rules");
    if (view === "terminal") setRightPaneTab("sandbox");
    if (view === "workspace") setRightPaneTab("diff");
  };

  const stopMigration = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsRunning(false);
  };

  // Autonomous Discovery state
  const [detectedInfo, setDetectedInfo] = useState<{
    library: string;
    targetVersion: string;
    sourceVersion?: string | null;
  } | null>(null);

  const startMigration = async (
    customRepo?: string,
    targetLib?: string,
    targetVer?: string,
    customBuildLog?: string
  ) => {
    // Cancel any existing run
    stopMigration();

    const activeRepo = customRepo ?? repoUrl;
    const activeLib = targetLib !== undefined ? targetLib : targetLibrary;
    const activeVer = targetVer !== undefined ? targetVer : targetVersion;
    const activeBuildLog = customBuildLog !== undefined ? customBuildLog : buildLog;

    if (!activeRepo.trim()) {
      alert("Please provide a GitHub repository URL or directory path.");
      return;
    }

    // Reset pipeline state
    setIsRunning(true);
    setIsCompleted(false);
    setIsError(false);
    setErrorMessage(null);
    setVerificationWarning(null);
    setManifestConflicts(null);
    setCurrentStage(0);
    setStageTimings({});
    setBlastReport(null);
    setMigrationDelta(null);
    setPatchedFiles(new Map());
    setStage3Report(null);
    setStage4Report(null);
    setStage5Bundle(null);
    setSelectedFilePath(null);
    setSuperCalls(0);
    setUltraCalls(0);
    setActualCostUsd(0);
    setHypotheticalCostUsd(0);
    setSavingsPercentage(0);
    setExecutionMode("AWAITING");
    setDetectedInfo(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // Use POST /api/migrate/stream proxied by Next.js route handler
    const streamUrl = "/api/migrate/stream";

    try {
      const response = await fetch(streamUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repo_url: activeRepo.trim(),
          target_library: activeLib?.trim() || null,
          target_version: activeVer?.trim() || null,
          build_log: activeBuildLog?.trim() || null,
        }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Server returned HTTP ${response.status}: ${response.statusText}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const evt of events) {
          if (!evt.trim()) continue;

          let eventName = "message";
          let dataStr = "";

          const lines = evt.split("\n");
          for (const line of lines) {
            if (line.startsWith("event:")) {
              eventName = line.replace("event:", "").trim();
            } else if (line.startsWith("data:")) {
              dataStr += line.replace("data:", "").trim();
            }
          }

          if (!dataStr) continue;

          try {
            const data = JSON.parse(dataStr);
            handleSSEEvent(eventName, data);
          } catch (err) {
            console.error("Failed to parse SSE JSON:", err, dataStr);
          }
        }
      }
    } catch (err: any) {
      if (err.name === "AbortError") {
        console.log("Migration stream aborted by user.");
      } else {
        console.error("Stream connection error:", err);
        setIsError(true);
        const rawMsg = String(err?.message || err || "");
        if (rawMsg.toLowerCase().includes("failed to fetch") || rawMsg.toLowerCase().includes("network")) {
          setErrorMessage("Network error: Unable to connect to backend at http://localhost:8000. Please ensure the backend is running.");
        } else {
          setErrorMessage(rawMsg || "Failed to connect to migration stream.");
        }
        setIsRunning(false);
      }
    }
  };

  const handleSSEEvent = (eventName: string, data: any) => {
    const timestamp = Date.now();

    switch (eventName) {
      case "pipeline_init":
        console.log("Pipeline Initialized:", data);
        break;

      case "discovery_start":
        console.log("Autonomous discovery started:", data.message);
        break;

      case "discovery_complete": {
        console.log("Autonomous discovery complete:", data);
        setDetectedInfo({
          library: data.target_library,
          targetVersion: data.target_version,
          sourceVersion: data.source_version,
        });
        break;
      }

      case "stage_start": {
        const stageNum = data.stage;
        setCurrentStage(stageNum);
        setStageTimings((prev) => ({
          ...prev,
          [stageNum]: { startedAt: timestamp },
        }));
        break;
      }

      case "stage1_complete": {
        const report = data as BlastRadiusReport;
        setBlastReport(report);
        if (report.affected_files && report.affected_files.length > 0) {
          setSelectedFilePath(report.affected_files[0].relative_path);
          const superFile = report.affected_files.find((f) =>
            (f.assigned_tier || "").toUpperCase().includes("SUPER")
          );
          if (superFile?.target_model_id || superFile?.assigned_model) {
            setSuperModelLabel(
              (superFile.target_model_id || superFile.assigned_model)!.replace(/^nvidia\//i, "")
            );
          }
          const ultraFile = report.affected_files.find((f) =>
            (f.assigned_tier || "").toUpperCase().includes("ULTRA")
          );
          if (ultraFile?.target_model_id || ultraFile?.assigned_model) {
            setUltraModelLabel(
              (ultraFile.target_model_id || ultraFile.assigned_model)!.replace(/^nvidia\//i, "")
            );
          }
        }
        setStageTimings((prev) => {
          const start = prev[1]?.startedAt || timestamp - report.scan_duration_ms;
          return {
            ...prev,
            1: {
              startedAt: start,
              completedAt: timestamp,
              durationMs: report.scan_duration_ms,
            },
          };
        });
        break;
      }

      case "stage2_complete": {
        const delta = data as MigrationDelta;
        setMigrationDelta(delta);
        setStageTimings((prev) => {
          const start = prev[2]?.startedAt || timestamp - 1100;
          return {
            ...prev,
            2: {
              startedAt: start,
              completedAt: timestamp,
              durationMs: Math.max(100, timestamp - start),
            },
          };
        });
        break;
      }

      case "stage3_file_patched": {
        const patchEvt = data as PatchedFileEvent;
        setPatchedFiles((prev) => {
          const next = new Map(prev);
          next.set(patchEvt.relative_path, patchEvt);
          return next;
        });

        // Update live telemetry accumulators & dynamic model labels
        const tel = patchEvt.telemetry;
        if (tel) {
          const isSuper = (patchEvt.assigned_tier || tel.assigned_tier || tel.tier || "").toUpperCase().includes("SUPER");
          const rawModel = tel.model_used || patchEvt.model_used;
          if (rawModel) {
            const cleanModel = rawModel.replace(/^nvidia\//i, "");
            if (isSuper) {
              setSuperModelLabel(cleanModel);
            } else {
              setUltraModelLabel(cleanModel);
            }
          }
          if (isSuper) {
            setSuperCalls((c) => c + 1);
          } else {
            setUltraCalls((c) => c + 1);
          }
          setActualCostUsd((c) => c + (tel.actual_cost_usd || 0));
          setHypotheticalCostUsd((c) => c + (tel.hypothetical_ultra_cost_usd || 0));
          setExecutionMode(tel.execution_mode || "NEBIUS_TOKEN_FACTORY");
        }
        break;
      }

      case "manifest_conflicts": {
        console.log("Manifest conflicts received:", data);
        setManifestConflicts(data as ManifestConflictsData);
        break;
      }

      case "stage3_complete": {
        const s3 = data as Stage3Report;
        setStage3Report(s3);
        setSuperCalls(s3.super_files_count);
        setUltraCalls(s3.ultra_files_count);
        setActualCostUsd(s3.total_actual_cost_usd);
        setHypotheticalCostUsd(s3.total_hypothetical_ultra_cost_usd);
        setSavingsPercentage(s3.total_cost_savings_pct);

        setStageTimings((prev) => {
          const start = prev[3]?.startedAt || timestamp - s3.total_latency_ms;
          return {
            ...prev,
            3: {
              startedAt: start,
              completedAt: timestamp,
              durationMs: s3.total_latency_ms,
            },
          };
        });
        break;
      }

      case "stage4_complete": {
        const s4 = data as Stage4Report;
        setStage4Report(s4);
        setStageTimings((prev) => {
          const start = prev[4]?.startedAt || timestamp - 1800;
          return {
            ...prev,
            4: {
              startedAt: start,
              completedAt: timestamp,
              durationMs: Math.max(100, timestamp - start),
            },
          };
        });
        // Switch tab to sandbox to showcase verified parity tests!
        setRightPaneTab("sandbox");
        break;
      }

      case "stage5_complete": {
        const s5 = data as Stage5Bundle;
        setStage5Bundle(s5);
        setStageTimings((prev) => {
          const start = prev[5]?.startedAt || timestamp - 200;
          return {
            ...prev,
            5: {
              startedAt: start,
              completedAt: timestamp,
              durationMs: Math.max(50, timestamp - start),
            },
          };
        });
        break;
      }

      case "pipeline_done": {
        setIsRunning(false);
        const finalVer = data.final_verification;
        if (finalVer && finalVer !== "PASSED") {
          setIsCompleted(false);
          setIsError(true);
          const warnMsg = `Verification status: ${finalVer}. Parity tests did not pass 100%.`;
          setErrorMessage(warnMsg);
          setVerificationWarning(warnMsg);
        } else {
          setIsCompleted(true);
          setIsError(false);
          setErrorMessage(null);
          setVerificationWarning(null);
        }
        break;
      }

      case "pipeline_error":
        setIsError(true);
        setErrorMessage(data.error || "An unexpected pipeline error occurred.");
        setIsRunning(false);
        break;

      default:
        console.log("Unhandled SSE event:", eventName, data);
    }
  };

  // Find currently selected file metadata
  const selectedFile: AffectedFile | null =
    blastReport?.affected_files.find(
      (f) => f.relative_path === selectedFilePath
    ) || (selectedFilePath ? {
      relative_path: selectedFilePath,
      normalized_score: 4,
      complexity_score: 4,
      assigned_tier: "SUPER",
      target_model_id: "nvidia/nemotron-4-340b-instruct",
      symbols_detected: ["AppSettings", "BaseSettings", "Field(validation_alias)"],
    } : null);

  const activePatchedData: PatchedFileEvent | null = selectedFilePath
    ? patchedFiles.get(selectedFilePath) || null
    : null;

  // Calculate savings percentage if not yet reported by stage3_complete
  const calculatedSavingsPct =
    savingsPercentage > 0
      ? savingsPercentage
      : hypotheticalCostUsd > 0
      ? Math.round(
          ((hypotheticalCostUsd - actualCostUsd) / hypotheticalCostUsd) * 1000
        ) / 10
      : 0;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-black text-neutral-100 select-none">
      {/* 1. Leftmost Icon Navigation */}
      <SidebarNav
        activeView={activeSidebarView}
        setActiveView={handleSidebarNavChange}
        statusText={
          isRunning
            ? `Stage ${currentStage} Running`
            : isCompleted
            ? "Migration Verified"
            : isError
            ? (verificationWarning ? "Verification Warning" : "Pipeline Error")
            : "Online"
        }
        isStreaming={isRunning}
      />

      {/* 2. Main Center Body */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* A. Top Control Bar */}
        <TopControlBar
          repoUrl={repoUrl}
          setRepoUrl={setRepoUrl}
          targetLibrary={targetLibrary}
          setTargetLibrary={setTargetLibrary}
          targetVersion={targetVersion}
          setTargetVersion={setTargetVersion}
          buildLog={buildLog}
          setBuildLog={setBuildLog}
          detectedInfo={detectedInfo}
          isRunning={isRunning}
          onRunMigration={startMigration}
          onStopMigration={stopMigration}
        />

        {/* B. Live 5-Stage Pipeline Stepper */}
        <PipelineStepper
          currentStage={currentStage}
          isCompleted={isCompleted}
          isError={isError}
          stageTimings={stageTimings}
        />

        {/* C. The Nebius Token Factory Smart Router Telemetry Bar */}
        <TelemetryBar
          superCalls={superCalls}
          ultraCalls={ultraCalls}
          actualCostUsd={actualCostUsd}
          hypotheticalCostUsd={hypotheticalCostUsd}
          savingsPercentage={calculatedSavingsPct}
          executionMode={executionMode}
          superModelLabel={superModelLabel}
          ultraModelLabel={ultraModelLabel}
        />

        {/* Manifest Dependency Conflicts Alert Banner */}
        {manifestConflicts && manifestConflicts.conflicts && manifestConflicts.conflicts.length > 0 && (
          <div className="mx-4 mt-2 p-2.5 rounded-md bg-neutral-900 border border-neutral-700 text-neutral-200 text-xs flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-3.5 h-3.5 text-neutral-400 flex-shrink-0" />
                <span className="font-semibold text-white">
                  Manifest Dependency Conflicts Detected:
                </span>
                <span className="text-[11px] text-neutral-400">
                  ({manifestConflicts.conflicts.length} conflict{manifestConflicts.conflicts.length > 1 ? "s" : ""})
                </span>
              </div>
              <button
                onClick={() => setManifestConflicts(null)}
                className="text-neutral-400 hover:text-white text-xs px-1.5 py-0.5"
              >
                Dismiss
              </button>
            </div>
            <div className="pl-5 space-y-0.5">
              {manifestConflicts.conflicts.map((conflict, idx) => (
                <div key={idx} className="font-mono text-[11px] text-neutral-300 flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-neutral-400 flex-shrink-0" />
                  <span>{conflict}</span>
                </div>
              ))}
            </div>
            {manifestConflicts.companion_packages_added && manifestConflicts.companion_packages_added.length > 0 && (
              <div className="pl-5 text-[10px] text-neutral-500 font-mono">
                Companion packages auto-bumped / added: {manifestConflicts.companion_packages_added.join(", ")}
              </div>
            )}
          </div>
        )}

        {/* Error or Verification Warning Alert Banner if pipeline fails or parity checks do not pass */}
        {isError && errorMessage && (
          <div className="mx-4 mt-2 p-2.5 rounded-md border border-neutral-700 bg-neutral-900 text-xs flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 text-neutral-400" />
              <span className="font-semibold text-white">{verificationWarning ? "Verification Warning:" : "Pipeline Failure:"}</span>
              <span className="text-neutral-300">{errorMessage}</span>
            </div>
            <button
              onClick={() => {
                setIsError(false);
                setVerificationWarning(null);
              }}
              className="text-neutral-400 hover:text-white text-xs px-1.5 py-0.5"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Work Area: Split Pane Command Center (Notion-like Tabs & Trees) */}
        <main className="flex-1 flex min-h-0 relative overflow-hidden">
          {/* Left Pane: Interactive AST Blast-Radius Tree */}
          <div className="w-72 md:w-80 flex-shrink-0 h-full border-r border-neutral-800">
            <BlastRadiusTree
              blastReport={blastReport}
              migrationDelta={migrationDelta}
              patchedFiles={patchedFiles}
              selectedFilePath={selectedFilePath}
              onSelectFile={(path) => {
                setSelectedFilePath(path);
                setRightPaneTab("diff");
              }}
            />
          </div>

          {/* Right Pane: Notion-like Workspace Tabs */}
          <div className="flex-1 flex flex-col h-full min-w-0 bg-black">
            {/* Notion Tab Bar */}
            <div className="flex items-center justify-between border-b border-neutral-800 px-3 bg-[#0a0a0a]">
              <div className="flex items-center gap-1 text-xs">
                <button
                  onClick={() => setRightPaneTab("diff")}
                  className={`py-2 px-3 font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                    rightPaneTab === "diff"
                      ? "border-white text-white font-semibold"
                      : "border-transparent text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  <FileCode className="w-3.5 h-3.5" />
                  <span>Diff Viewer</span>
                  {patchedFiles.size > 0 && (
                    <span className="text-[10px] font-mono px-1 rounded bg-neutral-800 text-neutral-300">
                      {patchedFiles.size}
                    </span>
                  )}
                </button>

                <button
                  onClick={() => setRightPaneTab("sandbox")}
                  className={`py-2 px-3 font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                    rightPaneTab === "sandbox"
                      ? "border-white text-white font-semibold"
                      : "border-transparent text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  <Terminal className="w-3.5 h-3.5" />
                  <span>Sandbox & Parity</span>
                  {stage4Report && (
                    <span className="text-[10px] font-mono px-1 rounded bg-neutral-800 text-neutral-300">
                      {stage4Report.final_status}
                    </span>
                  )}
                </button>

                <button
                  onClick={() => setRightPaneTab("rules")}
                  className={`py-2 px-3 font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                    rightPaneTab === "rules"
                      ? "border-white text-white font-semibold"
                      : "border-transparent text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  <FileCode2 className="w-3.5 h-3.5" />
                  <span>Context Rules</span>
                  {stage5Bundle && (
                    <span className="text-[10px] font-mono px-1 rounded bg-neutral-800 text-neutral-300">
                      Ready
                    </span>
                  )}
                </button>

                <button
                  onClick={() => setRightPaneTab("delta")}
                  className={`py-2 px-3 font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                    rightPaneTab === "delta"
                      ? "border-white text-white font-semibold"
                      : "border-transparent text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  <BookOpen className="w-3.5 h-3.5" />
                  <span>Migration Delta</span>
                  {migrationDelta && (
                    <span className="text-[10px] font-mono px-1 rounded bg-neutral-800 text-neutral-300">
                      {migrationDelta.rules?.length || 0}
                    </span>
                  )}
                </button>

                <button
                  onClick={() => setRightPaneTab("overview")}
                  className={`py-2 px-3 font-medium border-b-2 flex items-center gap-1.5 transition-colors ${
                    rightPaneTab === "overview"
                      ? "border-white text-white font-semibold"
                      : "border-transparent text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  <FileText className="w-3.5 h-3.5" />
                  <span>Overview</span>
                </button>
              </div>
            </div>

            {/* Tab Views */}
            <div className="flex-1 min-h-0 relative">
              {rightPaneTab === "diff" && (
                <DiffViewerTab
                  selectedFile={selectedFile}
                  patchedData={activePatchedData}
                  migrationDelta={migrationDelta}
                />
              )}

              {rightPaneTab === "sandbox" && (
                <SandboxTerminalTab
                  stage4Report={stage4Report}
                  isRunningStage4={currentStage === 4 && isRunning}
                />
              )}

              {rightPaneTab === "rules" && (
                <ContextRulesExportTab
                  stage5Bundle={stage5Bundle}
                  patchedFiles={patchedFiles}
                  repoUrl={repoUrl}
                />
              )}

              {rightPaneTab === "delta" && (
                <TavilyDeltaTab
                  migrationDelta={migrationDelta}
                />
              )}

              {rightPaneTab === "overview" && (
                <OverviewTab
                  repoUrl={repoUrl}
                  detectedInfo={detectedInfo}
                  isRunning={isRunning}
                  isCompleted={isCompleted}
                  onSelectFile={(path) => {
                    setSelectedFilePath(path);
                    setRightPaneTab("diff");
                  }}
                />
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
