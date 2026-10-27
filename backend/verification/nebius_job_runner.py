# Dispatches verification container to Nebius Serverless Jobs REST API or Local Docker Sandbox
import ast
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.schemas.migration_models import (
    MigrationDelta,
    SandboxRunAttempt,
    Stage4VerificationReport,
)
from backend.verification.parity_test_gen import generate_parity_test_suite
from backend.verification.self_healer import heal_failed_file_with_ultra


def _parse_pytest_counts(stdout_text: str) -> Tuple[int, int]:
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


def _run_nebius_serverless_job(
    repo_dir: Path,
    job_id: str,
    attempt_num: int,
    endpoint: str,
    target_label: str = "PATCHED",
    timeout: int = 45,
    extra_env: Optional[Dict[str, str]] = None,
) -> Optional[SandboxRunAttempt]:
    """
    Submits container job to Nebius Serverless Jobs API when NEBIUS_SERVERLESS_JOB_ENDPOINT is set.
    Only returns an attempt labeled NEBIUS when a job was actually submitted.
    """
    start_time = time.perf_counter()
    files_payload = {}
    for p in repo_dir.rglob("*"):
        if p.is_file() and not p.name.startswith(".") and "__pycache__" not in p.parts:
            try:
                rel = str(p.relative_to(repo_dir))
                files_payload[rel] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    req_data = {
        "job_id": job_id,
        "target": target_label,
        "image": "cr.nemotron.nebius.ai/driftzero/sandbox:latest",
        "limits": {"cpu": "2", "memory": "2Gi"},
        "network": "none",
        "command": ["pytest", "test_migration_parity.py", "-v", "--tb=short"],
        "files": files_payload,
        "timeout": timeout,
        "env": extra_env or {},
    }

    headers = {"Content-Type": "application/json"}
    nebius_key = os.getenv("NEBIUS_API_KEY", "")
    if nebius_key:
        headers["Authorization"] = f"Bearer {nebius_key}"

    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(req_data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            exit_code = int(res_json.get("exit_code", 0))
            stdout_log = str(res_json.get("stdout", ""))
            stderr_log = str(res_json.get("stderr", ""))
            passed, failed = _parse_pytest_counts(stdout_log)

            print(f"  🌐 [LIVE NEBIUS SERVERLESS JOB] Submitted {job_id} ({target_label}) -> Exit {exit_code}", flush=True)
            return SandboxRunAttempt(
                attempt_number=attempt_num,
                job_id=job_id,
                execution_target="NEBIUS_SERVERLESS_JOB",  # Only labeled NEBIUS when actually submitted
                exit_code=exit_code,
                passed_count=passed,
                failed_count=failed,
                duration_ms=duration_ms,
                stdout_log=f"[{target_label} Container - NEBIUS SERVERLESS JOB (network: none, cpus: 2, mem: 2Gi)]\n{stdout_log}",
                stderr_traceback=stderr_log.strip(),
            )
    except urllib.error.HTTPError as exc:
        print(f"  ⚠️ [Nebius Serverless Jobs API] HTTP {exc.code} for {endpoint}: {exc.reason}", flush=True)
        return None
    except Exception as exc:
        print(f"  ⚠️ [Nebius Serverless Jobs API] Submission notice: {exc}", flush=True)
        return None


def _run_docker_container(
    repo_dir: Path,
    job_id: str,
    attempt_num: int,
    target_label: str = "PATCHED",
    timeout: int = 45,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunAttempt:
    """
    Runs Docker container locally using sandbox_runner/Dockerfile.sandbox.
    Installs repo requirements inside container during image build.
    Runs with --network none, CPU limit 2.0, memory limit 2g.
    Catches TimeoutExpired and returns failed attempt.
    """
    start_time = time.perf_counter()
    exec_target = "LOCAL_DOCKER_SANDBOX"

    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise FileNotFoundError("Docker executable not found on host system.")

    project_root = Path(__file__).resolve().parents[2]
    dockerfile_path = project_root / "sandbox_runner" / "Dockerfile.sandbox"
    if not dockerfile_path.exists():
        dockerfile_path = Path("sandbox_runner/Dockerfile.sandbox").resolve()

    image_tag = f"driftzero-sandbox-{job_id.lower()}"

    # 1. Build container image using sandbox_runner/Dockerfile.sandbox
    # Requirements are installed inside the container at build time
    build_cmd = [
        docker_bin, "build",
        "-f", str(dockerfile_path),
        "-t", image_tag,
        "--build-arg", "TARGET_DIR=.",
        str(repo_dir),
    ]
    try:
        build_proc = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if build_proc.returncode != 0:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return SandboxRunAttempt(
                attempt_number=attempt_num,
                job_id=job_id,
                execution_target=exec_target,
                exit_code=build_proc.returncode,
                passed_count=0,
                failed_count=1,
                duration_ms=duration_ms,
                stdout_log=build_proc.stdout,
                stderr_traceback=f"Container build failed:\n{build_proc.stderr}".strip(),
            )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = exc.stdout or ""
        err = exc.stderr or ""
        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=124,
            passed_count=0,
            failed_count=1,
            duration_ms=duration_ms,
            stdout_log=out if isinstance(out, str) else out.decode("utf-8", errors="replace"),
            stderr_traceback=f"Container image build timed out after 120s.\n{err}".strip(),
        )

    # 2. Run container: no network after install (--network none), CPU/memory limits
    run_cmd = [
        docker_bin, "run", "--rm",
        "--network", "none",
        "--cpus", "2.0",
        "--memory", "2g",
    ]
    if extra_env:
        for k, v in extra_env.items():
            run_cmd.extend(["-e", f"{k}={v}"])
    run_cmd.extend([
        image_tag,
        "test_migration_parity.py", "-v", "--tb=short",
    ])

    try:
        run_proc = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        passed, failed = _parse_pytest_counts(run_proc.stdout)
        traceback_excerpt = run_proc.stderr
        if run_proc.returncode != 0 and "FAILURES" in run_proc.stdout:
            traceback_excerpt = run_proc.stdout.split("FAILURES", 1)[1]
        elif run_proc.returncode != 0 and "ERRORS" in run_proc.stdout:
            traceback_excerpt = run_proc.stdout.split("ERRORS", 1)[1]
        elif run_proc.returncode != 0 and run_proc.stdout:
            traceback_excerpt = run_proc.stdout

        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=run_proc.returncode,
            passed_count=passed,
            failed_count=failed,
            duration_ms=duration_ms,
            stdout_log=f"[{target_label} Container - DOCKER SANDBOX (network: none, cpus: 2, mem: 2g)]\n{run_proc.stdout}",
            stderr_traceback=traceback_excerpt.strip(),
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = exc.stdout or ""
        err = exc.stderr or ""
        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=124,
            passed_count=0,
            failed_count=1,
            duration_ms=duration_ms,
            stdout_log=out if isinstance(out, str) else out.decode("utf-8", errors="replace"),
            stderr_traceback=f"Execution timed out after {timeout} seconds: Container exceeded execution deadline.\n{err}".strip(),
        )
    finally:
        # Cleanup ephemeral container image
        subprocess.run([docker_bin, "rmi", "-f", image_tag], capture_output=True)


