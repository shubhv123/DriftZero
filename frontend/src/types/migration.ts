export interface SymbolUsage {
  symbol_name: string;
  usage_type: string;
  line_number: number;
  code_snippet: string;
  complexity_weight: number;
  deprecation_hint: string;
}

export interface AffectedFile {
  file_path?: string;
  relative_path: string;
  total_lines?: number;
  normalized_score?: number;
  complexity_score?: number;
  raw_complexity_score?: number;
  assigned_tier: string;
  target_model_id?: string;
  assigned_model?: string;
  imports_found?: string[];
  routing_reasons?: string[];
  ast_reasoning?: string;
  source_code?: string;
  usages?: SymbolUsage[];
  symbols_detected?: string[];
  decorators_detected?: string[];
}

export interface BlastRadiusReport {
  repo_name?: string;
  repo_path?: string;
  target_library: string;
  old_version?: string;
  from_version?: string;
  target_version: string;
  to_version?: string;
  total_files_scanned: number;
  affected_files_count: number;
  scan_duration_ms: number;
  affected_files: AffectedFile[];
}

export interface MigrationRule {
  symbol?: string;
  category?: string;
  old_syntax: string;
  new_syntax: string;
  explanation?: string;
  breaking_gotcha?: string;
  required_imports?: string[];
  source_url?: string;
  severity?: "BREAKING" | "DEPRECATED" | "INFO";
  silent_drift_risk?: boolean;
}

export interface MigrationDelta {
  library: string;
  old_version?: string;
  from_version?: string;
  target_version?: string;
  to_version?: string;
  tavily_queries_executed?: string[];
  citations: string[];
  rules: MigrationRule[];
  silent_drift_warnings?: string[];
  synthesized_by_model?: string;
  generation_latency_ms?: number;
  uncovered_symbols?: string[];
}

export interface FileTelemetry {
  assigned_tier?: string;
  tier?: string;
  model_used?: string;
  model?: string;
  complexity_score?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  latency_ms: number;
  actual_cost_usd: number;
  hypothetical_ultra_cost_usd: number;
  cost_saved_usd?: number;
  execution_mode?: "LIVE_NEBIUS" | "DETERMINISTIC_FALLBACK";
}

export interface PatchedFileEvent {
  relative_path: string;
  assigned_tier: string;
  model_used: string;
  original_code?: string;
  unified_diff: string;
  patched_code: string;
  telemetry: FileTelemetry;
}

export interface Stage3Report {
  total_files_patched: number;
  super_files_count: number;
  ultra_files_count: number;
  total_latency_ms: number;
  total_actual_cost_usd: number;
  total_hypothetical_ultra_cost_usd: number;
  total_cost_savings_pct: number;
  patches: PatchedFileEvent[];
}

export interface HealingAction {
  target_file: string;
  error_pattern: string;
  action_summary: string;
  healed_by_model: string;
}

export interface VerificationAttempt {
  attempt_number: number;
  job_id: string;
  execution_target?: string;
  exit_code: number;
  passed_count: number;
  failed_count: number;
  duration_ms?: number;
  stdout?: string;
  stdout_log?: string;
  stderr?: string;
  stderr_traceback?: string;
  healed_file?: string | null;
  healed_files?: string[];
  healed_by_model?: string | null;
  healing_actions?: HealingAction[];
}

export interface Stage4Report {
  test_file_path?: string;
  total_attempts: number;
  final_status: "PASSED" | "FAILED" | string;
  self_healing_triggered?: boolean;
  attempts: VerificationAttempt[];
}

export interface Stage5Bundle {
  cursorrules_path: string;
  claude_md_path: string;
  cursorrules_content: string;
  claude_md_content: string;
  pr_url?: string | null;
  pr_branch_name: string;
  pr_title?: string;
  status?: string;
  manifest_filename?: string;
  manifest_content?: string;
  parity_test_content?: string;
}

export type PipelineStage = 1 | 2 | 3 | 4 | 5;

export interface StageTiming {
  startedAt?: number;
  completedAt?: number;
  durationMs?: number;
}
