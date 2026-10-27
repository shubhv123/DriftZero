import os
import json
from pathlib import Path

from backend.config import PROJECT_ROOT
from backend.core.ast_scanner import scan_repository
from backend.intelligence.tavily_delta_engine import build_migration_delta
from backend.core.codemods import partition_files_for_stage3
from backend.intelligence.token_factory_router import (
    patch_single_file_via_token_factory,
)
from backend.intelligence.telemetry_tracker import PipelineTelemetrySummary
from backend.schemas.migration_models import Stage3MigrationReport

def main():
    repo_path = str(PROJECT_ROOT / "demo_target_repo")
    target_lib = "pydantic"
    target_ver = "2.9.2"
    
    print(f"1. Scanning {repo_path} for {target_lib} -> {target_ver}...")
    blast = scan_repository(repo_path, target_lib, target_ver)
    blast_json_path = PROJECT_ROOT / "BlastRadiusReport.json"
    blast_json_path.write_text(blast.to_json(indent=2), encoding="utf-8")
    print(f"   -> Wrote {blast_json_path} ({len(blast.affected_files)} affected files)")
    
    print("2. Synthesizing Tavily Migration Delta...")
    delta = build_migration_delta(blast)
    delta_json_path = PROJECT_ROOT / "MigrationDelta.json"
    delta_json_path.write_text(delta.to_json(indent=2), encoding="utf-8")
    print(f"   -> Wrote {delta_json_path} ({len(delta.rules)} rules)")
    
    print("3. Executing Stage 3: LibCST Codemods + Token Factory Smart Router...")
    telemetry = PipelineTelemetrySummary()
    patches = []
    resolved_patches, residual_files = partition_files_for_stage3(blast, delta)
    
    for p in resolved_patches:
        patches.append(p)
        telemetry.record_event(p.telemetry)
        print(f"   [LibCST Codemod] {p.relative_path}: {p.status}")
        
    for f in residual_files:
        p = patch_single_file_via_token_factory(f, delta)
        patches.append(p)
        telemetry.record_event(p.telemetry)
        print(f"   [Nemotron Token Factory] {p.relative_path}: {p.status} ({p.model_used})")
        
    s3_report = Stage3MigrationReport(
        total_files_patched=len(patches),
        super_files_count=telemetry.super_calls,
        ultra_files_count=telemetry.ultra_calls,
        total_latency_ms=round(telemetry.total_latency_ms, 2),
        total_actual_cost_usd=round(telemetry.total_actual_cost_usd, 6),
        total_hypothetical_ultra_cost_usd=round(telemetry.total_hypothetical_ultra_cost_usd, 6),
        total_cost_savings_pct=telemetry.savings_percentage,
        patches=patches,
    )
    s3_json_path = PROJECT_ROOT / "Stage3MigrationReport.json"
    s3_json_path.write_text(s3_report.to_json(indent=2), encoding="utf-8")
    print(f"   -> Wrote {s3_json_path} ({s3_report.total_files_patched} files patched, {s3_report.total_cost_savings_pct}% token savings)")
    print("\n✅ All 3 JSON reports regenerated successfully!")

if __name__ == "__main__":
    main()
