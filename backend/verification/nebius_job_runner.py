# Dispatches verification container to Nebius Serverless Jobs REST API or Local Docker Sandbox
import ast
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    Prioritizes innermost (root-cause) frames per failure section before outer intermediate callers.
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

    def _resolve_candidate(clean_path: str) -> Optional[str]:
        p = Path(clean_path)
        if p.name.startswith("test_") or "site-packages" in clean_path or ".venv" in clean_path:
            return None
        if not p.is_absolute() and (patched_repo_dir / clean_path).exists():
            return clean_path
        elif p.is_absolute() and p.exists():
            try:
                return str(p.relative_to(patched_repo_dir))
            except ValueError:
                return None
        return None

    # Step 1: Split into individual test failure sections if present (e.g. "___ test_name ___")
    sections = re.split(r"_{3,}\s+[\w\.\-\[\]]+\s+_{3,}", target_text)
    failing_files = []

    for section in sections:
        matches = re.findall(r"([\w\.\-/]+?\.py):\d+", section)
        # Scan in reverse order: the bottom-most frame in each section is the leaf / exception site
        for m in reversed(matches):
            clean = m.replace("\\", "/")
            candidate = _resolve_candidate(clean)
            if candidate and candidate not in failing_files:
                failing_files.append(candidate)
                break  # Pick the innermost root-cause frame for this test failure

    # Step 2: Fallback across whole target_text if section splitting yielded nothing
    if not failing_files:
        matches = re.findall(r"([\w\.\-/]+?\.py):\d+", target_text)
        for m in reversed(matches):
            clean = m.replace("\\", "/")
            candidate = _resolve_candidate(clean)
            if candidate and candidate not in failing_files:
                failing_files.append(candidate)

    # Step 3: If no non-test file was in the traceback (e.g. assertion failed inside test_*.py),
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


