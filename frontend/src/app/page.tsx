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
import { WelcomeOrbView } from "../components/WelcomeOrbView";
import {
  AffectedFile,
  BlastRadiusReport,
  MigrationDelta,
  PatchedFileEvent,
  Stage3Report,
  Stage4Report,
  Stage5Bundle,
  StageTiming,
} from "../types/migration";
import { FileCode, Terminal, FileCode2, AlertCircle } from "lucide-react";

export default function DriftZeroCommandCenter() {
  // Input parameters (Autonomous Discovery Engine handles library & version detection)
  const [repoUrl, setRepoUrl] = useState<string>("https://github.com/shubhv123/pdf-rag");

  // Navigation & Workspace tabs
  const [activeSidebarView, setActiveSidebarView] = useState<
    "workspace" | "rules" | "terminal"
  >("workspace");
  const [rightPaneTab, setRightPaneTab] = useState<
    "diff" | "sandbox" | "rules"
  >("diff");

  // Pipeline Execution State
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [currentStage, setCurrentStage] = useState<number>(0);
  const [isCompleted, setIsCompleted] = useState<boolean>(false);
  const [isError, setIsError] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
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

  // Telemetry Aggregates
  const [superCalls, setSuperCalls] = useState<number>(0);
  const [ultraCalls, setUltraCalls] = useState<number>(0);
  const [actualCostUsd, setActualCostUsd] = useState<number>(0);
  const [hypotheticalCostUsd, setHypotheticalCostUsd] = useState<number>(0);
  const [savingsPercentage, setSavingsPercentage] = useState<number>(0);
  const [executionMode, setExecutionMode] = useState<
    "LIVE_NEBIUS" | "DETERMINISTIC_FALLBACK" | "AWAITING"
  >("AWAITING");

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

  const startMigration = async (customRepo?: string) => {
    // Cancel any existing run
    stopMigration();

    const activeRepo = customRepo ?? repoUrl;

    if (!activeRepo.trim()) {
      alert("Please provide a GitHub repository URL or directory path.");
      return;
    }

    // Reset pipeline state
    setIsRunning(true);
    setIsCompleted(false);
    setIsError(false);
    setErrorMessage(null);
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

    // Use relative URL by default so Next.js reverse-proxies directly to backend
    const baseApi = process.env.NEXT_PUBLIC_API_URL || "";
    const streamUrl = `${baseApi}/api/migrate/stream?repo_url=${encodeURIComponent(
      activeRepo.trim()
    )}`;

    try {
      const response = await fetch(streamUrl, {
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

        // Update live telemetry accumulators
        const tel = patchEvt.telemetry;
        if (tel) {
          const isSuper = (patchEvt.assigned_tier || tel.assigned_tier || tel.tier || "").toUpperCase().includes("SUPER");
          if (isSuper) {
            setSuperCalls((c) => c + 1);
          } else {
            setUltraCalls((c) => c + 1);
          }
          setActualCostUsd((c) => c + (tel.actual_cost_usd || 0));
          setHypotheticalCostUsd((c) => c + (tel.hypothetical_ultra_cost_usd || 0));
          setExecutionMode(tel.execution_mode || "LIVE_NEBIUS");
        }
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

      case "pipeline_done":
        setIsCompleted(true);
        setIsRunning(false);
        break;

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
    ) || null;

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
    <div className="flex h-screen w-screen overflow-hidden bg-[#070b13] text-slate-100">
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
            ? "Pipeline Error"
            : "Online"
        }
        isStreaming={isRunning}
      />

      {/* 2. Main Center Body */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* A. Top Control Bar (Autonomous Repo Input + 1-Click Judge Mode) */}
        <TopControlBar
          repoUrl={repoUrl}
          setRepoUrl={setRepoUrl}
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
        />

        {/* Error Alert Banner if pipeline fails */}
        {isError && errorMessage && (
          <div className="mx-4 mt-2 p-3 rounded-xl bg-rose-950/40 border border-rose-500/50 text-rose-300 text-xs flex items-center justify-between shadow-lg">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
              <span className="font-semibold">Pipeline Failure:</span>
              <span>{errorMessage}</span>
            </div>
            <button
              onClick={() => setIsError(false)}
              className="text-slate-400 hover:text-white text-xs px-2 py-0.5"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Work Area: Split Pane Command Center */}
        <main className="flex-1 flex min-h-0 relative overflow-hidden">
          {/* If completely idle with no data, show high-aesthetic WelcomeOrbView */}
          {currentStage === 0 && !blastReport && !isRunning && !isError ? (
            <WelcomeOrbView
              onQuickSelect={(repo) => {
                setRepoUrl(repo);
                startMigration(repo);
              }}
              onLaunchJudgeDemo={() => {
                const demo = "./demo_target_repo";
                setRepoUrl(demo);
                startMigration(demo);
              }}
            />
          ) : (
            <>
              {/* D. Left Pane: Interactive AST Blast-Radius Tree & Tavily Drawer */}
              <div className="w-80 md:w-96 flex-shrink-0 h-full border-r border-slate-800/80">
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

              {/* E. Right Pane: 3-Tab Engineering Workspace */}
              <div className="flex-1 flex flex-col h-full min-w-0 bg-[#060912]">
                {/* 3-Tab Workspace Header Bar */}
                <div className="flex items-center justify-between border-b border-slate-800/80 px-4 bg-slate-900/40">
                  <div className="flex items-center gap-1 text-xs">
                    <button
                      onClick={() => setRightPaneTab("diff")}
                      className={`py-2.5 px-3.5 font-semibold border-b-2 flex items-center gap-1.5 transition-all ${
                        rightPaneTab === "diff"
                          ? "border-blue-500 text-blue-400"
                          : "border-transparent text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <FileCode className="w-3.5 h-3.5" />
                      <span>Side-by-Side Monaco Diff</span>
                      {patchedFiles.size > 0 && (
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-blue-500/10 text-blue-300">
                          {patchedFiles.size}
                        </span>
                      )}
                    </button>

                    <button
                      onClick={() => setRightPaneTab("sandbox")}
                      className={`py-2.5 px-3.5 font-semibold border-b-2 flex items-center gap-1.5 transition-all ${
                        rightPaneTab === "sandbox"
                          ? "border-emerald-500 text-emerald-400"
                          : "border-transparent text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <Terminal className="w-3.5 h-3.5" />
                      <span>Nebius Sandbox & Self-Healing</span>
                      {stage4Report && (
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/15 text-emerald-300">
                          {stage4Report.final_status}
                        </span>
                      )}
                    </button>

                    <button
                      onClick={() => setRightPaneTab("rules")}
                      className={`py-2.5 px-3.5 font-semibold border-b-2 flex items-center gap-1.5 transition-all ${
                        rightPaneTab === "rules"
                          ? "border-indigo-500 text-indigo-400"
                          : "border-transparent text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <FileCode2 className="w-3.5 h-3.5" />
                      <span>Context Rules Export (.cursorrules)</span>
                      {stage5Bundle && (
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-indigo-500/15 text-indigo-300">
                          Ready
                        </span>
                      )}
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
                </div>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
