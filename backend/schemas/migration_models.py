# BlastRadiusReport, MigrationDelta, FilePatch, TelemetryEvent
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
import json

@dataclass
class SymbolUsage:
    symbol_name: str
    usage_type: str
    line_number: int
    code_snippet: str
    complexity_weight: int
    deprecation_hint: str

@dataclass
class AffectedFileReport:
    file_path: str
    relative_path: str
    total_lines: int
    imports_found: List[str]
    usages: List[SymbolUsage]
    raw_complexity_score: int
    normalized_score: int
    assigned_tier: str
    target_model_id: str
    routing_reasons: List[str]
    source_code: Optional[str] = None

@dataclass
class BlastRadiusReport:
    repo_path: str
    target_library: str
    old_version: Optional[str]
    target_version: str
    scan_duration_ms: float
    total_files_scanned: int
    affected_files_count: int
    super_routed_count: int
    ultra_routed_count: int
    estimated_cost_savings_pct: float
    unique_deprecated_symbols: List[str]
    tavily_search_queries: List[str]
    affected_files: List[AffectedFileReport] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

@dataclass
class MigrationRule:
    symbol: str
    old_syntax: str
    new_syntax: str
    required_imports: List[str]
    breaking_gotcha: str
    source_url: str

@dataclass
class MigrationDelta:
    library: str
    from_version: str
    to_version: str
    tavily_queries_executed: List[str]
    citations: List[str]
    rules: List[MigrationRule]
    synthesized_by_model: str
    generation_latency_ms: float
    uncovered_symbols: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

@dataclass
class TelemetryEvent:
    file_path: str
    assigned_tier: str
    model_used: str
    complexity_score: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    actual_cost_usd: float
    hypothetical_ultra_cost_usd: float
    cost_saved_usd: float
    execution_mode: str = "NEBIUS_TOKEN_FACTORY"

@dataclass
class FilePatchResult:
    relative_path: str
    original_code: str
    patched_code: str
    unified_diff: str
    assigned_tier: str
    model_used: str
    syntax_valid: bool
    telemetry: TelemetryEvent
    status: str = "SUCCESS"  # "SUCCESS" or "FAILED"
    error_message: Optional[str] = None

@dataclass
class Stage3MigrationReport:
    total_files_patched: int
    super_files_count: int
    ultra_files_count: int
    total_latency_ms: float
    total_actual_cost_usd: float
    total_hypothetical_ultra_cost_usd: float
    total_cost_savings_pct: float
    patches: List[FilePatchResult] = field(default_factory=list)
    codemod_files_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

# --- APPEND THESE TO THE BOTTOM OF backend/schemas/migration_models.py ---

@dataclass
class SandboxRunAttempt:
    attempt_number: int
    job_id: str
    execution_target: str  # "NEBIUS_SERVERLESS_JOB" or "LOCAL_SANDBOX_CONTAINER"
    exit_code: int
    passed_count: int
    failed_count: int
    duration_ms: float
    stdout_log: str
    stderr_traceback: str
    healed_file: Optional[str] = None
    healed_by_model: Optional[str] = None


@dataclass
class Stage4VerificationReport:
    test_file_path: str
    total_attempts: int
    final_status: str  # "PASSED" or "FAILED"
    self_healing_triggered: bool
    attempts: List[SandboxRunAttempt] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass
class Stage5ExportBundle:
    cursorrules_path: str
    claude_md_path: str
    cursorrules_content: str
    claude_md_content: str
    pr_branch_name: str
    pr_title: str
    pr_url: Optional[str]
    status: str
    manifest_filename: Optional[str] = None
    manifest_content: Optional[str] = None
    parity_test_content: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)