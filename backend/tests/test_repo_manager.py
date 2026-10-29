import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.core.repo_manager import (
    GITHUB_URL_REGEX,
    ManifestUpdateResult,
    _bump_pyproject_toml,
    check_manifest_conflicts,
    is_openai_v1_migrated,
    update_manifest_version,
)


def test_is_openai_v1_migrated():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        # 1. Empty repo
        assert is_openai_v1_migrated(repo_dir) is False

        # 2. v0 OpenAI code
        v0_file = repo_dir / "app_v0.py"
        v0_file.write_text(
            "import openai\n"
            "openai.api_key = 'sk-123'\n"
            "res = openai.ChatCompletion.create(model='gpt-3.5-turbo', messages=[])\n"
        )
        assert is_openai_v1_migrated(repo_dir) is False

        # 3. v1 OpenAI code with import
        v1_file = repo_dir / "app_v1.py"
        v1_file.write_text(
            "from openai import OpenAI\n"
            "client = OpenAI()\n"
            "res = client.chat.completions.create(model='gpt-4o', messages=[])\n"
        )
        assert is_openai_v1_migrated(repo_dir) is True

        # Clean up v1 file
        v1_file.unlink()
        assert is_openai_v1_migrated(repo_dir) is False

        # 4. v1 OpenAI code with openai.OpenAI()
        v1_file2 = repo_dir / "app_v1_attr.py"
        v1_file2.write_text(
            "import openai\n"
            "client = openai.OpenAI()\n"
        )
        assert is_openai_v1_migrated(repo_dir) is True

        # Clean up
        v1_file2.unlink()

        # 5. v1 OpenAI code with AsyncOpenAI
        v1_async = repo_dir / "app_async.py"
        v1_async.write_text(
            "from openai import AsyncOpenAI\n"
            "client = AsyncOpenAI()\n"
        )
        assert is_openai_v1_migrated(repo_dir) is True


def test_auto_bump_ecosystem_conflicts_requirements_txt():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        # Create requirements.txt with pydantic v1 and outdated ecosystem packages
        req_file = repo_dir / "requirements.txt"
        req_file.write_text(
            "pydantic==1.10.13\n"
            "fastapi==0.95.0\n"
            "sqlmodel==0.0.8\n"
            "langchain<0.0.267\n"
            "pydantic-factories==0.1.0\n"  # min_ver is None, cannot be auto-bumped
            "uvicorn>=0.20.0\n"
        )

        with patch("subprocess.run") as mock_run:
            # Mock pip install --dry-run succeeding
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stderr = ""
            mock_proc.stdout = "Dry-run success"
            mock_run.return_value = mock_proc

            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        assert isinstance(res, ManifestUpdateResult)
        assert bool(res) is True
        assert res.status == "SUCCESS"
        assert res.needs_manual is False

        content = req_file.read_text()
        # pydantic bumped to 2.9.2
        assert "pydantic==2.9.2" in content
        # fastapi auto-bumped to recommended minimum >=0.100.0
        assert "fastapi>=0.100.0" in content
        # sqlmodel auto-bumped to recommended minimum >=0.0.14
        assert "sqlmodel>=0.0.14" in content
        # langchain auto-bumped to recommended minimum >=0.0.267
        assert "langchain>=0.0.267" in content
        # companion packages added
        assert "pydantic-settings>=2.0.0" in content
        # uvicorn untouched
        assert "uvicorn>=0.20.0" in content

        # auto_bumped_packages tracked
        assert "fastapi>=0.100.0" in res.auto_bumped_packages
        assert "sqlmodel>=0.0.14" in res.auto_bumped_packages
        assert "langchain>=0.0.267" in res.auto_bumped_packages

        # pydantic-factories cannot be auto-bumped (min_ver is None), so remains in conflicts!
        assert any("pydantic-factories" in c for c in res.conflicts)


