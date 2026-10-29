import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.verification.nebius_job_runner import (
    _compute_manifest_hash,
    _is_docker_daemon_reachable,
    _reset_local_venv_cache_for_testing,
    _run_docker_container,
    _run_local_process_sandbox,
    run_single_sandbox_job,
)


def test_is_docker_daemon_reachable():
    """Verify _is_docker_daemon_reachable runs 'docker info' (not which) and checks daemon reachability."""
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        # Case 1: docker executable missing
        mock_which.return_value = None
        assert _is_docker_daemon_reachable() is False
        mock_run.assert_not_called()

        # Case 2: docker CLI exists, but daemon unreachable (docker info exits != 0)
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/usr/bin/docker", "info"],
            returncode=1,
            stdout="",
            stderr="Cannot connect to Docker daemon",
        )
        assert _is_docker_daemon_reachable() is False
        mock_run.assert_called_with(
            ["/usr/bin/docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        # Case 3: docker CLI exists and daemon is reachable (docker info exits 0)
        mock_run.return_value = subprocess.CompletedProcess(
            args=["/usr/bin/docker", "info"],
            returncode=0,
            stdout="Server Version: 24.0.5",
            stderr="",
        )
        assert _is_docker_daemon_reachable() is True


def test_docker_container_temp_context_and_out_volume():
    """Verify docker build uses temp context containing repo and entrypoint.sh, and runs with -v <host_out_dir>:/out."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "test_repo"
        repo_dir.mkdir()
        (repo_dir / "test_file.py").write_text("x = 1\n", encoding="utf-8")

        build_calls = []
        run_calls = []

        def mock_subprocess_run(cmd, *args, **kwargs):
            if "build" in cmd:
                build_calls.append(cmd)
                # Verify that the build context passed to docker build contains repo/ and entrypoint.sh
                build_ctx = Path(cmd[-1])
                assert (build_ctx / "repo" / "test_file.py").exists()
                assert (build_ctx / "entrypoint.sh").exists()
                assert (build_ctx / "Dockerfile").exists()
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Successfully built", stderr="")
            elif "run" in cmd and "--network" in cmd:
                run_calls.append(cmd)
                # Verify volume mount -v <out_dir>:/out
                v_idx = cmd.index("-v")
                mount_spec = cmd[v_idx + 1]
                assert mount_spec.endswith(":/out")
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="1 passed in 0.05s", stderr=""
                )
            elif "rmi" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("shutil.which", return_value="/usr/local/bin/docker"), \
             patch("subprocess.run", side_effect=mock_subprocess_run):

            res = _run_docker_container(repo_dir, "job-123", 1, "PATCHED", 45)
            assert res.exit_code == 0
            assert res.passed_count == 1
            assert len(build_calls) == 1
            assert len(run_calls) == 1


def test_local_process_sandbox_refuses_without_env_var(monkeypatch):
    """Verify _run_local_process_sandbox refuses execution when ALLOW_UNSAFE_LOCAL_SANDBOX != 1."""
    monkeypatch.setenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "0")
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        (repo_dir / "requirements.txt").write_text("pydantic==2.9.2\n", encoding="utf-8")

        res = _run_local_process_sandbox(repo_dir, "job-refuse", 1, "PATCHED", 30)
        assert res.exit_code == 1
        assert res.passed_count == 0
        assert res.failed_count == 1
        assert "ALLOW_UNSAFE_LOCAL_SANDBOX=1 is required" in res.stderr_traceback


def test_local_process_sandbox_venv_per_run_and_failure_reporting(monkeypatch):
    """Verify _run_local_process_sandbox creates a venv with python -m venv, installs dependencies, and runs pytest with venv python."""
    monkeypatch.setenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "1")
    _reset_local_venv_cache_for_testing()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        (repo_dir / "requirements.txt").write_text("pydantic==2.9.2\n", encoding="utf-8")
        (repo_dir / "test_migration_parity.py").write_text("def test_dummy(): pass\n", encoding="utf-8")

        executed_cmds = []

        def mock_subprocess_run(cmd, *args, **kwargs):
            executed_cmds.append(cmd)
            # 1. venv creation
            if "-m" in cmd and "venv" in cmd:
                venv_dir = Path(cmd[-1])
                # Mock creating the bin/python and bin/pip files
                bin_dir = venv_dir / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "python").write_text("#!/bin/sh\n", encoding="utf-8")
                (bin_dir / "pip").write_text("#!/bin/sh\n", encoding="utf-8")
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            # 2. pip install pytest
            elif "pip" in cmd[0] and "install" in cmd and "pytest>=8.0.0" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Installed pytest", stderr="")
            # 3. pip install requirements
            elif "pip" in cmd[0] and "install" in cmd and "-r" in cmd:
                assert "requirements.txt" in str(cmd[-1])
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Installed reqs", stderr="")
            # 4. pytest execution
            elif "python" in cmd[0] and "-m" in cmd and "pytest" in cmd:
                # Ensure it executes with the venv python
                assert "driftzero_venv_patched_" in cmd[0]
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="2 passed in 0.10s", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            res = _run_local_process_sandbox(repo_dir, "job-456", 1, "PATCHED", 30)
            assert res.exit_code == 0
            assert res.passed_count == 2
            assert res.failed_count == 0
            assert "LOCAL_SANDBOX_RUNNER" in res.execution_target


def test_local_process_sandbox_pip_install_failure(monkeypatch):
    """Verify that if pip-install fails, _run_local_process_sandbox returns a failed attempt with a clear error."""
    monkeypatch.setenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "1")
    _reset_local_venv_cache_for_testing()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        (repo_dir / "requirements.txt").write_text("invalid-pkg-that-does-not-exist==999.0\n", encoding="utf-8")

        def mock_subprocess_run(cmd, *args, **kwargs):
            if "-m" in cmd and "venv" in cmd:
                venv_dir = Path(cmd[-1])
                bin_dir = venv_dir / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "python").write_text("#!/bin/sh\n", encoding="utf-8")
                (bin_dir / "pip").write_text("#!/bin/sh\n", encoding="utf-8")
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            elif "pip" in cmd[0] and "install" in cmd and "pytest>=8.0.0" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Installed pytest", stderr="")
            elif "pip" in cmd[0] and "install" in cmd and "-r" in cmd:
                # Simulate pip install failure
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="",
                    stderr="ERROR: Could not find a version that satisfies the requirement invalid-pkg",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            res = _run_local_process_sandbox(repo_dir, "job-fail", 1, "BASELINE", 30)
            assert res.exit_code == 1
            assert res.passed_count == 0
            assert res.failed_count == 1
            assert "Failed to pip-install BASELINE dependencies from requirements.txt" in res.stderr_traceback
            assert "Could not find a version" in res.stderr_traceback


def test_local_process_sandbox_scrubbed_env(monkeypatch):
    """Verify _run_local_process_sandbox passes a scrubbed env (PATH, HOME, PYTHONPATH only) and doesn't leak secrets."""
    monkeypatch.setenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "1")
    monkeypatch.setenv("NEBIUS_API_KEY", "super-secret-nebius-key")
    monkeypatch.setenv("TAVILY_API_KEY", "super-secret-tavily-key")
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-gh-pat")
    monkeypatch.setenv("DRIFTZERO_API_KEY", "super-secret-dz-key")
    _reset_local_venv_cache_for_testing()

    captured_envs = []

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        (repo_dir / "requirements.txt").write_text("pydantic==2.9.2\n", encoding="utf-8")
        (repo_dir / "test_migration_parity.py").write_text("def test_dummy(): pass\n", encoding="utf-8")

        def mock_subprocess_run(cmd, *args, **kwargs):
            if "env" in kwargs and kwargs["env"] is not None:
                captured_envs.append(kwargs["env"])
            if "-m" in cmd and "venv" in cmd:
                venv_dir = Path(cmd[-1])
                bin_dir = venv_dir / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "python").write_text("#!/bin/sh\n", encoding="utf-8")
                (bin_dir / "pip").write_text("#!/bin/sh\n", encoding="utf-8")
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="1 passed in 0.01s", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            res = _run_local_process_sandbox(
                repo_dir, "job-env", 1, "PATCHED", 30, extra_env={"DRIFTZERO_RECORD_GOLDEN": "1"}
            )
            assert res.exit_code == 0
            assert len(captured_envs) > 0

            for captured_env in captured_envs:
                # Must contain PATH, HOME, PYTHONPATH
                assert "PATH" in captured_env
                assert "HOME" in captured_env
                assert "PYTHONPATH" in captured_env
                # Must contain extra_env
                assert captured_env.get("DRIFTZERO_RECORD_GOLDEN") == "1"
                # Must NOT leak host secrets
                assert "NEBIUS_API_KEY" not in captured_env
                assert "TAVILY_API_KEY" not in captured_env
                assert "GITHUB_TOKEN" not in captured_env
                assert "DRIFTZERO_API_KEY" not in captured_env
                # Verify allowed keys only
                allowed_keys = {"PATH", "HOME", "PYTHONPATH", "DRIFTZERO_RECORD_GOLDEN"}
                assert set(captured_env.keys()).issubset(allowed_keys)


