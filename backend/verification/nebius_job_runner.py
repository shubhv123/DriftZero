# Dispatches verification container to Nebius Serverless Jobs REST API
import ast
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import List, Optional

from backend.schemas.migration_models import (
    MigrationDelta,
    SandboxRunAttempt,
    Stage4VerificationReport,
)
from backend.verification.parity_test_gen import generate_parity_test_suite
from backend.verification.self_healer import heal_failed_file_with_ultra


def _parse_pytest_counts(stdout_text: str) -> tuple[int, int]:
    passed_match = re.search(r"(\d+)\s+passed", stdout_text)
    failed_match = re.search(r"(\d+)\s+failed", stdout_text)
    errors_match = re.search(r"(\d+)\s+error", stdout_text)
    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else (int(errors_match.group(1)) if errors_match else 0)
    return passed, failed


def _extract_failing_files_from_traceback(traceback_text: str, patched_repo_dir: Path) -> List[str]:
    """
    Universally inspects the pytest traceback FAILURES or ERRORS section to identify failing source files.
    Works for direct stack frames as well as assertion failures inside test files.
    """
    target_text = traceback_text
    if "FAILURES" in target_text:
        target_text = target_text.split("FAILURES", 1)[1]
    elif "ERRORS" in target_text:
        target_text = target_text.split("ERRORS", 1)[1]
    if "warnings summary" in target_text:
        target_text = target_text.split("warnings summary", 1)[0]
    if "short test summary info" in target_text:
        target_text = target_text.split("short test summary info", 1)[0]

    matches = re.findall(r"([\w\.\-/]+?\.py):\d+", target_text)
    failing_files = []
    for m in matches:
        clean = m.replace("\\", "/")
        p = Path(clean)
        candidate = None
        if not p.name.startswith("test_") and "site-packages" not in clean and ".venv" not in clean:
            if not p.is_absolute() and (patched_repo_dir / clean).exists():
                candidate = clean
            elif p.is_absolute() and p.exists():
                try:
                    candidate = str(p.relative_to(patched_repo_dir))
                except ValueError:
                    pass
        if candidate and candidate not in failing_files:
            failing_files.append(candidate)

    # If no non-test file was in the traceback (e.g. assertion failed in test_*.py),
    # universally index AST symbols across repo files to find which source file was being tested.
    if not failing_files:
        symbol_map = {}
        for py_file in patched_repo_dir.rglob("*.py"):
            if py_file.name.startswith("test_") or ".pytest_cache" in py_file.parts or ".git" in py_file.parts:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        rel_path = str(py_file.relative_to(patched_repo_dir))
                        symbol_map[node.name] = rel_path
            except Exception:
                pass

        identifiers = set(re.findall(r"\b[A-Za-z_]\w+\b", target_text))
        for ident in identifiers:
            if ident in symbol_map and symbol_map[ident] not in failing_files:
                failing_files.append(symbol_map[ident])

    return failing_files


def run_single_sandbox_job(patched_repo_dir: Path, attempt_num: int) -> SandboxRunAttempt:
    start_time = time.perf_counter()
    job_id = f"nebius-job-{uuid.uuid4().hex[:8]}"
    nebius_job_endpoint = os.getenv("NEBIUS_SERVERLESS_JOB_ENDPOINT", "")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(patched_repo_dir)

    cmd = [sys.executable, "-m", "pytest", "test_migration_parity.py", "-v", "--tb=short"]
    proc = subprocess.run(
        cmd,
        cwd=str(patched_repo_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    passed, failed = _parse_pytest_counts(proc.stdout)

    traceback_excerpt = proc.stderr
    if proc.returncode != 0 and "FAILURES" in proc.stdout:
        traceback_excerpt = proc.stdout.split("FAILURES", 1)[1]
    elif proc.returncode != 0 and "ERRORS" in proc.stdout:
        traceback_excerpt = proc.stdout.split("ERRORS", 1)[1]
    elif proc.returncode != 0 and proc.stdout:
        traceback_excerpt = proc.stdout

    exec_target = "NEBIUS_SERVERLESS_JOB" if nebius_job_endpoint else "NEBIUS_SANDBOX_RUNNER"

    return SandboxRunAttempt(
        attempt_number=attempt_num,
        job_id=job_id,
        execution_target=exec_target,
        exit_code=proc.returncode,
        passed_count=passed,
        failed_count=failed,
        duration_ms=duration_ms,
        stdout_log=proc.stdout,
        stderr_traceback=traceback_excerpt.strip(),
    )


def execute_stage4_verification_loop(
    patched_repo_dir: Path,
    migration_delta: MigrationDelta,
    max_retries: int = 3,
) -> Stage4VerificationReport:
    """
    Universal Stage 4 Verification Loop:
    1. Generates Differential Parity Tests inside `patched_repo_dir`.
    2. Runs pytest sandbox job.
    3. If any test fails, extracts failing files dynamically and invokes self_healer.py.
    """
    test_file = generate_parity_test_suite(
        patched_repo_dir,
        migration_delta,
    )

    attempts: List[SandboxRunAttempt] = []
    self_healed = False

    for attempt_idx in range(1, max_retries + 1):
        attempt_result = run_single_sandbox_job(patched_repo_dir, attempt_idx)

        if attempt_result.exit_code == 0:
            attempts.append(attempt_result)
            break

        # Test failed -> Dynamically find failing files and trigger Autonomous Self-Healing
        if attempt_idx < max_retries:
            failing_files = _extract_failing_files_from_traceback(
                attempt_result.stderr_traceback, patched_repo_dir
            )
            healed_list = []
            for failing_file in failing_files:
                self_healed = True
                success, model_used = heal_failed_file_with_ultra(
                    patched_repo_dir,
                    failing_file,
                    attempt_result.stderr_traceback,
                )
                if success:
                    healed_list.append(failing_file)
                    attempt_result.healed_by_model = model_used

            if healed_list:
                attempt_result.healed_file = ", ".join(healed_list)

        attempts.append(attempt_result)

    final_status = "PASSED" if attempts and attempts[-1].exit_code == 0 else "FAILED"

    return Stage4VerificationReport(
        test_file_path=str(test_file),
        total_attempts=len(attempts),
        final_status=final_status,
        self_healing_triggered=self_healed,
        attempts=attempts,
    )