def test_auto_bump_openai_when_migrated_to_v1():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        # 1. Code migrated to v1
        py_file = repo_dir / "service.py"
        py_file.write_text(
            "from openai import OpenAI\n"
            "client = OpenAI()\n"
            "def ask(q):\n"
            "    return client.chat.completions.create(model='gpt-4o', messages=[{'role': 'user', 'content': q}])\n"
        )

        # requirements.txt with old openai pin
        req_file = repo_dir / "requirements.txt"
        req_file.write_text(
            "pydantic==1.10.13\n"
            "openai==0.28.0\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        content = req_file.read_text()
        assert "openai>=1.0.0" in content
        assert "openai>=1.0.0" in res.auto_bumped_packages


def test_no_bump_openai_when_still_v0():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        # Code still using v0
        py_file = repo_dir / "service.py"
        py_file.write_text(
            "import openai\n"
            "openai.api_key = 'sk-xxx'\n"
            "def ask(q):\n"
            "    return openai.ChatCompletion.create(model='gpt-3.5-turbo', messages=[])\n"
        )

        # requirements.txt with old openai pin
        req_file = repo_dir / "requirements.txt"
        req_file.write_text(
            "pydantic==1.10.13\n"
            "openai==0.28.0\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        content = req_file.read_text()
        # openai==0.28.0 remains untouched because code was NOT migrated to v1 client
        assert "openai==0.28.0" in content
        assert not any("openai" in b for b in res.auto_bumped_packages)


def test_auto_bump_pyproject_poetry_and_pep621():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        # Create pyproject.toml with Poetry style
        pyproj = repo_dir / "pyproject.toml"
        pyproj.write_text(
            '[tool.poetry]\n'
            'name = "sample-poetry"\n'
            'version = "0.1.0"\n\n'
            '[tool.poetry.dependencies]\n'
            'python = "^3.10"\n'
            'pydantic = "^1.10.13"\n'
            'fastapi = "0.95.0"\n'
            'sqlmodel = "^0.0.8"\n'
            'openai = "0.28.0"\n'
        )

        # Add v1 OpenAI usage in code
        (repo_dir / "main.py").write_text("from openai import OpenAI\nclient = OpenAI()\n")

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        content = pyproj.read_text()
        assert 'pydantic = "^2.9.2"' in content
        assert 'fastapi = "^0.100.0"' in content
        assert 'sqlmodel = "^0.0.14"' in content
        assert 'openai = "^1.0.0"' in content
        assert 'pydantic-settings = "^2.0.0"' in content


def test_pip_dry_run_failure_reports_needs_manual():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        req_file = repo_dir / "requirements.txt"
        req_file.write_text(
            "pydantic==1.10.13\n"
            "conflicting-dep==0.1.0\n"
        )

        with patch("subprocess.run") as mock_run:
            # Mock pip install --dry-run failure
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stderr = "ERROR: Cannot install conflicting-dep because of dependency conflict."
            mock_proc.stdout = ""
            mock_run.return_value = mock_proc

            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        assert res.status == "NEEDS_MANUAL"
        assert res.needs_manual is True
        assert any("NEEDS_MANUAL" in c for c in res.conflicts)
        assert any("Cannot install conflicting-dep" in c for c in res.conflicts)


def test_pip_dry_run_timeout_reports_needs_manual():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)

        req_file = repo_dir / "requirements.txt"
        req_file.write_text("pydantic==1.10.13\n")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=30)):
            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        assert res.status == "NEEDS_MANUAL"
        assert res.needs_manual is True
        assert any("NEEDS_MANUAL" in c and "timed out" in c for c in res.conflicts)


def test_manifest_update_result_backwards_compat():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)
        req_file = repo_dir / "requirements.txt"
        req_file.write_text("pydantic==1.10.13\n")

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        # Tuple unpacking test
        updated, conflicts = res
        assert updated is True
        assert isinstance(conflicts, list)

        # Truthiness test
        assert bool(res) is True


def test_real_pip_dry_run_conflict_reports_needs_manual():
    """
    Test that an actual pip install --dry-run with an unresolvable requirement
    fails and is reported as NEEDS_MANUAL without mocking.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir)
        req_file = repo_dir / "requirements.txt"
        # Unresolvable package that will fail pip install --dry-run
        req_file.write_text("pydantic==1.10.13\npackage-that-does-not-exist-xyz123==1.0.0\n")

        res = update_manifest_version(repo_dir, "pydantic", "2.9.2")

        assert res.status == "NEEDS_MANUAL"
        assert res.needs_manual is True
        assert any("NEEDS_MANUAL" in c for c in res.conflicts)