def test_local_process_sandbox_caches_venvs(monkeypatch):
    """Verify venvs are cached per (manifest hash, role) across attempts, avoiding redundant creation/installs."""
    monkeypatch.setenv("ALLOW_UNSAFE_LOCAL_SANDBOX", "1")
    _reset_local_venv_cache_for_testing()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        (repo_dir / "requirements.txt").write_text("pydantic==2.9.2\n", encoding="utf-8")
        (repo_dir / "test_migration_parity.py").write_text("def test_dummy(): pass\n", encoding="utf-8")

        executed_cmds = []

        def mock_subprocess_run(cmd, *args, **kwargs):
            executed_cmds.append(list(cmd))
            if "-m" in cmd and "venv" in cmd:
                venv_dir = Path(cmd[-1])
                bin_dir = venv_dir / "bin"
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "python").write_text("#!/bin/sh\n", encoding="utf-8")
                (bin_dir / "pip").write_text("#!/bin/sh\n", encoding="utf-8")
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="1 passed in 0.01s", stderr="")

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            # Attempt 1: PATCHED
            res1 = _run_local_process_sandbox(repo_dir, "job-cache-1", 1, "PATCHED", 30)
            assert res1.exit_code == 0
            # Attempt 1 should create venv, install pytest, install requirements, and run pytest (4 calls)
            assert len(executed_cmds) == 4
            assert any("-m" in c and "venv" in c for c in executed_cmds)

            # Attempt 2: PATCHED with exact same manifest -> should reuse cached venv!
            executed_cmds.clear()
            res2 = _run_local_process_sandbox(repo_dir, "job-cache-2", 2, "PATCHED", 30)
            assert res2.exit_code == 0
            # Attempt 2 should only run pytest! (1 call)
            assert len(executed_cmds) == 1
            assert "pytest" in executed_cmds[0]
            assert not any("-m" in c and "venv" in c for c in executed_cmds)
            assert not any("install" in c for c in executed_cmds)

            # Attempt 2: BASELINE with same manifest -> role is different ("BASELINE" vs "PATCHED"), so separate venv
            executed_cmds.clear()
            res3 = _run_local_process_sandbox(repo_dir, "job-cache-3", 2, "BASELINE", 30)
            assert res3.exit_code == 0
            # Should create new venv for BASELINE role
            assert any("-m" in c and "venv" in c for c in executed_cmds)


def test_entrypoint_sh_contents():
    """Verify entrypoint.sh uses command -v pytest >/dev/null 2>&1, has no runtime pip install, and handles /out."""
    entrypoint_path = Path(__file__).resolve().parents[2] / "sandbox_runner" / "entrypoint.sh"
    content = entrypoint_path.read_text(encoding="utf-8")

    # Must use command -v pytest >/dev/null 2>&1
    assert "command -v pytest >/dev/null 2>&1" in content
    assert "&> /dev/null" not in content

    # Must remove runtime pip install
    assert "pip install" not in content

    # Must handle /out volume and golden.json
    assert "/out" in content
    assert "golden.json" in content