def _run_local_process_sandbox(
    repo_dir: Path,
    job_id: str,
    attempt_num: int,
    target_label: str = "PATCHED",
    timeout: int = 45,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunAttempt:
    """
    Fallback runner when local Docker daemon is not active.
    Applies timeout and catches TimeoutExpired.
    Labeled LOCAL_SANDBOX_RUNNER (NOT NEBIUS!).
    """
    start_time = time.perf_counter()
    exec_target = "LOCAL_SANDBOX_RUNNER"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_dir)
    if extra_env:
        env.update(extra_env)

    cmd = [sys.executable, "-m", "pytest", "test_migration_parity.py", "-v", "--tb=short"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
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

        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,  # Only labeled NEBIUS when a job was actually submitted!
            exit_code=proc.returncode,
            passed_count=passed,
            failed_count=failed,
            duration_ms=duration_ms,
            stdout_log=f"[{target_label} Process Sandbox - LOCAL RUNNER]\n{proc.stdout}",
            stderr_traceback=traceback_excerpt.strip(),
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = exc.stdout or ""
        err = exc.stderr or ""
        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=124,
            passed_count=0,
            failed_count=1,
            duration_ms=duration_ms,
            stdout_log=out if isinstance(out, str) else out.decode("utf-8", errors="replace"),
            stderr_traceback=f"Execution timed out after {timeout} seconds: Container exceeded execution deadline.\n{err}".strip(),
        )


