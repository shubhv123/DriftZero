# Clones target GitHub repo, parses requirements.txt/package.json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Tuple


def is_git_url(repo_str: str) -> bool:
    if not repo_str:
        return False
    s = repo_str.strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("git@") or s.endswith(".git")


def prepare_repo_workspace(
    repo_input: Optional[str] = None,
    default_fallback_dir: Optional[Path] = None,
    ephemeral_parent_dir: Optional[Path] = None,
    github_token: Optional[str] = None,
) -> Tuple[Path, bool]:
    """
    Clones a remote GitHub repository or copies a local repository into an ephemeral workspace.
    Returns (workspace_path, is_ephemeral).
    """
    unique_id = uuid.uuid4().hex[:10]
    if ephemeral_parent_dir is None:
        ephemeral_parent_dir = Path(tempfile.gettempdir()) / "driftzero_workspaces"
    ephemeral_parent_dir.mkdir(parents=True, exist_ok=True)
    workspace_path = ephemeral_parent_dir / f"repo_{unique_id}"

    # 1. Validate repository input
    if not repo_input or not str(repo_input).strip():
        if default_fallback_dir and default_fallback_dir.exists():
            shutil.copytree(default_fallback_dir, workspace_path, dirs_exist_ok=True)
            return workspace_path, True
        raise ValueError("Missing repository input: A valid GitHub repository URL or local directory path must be provided.")

    repo_str = str(repo_input).strip()

    # 2. Remote Git URL
    if is_git_url(repo_str):
        workspace_path.mkdir(parents=True, exist_ok=True)
        git_url = repo_str
        if github_token and "github.com" in git_url and "@" not in git_url:
            git_url = git_url.replace("https://github.com", f"https://x-access-token:{github_token}@github.com")
            git_url = git_url.replace("http://github.com", f"https://x-access-token:{github_token}@github.com")

        clone_env = os.environ.copy()
        clone_env["GIT_TERMINAL_PROMPT"] = "0"
        clone_env["GIT_ASKPASS"] = "echo"

        clone_cmd = ["git", "clone", "--depth", "1", git_url, str(workspace_path)]
        try:
            res = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=30, env=clone_env)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Git clone for '{repo_str}' timed out after 30 seconds.")

        if res.returncode != 0:
            err_msg = (res.stderr or res.stdout or "").strip()
            if "terminal prompts disabled" in err_msg or "Authentication failed" in err_msg or "not found" in err_msg.lower():
                raise RuntimeError(
                    f"Repository '{repo_str}' could not be accessed. The repository does not exist, was mistyped, or is private (requires authentication)."
                )
            raise RuntimeError(f"Failed to clone repository '{repo_str}': {err_msg}")
        return workspace_path, True

    # 3. Local directory
    candidate = Path(repo_str).resolve()
    if candidate.exists() and candidate.is_dir():
        shutil.copytree(candidate, workspace_path, dirs_exist_ok=True)
        return workspace_path, True

    raise ValueError(f"Repository input '{repo_input}' does not exist or is not a valid directory or Git URL.")


def detect_installed_library_version(repo_path: Path, target_library: str) -> Optional[str]:
    """
    Searches requirements.txt, pyproject.toml, Pipfile, setup.py/setup.cfg for the installed/old version.
    """
    lib_name = target_library.strip().lower().replace("_", "-")

    # 1. Check requirements.txt recursively across workspace
    req_candidates = [
        f for f in repo_path.rglob("requirements*.txt")
        if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git"))
    ]
    for req_file in req_candidates:
        if req_file.is_file():
            try:
                for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    cleaned = line.strip().split("#")[0].strip()
                    pattern = rf"^{re.escape(lib_name)}\s*([=><~^!]+)\s*([\w\.\-]+)"
                    match = re.search(pattern, cleaned, re.IGNORECASE)
                    if match:
                        return match.group(2)
            except Exception:
                continue

    # 2. Check pyproject.toml
    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            # e.g., "pydantic = '^1.10.12'" or '"pydantic>=1.10.12"'
            pattern = rf'["\']?{re.escape(lib_name)}["\']?\s*[:=]\s*["\']([^"\'\s]+)["\']'
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                ver_str = match.group(1).lstrip("^~=>=<")
                return ver_str
        except Exception:
            pass

    # 3. Check Pipfile
    pipfile = repo_path / "Pipfile"
    if pipfile.is_file():
        try:
            content = pipfile.read_text(encoding="utf-8", errors="ignore")
            pattern = rf'{re.escape(lib_name)}\s*=\s*["\']([^"\'\s]+)["\']'
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                ver_str = match.group(1).lstrip("^~=>=<")
                if ver_str != "*":
                    return ver_str
        except Exception:
            pass

    return None


def update_manifest_version(repo_path: Path, target_library: str, target_version: str) -> bool:
    """
    Updates the version string in requirements.txt or pyproject.toml to target_version.
    """
    lib_name = target_library.strip().lower().replace("_", "-")
    updated = False

    # 1. Update requirements.txt recursively
    req_candidates = [
        f for f in repo_path.rglob("requirements*.txt")
        if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git"))
    ]
    for req_file in req_candidates:
        if req_file.is_file():
            try:
                lines = req_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                new_lines = []
                matched = False
                for line in lines:
                    cleaned = line.strip().split("#")[0].strip()
                    pattern = rf"^{re.escape(lib_name)}\s*([=><~^!]+)\s*([\w\.\-]+)"
                    if re.search(pattern, cleaned, re.IGNORECASE):
                        new_lines.append(f"{target_library}=={target_version}")
                        matched = True
                        updated = True
                    else:
                        new_lines.append(line)
                if not matched and req_file.name == "requirements.txt":
                    new_lines.append(f"{target_library}=={target_version}")
                    updated = True
                req_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            except Exception:
                continue

    return updated
