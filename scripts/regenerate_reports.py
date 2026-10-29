import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Ensure unsafe local process sandbox is enabled when running script locally without Docker
os.environ["ALLOW_UNSAFE_LOCAL_SANDBOX"] = "1"

from backend.config import PROJECT_ROOT
from backend.core.ast_scanner import scan_repository
from backend.core.codemods import partition_files_for_stage3
from backend.core.repo_manager import update_manifest_version
from backend.intelligence.tavily_delta_engine import build_migration_delta
from backend.intelligence.telemetry_tracker import PipelineTelemetrySummary
from backend.intelligence.token_factory_router import (
    patch_single_file_via_token_factory,
)
from backend.schemas.migration_models import Stage3MigrationReport
from backend.verification.nebius_job_runner import execute_stage4_verification_loop


def main():
    repo_path = str(PROJECT_ROOT / "demo_target_repo")
    target_lib = "pydantic"
    target_ver = "2.9.2"

    print(f"1. Scanning {repo_path} for {target_lib} -> {target_ver}...")
    blast = scan_repository(repo_path, target_lib, target_ver)
    print(f"   -> Scanned repository ({len(blast.affected_files)} affected files)")

    print("2. Synthesizing Tavily Migration Delta...")
    delta = build_migration_delta(blast)
    print(f"   -> Synthesized delta ({len(delta.rules)} rules)")

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

    # Fail-fast if any patch failed
    failed_patches = [p for p in patches if p.status != "SUCCESS"]
    if failed_patches:
        print(f"❌ {len(failed_patches)} Stage 3 patch(es) failed! Refusing to write reports.")
        sys.exit(1)

    s3_report = Stage3MigrationReport(
        total_files_patched=len([p for p in patches if p.status == "SUCCESS"]),
        codemod_files_count=telemetry.codemod_calls,
        super_files_count=telemetry.super_calls,
        ultra_files_count=telemetry.ultra_calls,
        total_latency_ms=round(telemetry.total_latency_ms, 2),
        total_actual_cost_usd=round(telemetry.total_actual_cost_usd, 6),
        total_hypothetical_ultra_cost_usd=round(telemetry.total_hypothetical_ultra_cost_usd, 6),
        total_cost_savings_pct=telemetry.savings_percentage,
        patches=patches,
    )

    print("4. Executing Stage 4 Parity Verification...")
    with tempfile.TemporaryDirectory(prefix="driftzero_regen_stage4_") as tmp_ws_str:
        patched_workspace = Path(tmp_ws_str) / "demo_target_repo"
        shutil.copytree(repo_path, patched_workspace)

        # Apply patched files
        for p in patches:
            if p.status == "SUCCESS" and p.patched_code:
                out_file = patched_workspace / p.relative_path
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(p.patched_code, encoding="utf-8")

        # Update manifest files
        manifest_res = update_manifest_version(patched_workspace, target_lib, target_ver)
        print(f"   -> Manifests updated: {manifest_res.manifests_updated}")

        baseline_workspace = Path(tmp_ws_str) / "baseline_demo_target_repo"
        shutil.copytree(repo_path, baseline_workspace)

        try:
            stage4_report = execute_stage4_verification_loop(
                patched_repo_dir=patched_workspace,
                migration_delta=delta,
                max_retries=3,
                baseline_repo_dir=baseline_workspace,
            )
        finally:
            # Guarantee baseline demo_target_repo remains completely clean
            for tmp_name in ("test_migration_parity.py", "golden.json"):
                tmp_f = Path(repo_path) / tmp_name
                if tmp_f.exists():
                    try:
                        tmp_f.unlink()
                    except Exception:
                        pass

        print(f"   -> Stage 4 Verification Status: {stage4_report.final_status} ({stage4_report.total_attempts} attempts)")

        if stage4_report.final_status != "PASSED":
            print(f"❌ Stage 4 Parity Verification failed with status {stage4_report.final_status}! Reports will NOT be committed.")
            for i, att in enumerate(stage4_report.attempts, 1):
                print(f"\n--- Attempt {i} [Target: {att.execution_target}] Exit: {att.exit_code} ({att.passed_count} passed, {att.failed_count} failed) ---")
                if att.stderr_traceback:
                    print(f"Traceback:\n{att.stderr_traceback[:2000]}")
                if att.stdout_log:
                    print(f"Stdout:\n{att.stdout_log[:2000]}")
            sys.exit(1)

        # If self-healer repaired any files, sync the healed code into s3_report
        if stage4_report.self_healing_triggered:
            for attempt in stage4_report.attempts:
                if attempt.healed_file and attempt.exit_code == 0:
                    for healed_rel in attempt.healed_file.split(","):
                        clean_rel = healed_rel.strip()
                        healed_path = patched_workspace / clean_rel
                        if healed_path.exists():
                            for patch in s3_report.patches:
                                if patch.relative_path == clean_rel:
                                    patch.patched_code = healed_path.read_text(encoding="utf-8")
                                    print(f"   ✨ Synced self-healed code for '{clean_rel}' into Stage 3 report")

    # Write the reports only after Stage 4 has PASSED!
    blast_json_path = PROJECT_ROOT / "BlastRadiusReport.json"
    blast_json_path.write_text(blast.to_json(indent=2), encoding="utf-8")
    print(f"   -> Wrote {blast_json_path} ({len(blast.affected_files)} affected files)")

    delta_json_path = PROJECT_ROOT / "MigrationDelta.json"
    delta_json_path.write_text(delta.to_json(indent=2), encoding="utf-8")
    print(f"   -> Wrote {delta_json_path} ({len(delta.rules)} rules)")

    s3_json_path = PROJECT_ROOT / "Stage3MigrationReport.json"
    s3_json_path.write_text(s3_report.to_json(indent=2), encoding="utf-8")
    print(f"   -> Wrote {s3_json_path} ({s3_report.total_files_patched} files patched, {s3_report.total_cost_savings_pct}% token savings)")

    print("\n✅ All 3 JSON reports verified against Stage 4 and regenerated successfully!")


if __name__ == "__main__":
    main()