def _is_docker_daemon_reachable() -> bool:
    """
    Checks if Docker CLI is present and the Docker daemon is reachable via 'docker info'.
    Falls back only when the daemon is unreachable.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        proc = subprocess.run(
            [docker_bin, "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _run_docker_container(
    repo_dir: Path,
    job_id: str,
    attempt_num: int,
    target_label: str = "PATCHED",
    timeout: int = 45,
    extra_env: Optional[Dict[str, str]] = None,
    host_out_dir: Optional[Path] = None,
) -> SandboxRunAttempt:
    """
    Runs Docker container locally using sandbox_runner/Dockerfile.sandbox.
    Builds from a temp context containing the repo and entrypoint.sh.
    Runs with -v <host_out_dir>:/out and writes golden.json there.
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

    entrypoint_path = project_root / "sandbox_runner" / "entrypoint.sh"
    if not entrypoint_path.exists():
        entrypoint_path = Path("sandbox_runner/entrypoint.sh").resolve()

    image_tag = f"driftzero-sandbox-{job_id.lower()}"
    out_dir = (host_out_dir or repo_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build from a temp context containing the repo and entrypoint.sh
    with tempfile.TemporaryDirectory(prefix="driftzero_docker_ctx_") as temp_ctx_str:
        temp_ctx = Path(temp_ctx_str)
        # Copy repo into temp_ctx / "repo"
        shutil.copytree(repo_dir, temp_ctx / "repo", dirs_exist_ok=True)
        # Copy entrypoint.sh into temp_ctx
        shutil.copy2(entrypoint_path, temp_ctx / "entrypoint.sh")
        # Copy Dockerfile.sandbox into temp_ctx
        shutil.copy2(dockerfile_path, temp_ctx / "Dockerfile")

        build_cmd = [
            docker_bin, "build",
            "-f", str(temp_ctx / "Dockerfile"),
            "-t", image_tag,
            "--build-arg", "TARGET_DIR=repo",
            str(temp_ctx),
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

    # 2. Run with -v <host_out_dir>:/out and write golden.json there
    run_cmd = [
        docker_bin, "run", "--rm",
        "--network", "none",
        "--cpus", "2.0",
        "--memory", "2g",
        "-v", f"{str(out_dir)}:/out",
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

        # Ensure golden.json written to /out is available in repo_dir
        if (out_dir / "golden.json").exists() and not (repo_dir / "golden.json").exists():
            shutil.copy2(out_dir / "golden.json", repo_dir / "golden.json")

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


def _find_workspace_manifests(repo_dir: Path) -> List[Path]:
    """
    Universally finds requirements.txt, pyproject.toml, and setup.py manifests
    across the repo root and primary subdirectories (e.g. backend/, api/, server/).
    """
    manifests: List[Path] = []
    # 1. Root manifests
    for name in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"):
        root_cand = repo_dir / name
        if root_cand.is_file():
            manifests.append(root_cand)

    # 2. Primary subdirectory manifests (e.g. backend/requirements.txt, server/requirements.txt)
    ignore_subs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".tox", "tests"}
    if repo_dir.exists():
        try:
            for sub in sorted(repo_dir.iterdir()):
                if sub.is_dir() and sub.name not in ignore_subs and not sub.name.startswith("."):
                    for name in ("requirements.txt", "pyproject.toml", "setup.py"):
                        cand = sub / name
                        if cand.is_file() and cand not in manifests:
                            manifests.append(cand)
        except Exception:
            pass

    return manifests


def _discover_pythonpath_roots(repo_dir: Path) -> str:
    """
    Discovers all candidate Python source roots to guarantee internal package imports resolve.
    Includes repo root, src/, and top-level subdirectories containing Python modules or packages
    (e.g. backend/, api/, server/, app/).
    """
    roots = [str(repo_dir)]
    src_dir = repo_dir / "src"
    if src_dir.is_dir():
        roots.append(str(src_dir))

    sep = ";" if os.name == "nt" else ":"
    ignore_dirs = {
        ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".tox", "tests"
    }
    if repo_dir.exists():
        try:
            for child in sorted(repo_dir.iterdir()):
                if child.is_dir() and child.name not in ignore_dirs and not child.name.startswith("."):
                    try:
                        has_py = (
                            (child / "__init__.py").exists()
                            or any(child.glob("*.py"))
                            or any((child / p).is_dir() for p in ("app", "core", "services", "models", "routers", "api", "src"))
                        )
                        if has_py and str(child) not in roots:
                            roots.append(str(child))
                    except Exception:
                        pass
        except Exception:
            pass

    clean_pythonpath = os.environ.get("PYTHONPATH", "")
    if clean_pythonpath:
        roots.append(clean_pythonpath)

    return sep.join(roots)


def _compute_manifest_hash(repo_dir: Path) -> str:
    """
    Computes a deterministic SHA-256 hash of repository dependency manifests
    (requirements.txt, pyproject.toml, setup.py, setup.cfg, Pipfile across root and subdirs).
    """
    hasher = hashlib.sha256()
    manifests = _find_workspace_manifests(repo_dir)
    if not manifests:
        hasher.update(b"no-manifest")
        return hasher.hexdigest()[:16]

    for manifest_path in manifests:
        try:
            rel = str(manifest_path.relative_to(repo_dir))
        except ValueError:
            rel = manifest_path.name
        hasher.update(rel.encode("utf-8"))
        try:
            hasher.update(manifest_path.read_bytes())
        except Exception:
            pass

    return hasher.hexdigest()[:16]


_LOCAL_VENV_CACHE: Dict[Tuple[str, str], Path] = {}
_CAN_UNSHARE_NET: Optional[bool] = None


def _reset_local_venv_cache_for_testing() -> None:
    """Clears the in-memory cache of local venvs, network isolation probe, and removes cached directories."""
    global _CAN_UNSHARE_NET
    _CAN_UNSHARE_NET = None
    _LOCAL_VENV_CACHE.clear()
    for p in Path(tempfile.gettempdir()).glob("driftzero_venv_*"):
        try:
            shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def _check_network_isolation_support() -> Tuple[bool, List[str], str]:
    """
    Checks if network namespace isolation is supported on the current platform.

    Returns:
        (is_isolated: bool, prefix_cmd: List[str], documentation_message: str)

    Fallback Documentation:
    -----------------------
    Network namespace isolation (`unshare -n`) is a Linux kernel feature provided by
    util-linux (`CLONE_NEWNET`). When running on platforms without network namespace
    support (such as macOS / Darwin or Windows), or inside unprivileged containers
    that lack CAP_SYS_ADMIN capabilities:
      - `unshare` is either not present in PATH, or returns EPERM ("Operation not permitted").
      - Fallback behavior: DriftZero logs the fallback reason and executes the test command
        without the network unshare wrapper. The execution environment remains constrained
        by the scrubbed environment variables (PATH limited to venv bin and /usr/bin:/bin,
        scrubbed secrets, and pip non-interactive flags) and filesystem isolation in the
        isolated venv.
    """
    global _CAN_UNSHARE_NET
    unshare_bin = shutil.which("unshare")
    if not unshare_bin:
        doc = (
            "Network isolation unavailable: 'unshare' utility not found on this platform "
            "(macOS/Windows or minimal environment). Falling back to direct execution."
        )
        return False, [], doc

    if _CAN_UNSHARE_NET is False:
        doc = (
            "Network isolation unavailable: 'unshare -n' previously failed or lacked permissions "
            "(e.g. unprivileged container lacking CAP_SYS_ADMIN). Falling back to direct execution."
        )
        return False, [], doc

    if _CAN_UNSHARE_NET is True:
        return True, [unshare_bin, "-n"], "Network isolation active: running tests in an isolated network namespace (unshare -n)."

    # Probe once
    try:
        probe = subprocess.run(
            [unshare_bin, "-n", "true"],
            capture_output=True,
            timeout=2,
        )
        if probe.returncode == 0:
            _CAN_UNSHARE_NET = True
            return True, [unshare_bin, "-n"], "Network isolation active: running tests in an isolated network namespace (unshare -n)."
        else:
            _CAN_UNSHARE_NET = False
            err = (probe.stderr or "").strip()
            doc = (
                f"Network isolation unpermitted: 'unshare -n' exited with code {probe.returncode} "
                f"({err or 'permission denied'}). Falling back to direct execution."
            )
            return False, [], doc
    except Exception as exc:
        _CAN_UNSHARE_NET = False
        doc = f"Network isolation probe failed ({exc}). Falling back to direct execution."
        return False, [], doc


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
    Creates and caches an isolated venv per (manifest hash, role).
    Pip-installs baseline pins for BASELINE and the patched manifest for PATCHED.
    Runs pytest with that venv's python executable using a scrubbed environment.
    If environment creation, dependency installation, or execution fails,
    returns a failed attempt with a clear error.
    """
    start_time = time.perf_counter()
    exec_target = "LOCAL_SANDBOX_RUNNER"

    # 0. Refuse execution unless explicitly enabled via ALLOW_UNSAFE_LOCAL_SANDBOX=1
    if os.getenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "0").strip() != "1":
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        error_msg = (
            "Local process sandbox execution refused: ALLOW_UNSAFE_LOCAL_SANDBOX=1 is required "
            "to run fallback execution without container isolation."
        )
        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=1,
            passed_count=0,
            failed_count=1,
            duration_ms=duration_ms,
            stdout_log="",
            stderr_traceback=error_msg,
        )

    # 1. Compute manifest hash and lookup cached venv per (manifest hash, role)
    manifest_hash = _compute_manifest_hash(repo_dir)
    role = target_label.upper()
    cache_key = (manifest_hash, role)

    venv_dir_name = f"driftzero_venv_{target_label.lower()}_{manifest_hash}"
    venv_path = Path(tempfile.gettempdir()) / venv_dir_name
    venv_python = venv_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    venv_pip = venv_path / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    ready_marker = venv_path / ".driftzero_ready"

    # Build scrubbed environment (PATH, HOME, PYTHONPATH, and PIP flags only)
    bin_dir = str(venv_path / ("Scripts" if os.name == "nt" else "bin"))
    full_pythonpath = _discover_pythonpath_roots(repo_dir)

    system_path = "C:\\Windows\\system32;C:\\Windows" if os.name == "nt" else "/usr/bin:/bin"
    sep = ";" if os.name == "nt" else ":"
    full_path = f"{bin_dir}{sep}{system_path}"

    clean_home = os.environ.get("HOME", str(Path.home()))

    scrubbed_env: Dict[str, str] = {
        "PATH": full_path,
        "HOME": clean_home,
        "PYTHONPATH": full_pythonpath,
        "PIP_NO_INPUT": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    if extra_env:
        scrubbed_env.update(extra_env)

    # 2. Re-use cached venv or build a new one
    is_cached = (
        (cache_key in _LOCAL_VENV_CACHE and venv_python.exists())
        or (ready_marker.exists() and venv_python.is_file() and os.access(venv_python, os.X_OK))
    )
    pins_relaxed = False
    if is_cached and (venv_path / "PINS_RELAXED").exists():
        pins_relaxed = True

    if not is_cached:
        if venv_path.exists() and not ready_marker.exists():
            shutil.rmtree(venv_path, ignore_errors=True)

        venv_cmd = [sys.executable, "-m", "venv", str(venv_path)]
        try:
            venv_proc = subprocess.run(
                venv_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if venv_proc.returncode != 0:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return SandboxRunAttempt(
                    attempt_number=attempt_num,
                    job_id=job_id,
                    execution_target=exec_target,
                    exit_code=venv_proc.returncode,
                    passed_count=0,
                    failed_count=1,
                    duration_ms=duration_ms,
                    stdout_log=venv_proc.stdout,
                    stderr_traceback=f"Failed to create virtual environment with 'python -m venv':\n{venv_proc.stderr}".strip(),
                )
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return SandboxRunAttempt(
                attempt_number=attempt_num,
                job_id=job_id,
                execution_target=exec_target,
                exit_code=1,
                passed_count=0,
                failed_count=1,
                duration_ms=duration_ms,
                stdout_log="",
                stderr_traceback=f"Exception creating virtual environment: {exc}",
            )

        if not venv_python.exists():
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return SandboxRunAttempt(
                attempt_number=attempt_num,
                job_id=job_id,
                execution_target=exec_target,
                exit_code=1,
                passed_count=0,
                failed_count=1,
                duration_ms=duration_ms,
                stdout_log="",
                stderr_traceback=f"Venv Python executable not found at {venv_python}",
            )

        # Install pytest in venv
        try:
            pytest_inst = subprocess.run(
                [str(venv_pip), "install", "--no-cache-dir", "pytest>=8.0.0"],
                capture_output=True,
                text=True,
                timeout=60,
                env=scrubbed_env,
            )
            if pytest_inst.returncode != 0:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return SandboxRunAttempt(
                    attempt_number=attempt_num,
                    job_id=job_id,
                    execution_target=exec_target,
                    exit_code=pytest_inst.returncode,
                    passed_count=0,
                    failed_count=1,
                    duration_ms=duration_ms,
                    stdout_log=pytest_inst.stdout,
                    stderr_traceback=f"Failed to install pytest in venv:\n{pytest_inst.stderr}".strip(),
                )
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return SandboxRunAttempt(
                attempt_number=attempt_num,
                job_id=job_id,
                execution_target=exec_target,
                exit_code=1,
                passed_count=0,
                failed_count=1,
                duration_ms=duration_ms,
                stdout_log="",
                stderr_traceback=f"Exception installing pytest into venv: {exc}",
            )

        # Pip-install baseline pins for BASELINE and patched manifest for PATCHED
        manifests = _find_workspace_manifests(repo_dir)
        install_errors = []
        any_installed = False

        if not manifests:
            pyproject_file = repo_dir / "pyproject.toml"
            if pyproject_file.exists():
                manifests = [pyproject_file]

        for manifest_file in manifests:
            is_req_txt = manifest_file.name.startswith("requirements") or manifest_file.suffix == ".txt"
            if is_req_txt:
                install_cmd = [str(venv_pip), "install", "--no-cache-dir", "--only-binary=:all:", "-r", str(manifest_file)]
            elif manifest_file.name == "pyproject.toml":
                install_cmd = [str(venv_pip), "install", "--no-cache-dir", "--only-binary=:all:", str(manifest_file.parent)]
            else:
                continue

            try:
                deps_inst = subprocess.run(
                    install_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=scrubbed_env,
                )
                if deps_inst.returncode == 0:
                    any_installed = True
                elif is_req_txt:
                    # Pass 2: Relax exact '==' to '>='
                    try:
                        content = manifest_file.read_text(encoding="utf-8")
                        relaxed_lines = []
                        for line in content.splitlines():
                            stripped = line.strip()
                            if not stripped or stripped.startswith("#"):
                                relaxed_lines.append(line)
                                continue
                            if "==" in line:
                                print(f"  ⚠️ [Sandbox] Exact pin '{stripped}' failed to install; relaxing to '>=' (baseline/patched may no longer differ only by the migration)", flush=True)
                                relaxed_line = line.replace("==", ">=")
                                if relaxed_line != line:
                                    pins_relaxed = True
                                relaxed_lines.append(relaxed_line)
                            else:
                                relaxed_lines.append(line)
                        if pins_relaxed:
                            with tempfile.NamedTemporaryFile("w", suffix="_relaxed_reqs.txt", delete=False) as rf:
                                rf.write("\n".join(relaxed_lines) + "\n")
                                temp_rf_path = rf.name
                            deps_inst = subprocess.run(
                                [str(venv_pip), "install", "--no-cache-dir", "-r", temp_rf_path],
                                capture_output=True,
                                text=True,
                                timeout=120,
                                env=scrubbed_env,
                            )
                            try:
                                os.unlink(temp_rf_path)
                            except Exception:
                                pass
                            if deps_inst.returncode == 0:
                                any_installed = True
                    except Exception:
                        pass

                    # Pass 3: Resilient line-by-line fallback if batch install failed and multiple packages exist
                    req_lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
                    if deps_inst.returncode != 0 and len(req_lines) > 1:
                        try:
                            for line in req_lines:
                                line_inst = subprocess.run(
                                    [str(venv_pip), "install", "--no-cache-dir", "--prefer-binary", line],
                                    capture_output=True,
                                    text=True,
                                    timeout=60,
                                    env=scrubbed_env,
                                )
                                if line_inst.returncode == 0:
                                    any_installed = True
                                else:
                                    pins_relaxed = True
                                    print(f"  ⚠️ [Sandbox Notice] Skipped incompatible package pin '{line}': {line_inst.stderr.strip()}", flush=True)
                        except Exception:
                            pass

                if deps_inst.returncode != 0 and not any_installed:
                    install_errors.append(f"Failed to pip-install {target_label} dependencies from {manifest_file.name} into venv:\n{deps_inst.stderr}")
            except Exception as exc:
                install_errors.append(f"Exception installing {target_label} dependencies from {manifest_file.name} into venv: {exc}")

        if manifests and install_errors and not any_installed:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            stdout_log = ""
            if pins_relaxed:
                stdout_log = "PINS RELAXED\n"
            return SandboxRunAttempt(
                attempt_number=attempt_num,
                job_id=job_id,
                execution_target=exec_target,
                exit_code=1,
                passed_count=0,
                failed_count=1,
                duration_ms=duration_ms,
                stdout_log=stdout_log,
                stderr_traceback="\n".join(install_errors).strip(),
                pins_relaxed=pins_relaxed,
            )

        try:
            if pins_relaxed:
                (venv_path / "PINS_RELAXED").write_text("1", encoding="utf-8")
            ready_marker.write_text("ready", encoding="utf-8")
        except Exception:
            pass
        _LOCAL_VENV_CACHE[cache_key] = venv_path
    else:
        _LOCAL_VENV_CACHE[cache_key] = venv_path
        if (venv_path / "PINS_RELAXED").exists():
            pins_relaxed = True

    # 3. Run pytest with that venv's python with network access disabled where supported
    is_isolated, net_prefix, net_doc = _check_network_isolation_support()
    base_test_cmd = [str(venv_python), "-m", "pytest", "test_migration_parity.py", "-v", "--tb=short"]
    cmd = net_prefix + base_test_cmd
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            env=scrubbed_env,
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

        stdout_log = (
            f"[{target_label} Process Sandbox - LOCAL RUNNER (venv: {venv_path.name})]\n"
            f"[{net_doc}]\n"
            f"{proc.stdout}"
        )
        if pins_relaxed:
            if stdout_log and not stdout_log.endswith("\n"):
                stdout_log += "\n"
            stdout_log += "PINS RELAXED\n"

        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=proc.returncode,
            passed_count=passed,
            failed_count=failed,
            duration_ms=duration_ms,
            stdout_log=stdout_log,
            stderr_traceback=traceback_excerpt.strip(),
            pins_relaxed=pins_relaxed,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = exc.stdout or ""
        err = exc.stderr or ""
        stdout_log = out if isinstance(out, str) else out.decode("utf-8", errors="replace")
        if pins_relaxed:
            if stdout_log and not stdout_log.endswith("\n"):
                stdout_log += "\n"
            stdout_log += "PINS RELAXED\n"
        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=124,
            passed_count=0,
            failed_count=1,
            duration_ms=duration_ms,
            stdout_log=stdout_log,
            stderr_traceback=f"Execution timed out after {timeout} seconds: Process exceeded execution deadline.\n{err}".strip(),
            pins_relaxed=pins_relaxed,
        )
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        stdout_log = ""
        if pins_relaxed:
            stdout_log = "PINS RELAXED\n"
        return SandboxRunAttempt(
            attempt_number=attempt_num,
            job_id=job_id,
            execution_target=exec_target,
            exit_code=1,
            passed_count=0,
            failed_count=1,
            duration_ms=duration_ms,
            stdout_log=stdout_log,
            stderr_traceback=f"Local sandbox execution failed with error: {exc}",
            pins_relaxed=pins_relaxed,
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
    2. Otherwise, executes a local container via Docker if daemon is reachable (checked via 'docker info').
    3. Falls back gracefully to isolated local venv process when Docker daemon is unreachable.
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

    # 2. Local Docker container: check docker info (not which) and fall back only when daemon is unreachable
    if _is_docker_daemon_reachable():
        try:
            return _run_docker_container(
                repo_dir, job_id, attempt_num, target_label, timeout, extra_env
            )
        except Exception as exc:
            print(f"  ⚠️ [Docker Container Fallback] Docker execution notice: {exc}", flush=True)

    # 3. Graceful local process fallback with isolated per-run venv
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
            baseline_rec = run_single_sandbox_job(
                baseline_repo_dir,
                0,
                target_label="BASELINE_RECORD",
                timeout=45,
                extra_env={"DRIFTZERO_RECORD_GOLDEN": "1"},
            )
            if baseline_rec and (getattr(baseline_rec, "pins_relaxed", False) or "PINS RELAXED" in getattr(baseline_rec, "stdout_log", "")):
                pins_relaxed_overall = True
            golden_src = baseline_repo_dir / "golden.json"
            if golden_src.exists():
                shutil.copy2(golden_src, patched_repo_dir / "golden.json")
                print(f"  ✨ [Differential Parity] Synchronized golden.json ({golden_src.stat().st_size} bytes) to patched workspace", flush=True)
        except Exception as exc:
            print(f"  ⚠️ [Golden Recording Notice] {exc}", flush=True)

    attempts: List[SandboxRunAttempt] = []
    self_healed = False
    pins_relaxed_overall = False

    for attempt_idx in range(1, max_retries + 1):
        # Run baseline and patched code in two containers in parallel
        baseline_res, attempt_result = run_baseline_and_patched_parallel(
            baseline_repo_dir, patched_repo_dir, attempt_idx
        )

        if baseline_res and (getattr(baseline_res, "pins_relaxed", False) or "PINS RELAXED" in getattr(baseline_res, "stdout_log", "")):
            pins_relaxed_overall = True
        if getattr(attempt_result, "pins_relaxed", False) or "PINS RELAXED" in getattr(attempt_result, "stdout_log", ""):
            pins_relaxed_overall = True

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

    final_status = "FAILED"
    if attempts and attempts[-1].exit_code == 0:
        final_status = "NEEDS_MANUAL" if pins_relaxed_overall else "PASSED"

    return Stage4VerificationReport(
        test_file_path=str(test_file),
        total_attempts=len(attempts),
        final_status=final_status,
        self_healing_triggered=self_healed,
        attempts=attempts,
        pins_relaxed=pins_relaxed_overall,
    )