def run_single_sandbox_job(
    repo_dir: Path,
    attempt_num: int,
    target_label: str = "PATCHED",
    timeout: int = 45,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunAttempt:
    """
    Executes a single sandbox run:
    1. If NEBIUS_SERVERLESS_JOB_ENDPOINT is set, submits to Nebius Serverless Jobs API.
    2. Otherwise, executes a local container via Docker using sandbox_runner/Dockerfile.sandbox.
    3. Falls back gracefully to local isolated process if Docker is not available.
    4. Only labels execution_target as NEBIUS when a job was actually submitted to Nebius.
    5. Catches TimeoutExpired and returns a failed attempt.
    """
    job_id = f"sandbox-job-{uuid.uuid4().hex[:8]}"
    nebius_job_endpoint = os.getenv("NEBIUS_SERVERLESS_JOB_ENDPOINT", "").strip()

    # 1. Nebius Serverless Jobs API (when configured)
    if nebius_job_endpoint:
        nebius_res = _run_nebius_serverless_job(
            repo_dir, job_id, attempt_num, nebius_job_endpoint, target_label, timeout, extra_env
        )
        if nebius_res is not None:
            return nebius_res

    # 2. Local Docker container using sandbox_runner/Dockerfile.sandbox
    if shutil.which("docker"):
        try:
            return _run_docker_container(
                repo_dir, job_id, attempt_num, target_label, timeout, extra_env
            )
        except Exception as exc:
            print(f"  ⚠️ [Docker Container Fallback] Docker execution notice: {exc}", flush=True)

    # 3. Graceful local process fallback
    return _run_local_process_sandbox(
        repo_dir, job_id, attempt_num, target_label, timeout, extra_env
    )


def run_baseline_and_patched_parallel(
    baseline_repo_dir: Optional[Path],
    patched_repo_dir: Path,
    attempt_idx: int,
    timeout: int = 45,
) -> Tuple[Optional[SandboxRunAttempt], SandboxRunAttempt]:
    """
    Runs the baseline (original code, old pinned version) and the patched code
    in two containers in parallel using ThreadPoolExecutor.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        patched_future = executor.submit(
            run_single_sandbox_job, patched_repo_dir, attempt_idx, "PATCHED", timeout, {"DRIFTZERO_RECORD_GOLDEN": "0"}
        )
        baseline_future = None
        if baseline_repo_dir and baseline_repo_dir.exists():
            baseline_future = executor.submit(
                run_single_sandbox_job, baseline_repo_dir, attempt_idx, "BASELINE", timeout, {"DRIFTZERO_RECORD_GOLDEN": "0"}
            )

        baseline_res = baseline_future.result() if baseline_future else None
        patched_res = patched_future.result()

    return baseline_res, patched_res


def execute_stage4_verification_loop(
    patched_repo_dir: Path,
    migration_delta: MigrationDelta,
    max_retries: int = 3,
    baseline_repo_dir: Optional[Path] = None,
) -> Stage4VerificationReport:
    """
    Universal Stage 4 Verification Loop:
    1. Generates Differential Parity Tests inside `patched_repo_dir`.
    2. Runs baseline container in recording mode (DRIFTZERO_RECORD_GOLDEN=1) to record golden.json.
    3. Runs the baseline and patched code in two containers in parallel, asserting equality against golden.json.
    4. If any test fails, extracts failing files dynamically and invokes self_healer.py.
    """
    # Auto-infer baseline_repo_dir if not explicitly passed
    if baseline_repo_dir is None:
        if patched_repo_dir.name.startswith("patched_"):
            candidate = patched_repo_dir.parent / patched_repo_dir.name.replace("patched_", "", 1)
            if candidate.exists():
                baseline_repo_dir = candidate

    test_file = generate_parity_test_suite(
        patched_repo_dir,
        migration_delta,
    )

    # Copy the parity test file to baseline_repo_dir so both run the identical parity suite
    if baseline_repo_dir and baseline_repo_dir.exists():
        try:
            shutil.copy2(test_file, baseline_repo_dir / "test_migration_parity.py")
        except Exception as exc:
            print(f"  ⚠️ [Baseline Test Copy Notice] {exc}", flush=True)

        # Record baseline outputs into golden.json using the old-version container
        try:
            print("  ✨ [Differential Parity] Recording baseline outputs into golden.json via old-version container...", flush=True)
            run_single_sandbox_job(
                baseline_repo_dir,
                0,
                target_label="BASELINE_RECORD",
                timeout=45,
                extra_env={"DRIFTZERO_RECORD_GOLDEN": "1"},
            )
            golden_src = baseline_repo_dir / "golden.json"
            if golden_src.exists():
                shutil.copy2(golden_src, patched_repo_dir / "golden.json")
                print(f"  ✨ [Differential Parity] Synchronized golden.json ({golden_src.stat().st_size} bytes) to patched workspace", flush=True)
        except Exception as exc:
            print(f"  ⚠️ [Golden Recording Notice] {exc}", flush=True)

    attempts: List[SandboxRunAttempt] = []
    self_healed = False

    for attempt_idx in range(1, max_retries + 1):
        # Run baseline and patched code in two containers in parallel
        baseline_res, attempt_result = run_baseline_and_patched_parallel(
            baseline_repo_dir, patched_repo_dir, attempt_idx
        )

        # Attach differential summary to the patched result stdout
        if baseline_res:
            diff_header = (
                f"--- [Differential Parity Verification] ---\n"
                f"• Baseline (Original Code, Old Pinned Version): Exit {baseline_res.exit_code} "
                f"({baseline_res.passed_count} passed, {baseline_res.failed_count} failed) [{baseline_res.execution_target}]\n"
                f"• Patched  (Migrated Code, Upgraded Version):  Exit {attempt_result.exit_code} "
                f"({attempt_result.passed_count} passed, {attempt_result.failed_count} failed) [{attempt_result.execution_target}]\n\n"
            )
            attempt_result.stdout_log = diff_header + attempt_result.stdout_log

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