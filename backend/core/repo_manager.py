# Clones target GitHub repo, parses requirements.txt/pyproject.toml, manages manifests & workspaces
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

GITHUB_URL_REGEX = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$")

# Known ecosystem version conflict matrices
ECOSYSTEM_CONFLICTS = {
    "pydantic": {
        "2": [
            ("fastapi", (0, 100, 0), "requires pydantic<2.0.0; recommended: upgrade to fastapi>=0.100.0"),
            ("sqlmodel", (0, 0, 14), "requires pydantic<2.0.0; recommended: upgrade to sqlmodel>=0.0.14"),
            ("langchain", (0, 0, 267), "requires pydantic<2.0.0; recommended: upgrade to langchain>=0.0.267"),
            ("instructor", (0, 3, 0), "requires pydantic<2.0.0; recommended: upgrade to instructor>=0.3.0"),
            ("pydantic-factories", None, "deprecated and incompatible with Pydantic v2; migrate to 'polyfactory'"),
            ("spacy", (3, 7, 0), "requires pydantic<2.0.0; recommended: upgrade to spacy>=3.7.0"),
            ("beanie", (1, 21, 0), "requires pydantic<2.0.0; recommended: upgrade to beanie>=1.21.0"),
        ]
    },
    "sqlalchemy": {
        "2": [
            ("databases", (0, 7, 0), "requires sqlalchemy<2.0.0; recommended: upgrade to databases>=0.7.0"),
            ("alembic", (1, 10, 0), "requires sqlalchemy<2.0.0; recommended: upgrade to alembic>=1.10.0"),
            ("flask-sqlalchemy", (3, 0, 0), "requires sqlalchemy<2.0.0; recommended: upgrade to flask-sqlalchemy>=3.0.0"),
        ]
    },
}

# Companion packages needed for major version upgrades
COMPANION_PACKAGES = {
    "pydantic": {
        "2": [("pydantic-settings", ">=2.0.0")],
    },
    "sqlalchemy": {
        "2": [("greenlet", ">=2.0.0")],
    },
}


class ManifestUpdateResult:
    """
    Result returned by update_manifest_version.
    Maintains backwards compatibility with bool evaluations and tuple unpacking.
    """
    def __init__(
        self,
        updated: bool,
        conflicts: List[str],
        companion_packages_added: List[str],
        manifests_updated: List[str],
    ):
        self.updated = updated
        self.conflicts = conflicts
        self.companion_packages_added = companion_packages_added
        self.manifests_updated = manifests_updated

    def __bool__(self) -> bool:
        return self.updated

    def __iter__(self):
        yield self.updated
        yield self.conflicts

    def __repr__(self) -> str:
        return f"<ManifestUpdateResult updated={self.updated} conflicts={len(self.conflicts)} companions={len(self.companion_packages_added)}>"


def is_git_url(repo_str: str) -> bool:
    if not repo_str:
        return False
    s = repo_str.strip()
    return (
        s.startswith("http://")
        or s.startswith("https://")
        or s.startswith("git@")
        or s.endswith(".git")
        or "github.com" in s
    )


def _is_path_allowlisted(candidate: Path) -> bool:
    """
    Checks if candidate path is inside an allowlisted directory.
    Allowlist source: ALLOWED_LOCAL_REPO_DIRS (comma-, colon-, or semicolon-separated paths).
    Default allowlist if env unset: DriftZero project root, current working dir, and system tempdir.
    """
    allowed_env = os.getenv("ALLOWED_LOCAL_REPO_DIRS", "")
    allowed_roots: List[Path] = []
    if allowed_env.strip():
        for part in re.split(r"[,;:]", allowed_env):
            part = part.strip()
            if part:
                allowed_roots.append(Path(part).resolve())
    else:
        project_root = Path(__file__).resolve().parent.parent.parent
        allowed_roots.append(project_root.resolve())
        allowed_roots.append(Path.cwd().resolve())
        allowed_roots.append(Path(tempfile.gettempdir()).resolve())

    candidate_res = candidate.resolve()
    for root in allowed_roots:
        try:
            candidate_res.relative_to(root)
            return True
        except ValueError:
            if candidate_res == root:
                return True
    return False


def _strip_git_token(workspace_path: Path, clean_url: str) -> None:
    """
    Strips embedded personal access tokens from .git/config after clone.
    """
    git_dir = workspace_path / ".git"
    if not git_dir.exists():
        return

    # 1. Update origin remote URL to clean URL
    subprocess.run(
        ["git", "remote", "set-url", "origin", clean_url],
        cwd=str(workspace_path),
        capture_output=True,
        text=True,
    )

    # 2. Sanitize .git/config file directly to eliminate any credential leaks
    config_file = git_dir / "config"
    if config_file.exists():
        content = config_file.read_text(encoding="utf-8", errors="ignore")
        if "@github.com" in content:
            sanitized = re.sub(r"https?://[^@]+@github\.com", "https://github.com", content)
            config_file.write_text(sanitized, encoding="utf-8")


def cleanup_workspace(workspace_path: Optional[Path]) -> None:
    """
    Safely deletes an ephemeral workspace directory and its companion patched workspace.
    Guards against deleting non-ephemeral directories outside temp/ephemeral root.
    """
    if not workspace_path:
        return
    p = Path(workspace_path).resolve()
    temp_dir = Path(tempfile.gettempdir()).resolve()

    is_safe = False
    try:
        p.relative_to(temp_dir)
        is_safe = True
    except ValueError:
        if "driftzero_workspaces" in str(p):
            is_safe = True

    if not is_safe:
        print(f"[Repo Manager] Safety Notice: Refusing to delete non-ephemeral directory: {p}")
        return

    if p.exists() and p.is_dir():
        shutil.rmtree(p, ignore_errors=True)

    patched_dir = p.parent / f"patched_{p.name}"
    if patched_dir.exists() and patched_dir.is_dir():
        shutil.rmtree(patched_dir, ignore_errors=True)


@contextmanager
def managed_workspace(
    repo_input: Optional[str] = None,
    default_fallback_dir: Optional[Path] = None,
    ephemeral_parent_dir: Optional[Path] = None,
    github_token: Optional[str] = None,
):
    """
    Context manager that prepares an ephemeral workspace and guarantees deletion on exit.
    """
    ws_path, is_ephem = prepare_repo_workspace(repo_input, default_fallback_dir, ephemeral_parent_dir, github_token)
    try:
        yield ws_path
    finally:
        if is_ephem:
            cleanup_workspace(ws_path)


def prepare_repo_workspace(
    repo_input: Optional[str] = None,
    default_fallback_dir: Optional[Path] = None,
    ephemeral_parent_dir: Optional[Path] = None,
    github_token: Optional[str] = None,
) -> Tuple[Path, bool]:
    r"""
    Clones a remote GitHub repository or copies a local repository into an ephemeral workspace.
    Validates remote URLs against ^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$.
    Rejects local paths unless ALLOW_LOCAL_REPOS=1 and path is inside an allowlisted directory.
    Strips access tokens from .git/config after clone.
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
            allow_local = os.getenv("ALLOW_LOCAL_REPOS", "0").strip().lower() in ("1", "true", "yes")
            if not allow_local:
                raise ValueError("Local fallback repository disallowed: ALLOW_LOCAL_REPOS=1 is required.")
            if not _is_path_allowlisted(default_fallback_dir):
                raise ValueError(f"Fallback repository '{default_fallback_dir}' is not inside an allowlisted directory.")
            shutil.copytree(default_fallback_dir, workspace_path, dirs_exist_ok=True)
            return workspace_path, True
        raise ValueError("Missing repository input: A valid GitHub repository URL matching ^https://github.com/[\\w.-]+/[\\w.-]+(\\.git)?$ must be provided.")

    repo_str = str(repo_input).strip()

    # 2. Remote Git URL
    if is_git_url(repo_str):
        # Validate that repo_url matches ^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$
        if not GITHUB_URL_REGEX.match(repo_str):
            raise ValueError(
                f"Invalid repository URL '{repo_str}'. Must match pattern: ^https://github\\.com/[\\w.-]+/[\\w.-]+(\\.git)?$"
            )

        workspace_path.mkdir(parents=True, exist_ok=True)
        git_url = repo_str
        if github_token and "@" not in git_url:
            git_url = git_url.replace("https://github.com", f"https://x-access-token:{github_token}@github.com")

        clone_env = os.environ.copy()
        clone_env["GIT_TERMINAL_PROMPT"] = "0"
        clone_env["GIT_ASKPASS"] = "echo"

        clone_cmd = ["git", "clone", "--depth", "1", git_url, str(workspace_path)]
        try:
            res = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=30, env=clone_env)
        except subprocess.TimeoutExpired:
            cleanup_workspace(workspace_path)
            raise RuntimeError(f"Git clone for '{repo_str}' timed out after 30 seconds.")

        if res.returncode != 0:
            cleanup_workspace(workspace_path)
            err_msg = (res.stderr or res.stdout or "").strip()
            if "terminal prompts disabled" in err_msg or "Authentication failed" in err_msg or "not found" in err_msg.lower():
                raise RuntimeError(
                    f"Repository '{repo_str}' could not be accessed. The repository does not exist, was mistyped, or is private (requires authentication)."
                )
            raise RuntimeError(f"Failed to clone repository '{repo_str}': {err_msg}")

        # Strip the token from .git/config after clone
        _strip_git_token(workspace_path, repo_str)

        return workspace_path, True

    # 3. Local directory: Reject unless ALLOW_LOCAL_REPOS=1 and inside allowlisted directory
    allow_local = os.getenv("ALLOW_LOCAL_REPOS", "0").strip().lower() in ("1", "true", "yes")
    if not allow_local:
        raise ValueError(
            f"Local repository path '{repo_str}' rejected. Local paths are disallowed unless ALLOW_LOCAL_REPOS=1 and the path is inside an allowlisted directory."
        )

    candidate = Path(repo_str).resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"Local repository path '{repo_str}' does not exist or is not a valid directory.")

    if not _is_path_allowlisted(candidate):
        raise ValueError(
            f"Local repository path '{candidate}' is not inside an allowlisted directory. "
            f"Configure ALLOWED_LOCAL_REPO_DIRS or place repositories in approved workspace directories."
        )

    shutil.copytree(candidate, workspace_path, dirs_exist_ok=True)
    return workspace_path, True


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

    # 2. Check pyproject.toml (Poetry & PEP 621)
    for pyproject in repo_path.rglob("pyproject.toml"):
        if any(p in pyproject.parts for p in (".venv", "venv", "env", "node_modules", ".git")):
            continue
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            # Poetry: pydantic = "^1.10.12"
            pattern_poetry = rf'["\']?{re.escape(lib_name)}["\']?\s*[:=]\s*["\']([^"\'\s]+)["\']'
            match = re.search(pattern_poetry, content, re.IGNORECASE)
            if match:
                return match.group(1).lstrip("^~=>=<")
            # PEP 621: "pydantic>=1.10.12"
            pattern_pep = rf'["\']{re.escape(lib_name)}\s*([=><~^!]+)\s*([\w\.\-]+)'
            match_pep = re.search(pattern_pep, content, re.IGNORECASE)
            if match_pep:
                return match_pep.group(2)
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


def _parse_version_tuple(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums) if nums else (0,)


def _collect_pinned_packages(repo_path: Path) -> Dict[str, List[Tuple[str, str, str]]]:
    """
    Parses pinned packages across requirements*.txt and pyproject.toml.
    Returns mapping: pkg_name -> list of (version_str, full_spec_line, source_file)
    """
    pins: Dict[str, List[Tuple[str, str, str]]] = {}

    # 1. requirements*.txt
    for req_file in repo_path.rglob("requirements*.txt"):
        if any(p in req_file.parts for p in (".venv", "venv", "env", "node_modules", ".git")):
            continue
        try:
            for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                cleaned = line.strip().split("#")[0].strip()
                if not cleaned:
                    continue
                m = re.match(r"^([\w\.\-]+)\s*([=><~^!]+)\s*([\w\.\-]+)", cleaned)
                if m:
                    pkg = m.group(1).lower().replace("_", "-")
                    ver = m.group(3)
                    pins.setdefault(pkg, []).append((ver, cleaned, req_file.name))
        except Exception:
            pass

    # 2. pyproject.toml
    for pyproject in repo_path.rglob("pyproject.toml"):
        if any(p in pyproject.parts for p in (".venv", "venv", "env", "node_modules", ".git")):
            continue
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            # Poetry style: pkg = "^1.2.3"
            for m in re.finditer(r'([\w\.\-]+)\s*=\s*["\']([^"\'\s]+)["\']', content):
                pkg = m.group(1).lower().replace("_", "-")
                if pkg in ("python", "name", "version", "description"):
                    continue
                ver = m.group(2).lstrip("^~=>=<")
                pins.setdefault(pkg, []).append((ver, m.group(0), pyproject.name))
            # PEP 621 style: "pkg>=1.2.3"
            for m in re.finditer(r'["\']([\w\.\-]+)\s*([=><~^!]+)\s*([\w\.\-]+)', content):
                pkg = m.group(1).lower().replace("_", "-")
                ver = m.group(3)
                pins.setdefault(pkg, []).append((ver, m.group(0), pyproject.name))
        except Exception:
            pass

    return pins


def check_manifest_conflicts(
    repo_path: Path,
    target_library: str,
    target_version: str,
) -> List[str]:
    """
    Inspects repo manifests for pinned dependencies that conflict with the upgraded target library.
    """
    conflicts: List[str] = []
    lib_name = target_library.strip().lower().replace("_", "-")
    target_major = target_version.split(".")[0]

    pinned_packages = _collect_pinned_packages(repo_path)

    # 1. Direct restrictive pin on target library itself (e.g. <2.0.0)
    if lib_name in pinned_packages:
        for ver_str, full_line, src_file in pinned_packages[lib_name]:
            if "<" in full_line:
                upper_match = re.search(r"<\s*([\w\.\-]+)", full_line)
                if upper_match:
                    upper_ver = _parse_version_tuple(upper_match.group(1))
                    target_ver_tuple = _parse_version_tuple(target_version)
                    if target_ver_tuple >= upper_ver:
                        conflicts.append(
                            f"{src_file}: Pin '{full_line}' enforces upper bound below target version {target_version}."
                        )

    # 2. Ecosystem conflicts
    rules = ECOSYSTEM_CONFLICTS.get(lib_name, {}).get(target_major, [])
    for conflict_pkg, min_ver, reason in rules:
        conflict_pkg_name = conflict_pkg.lower().replace("_", "-")
        if conflict_pkg_name in pinned_packages:
            for ver_str, full_line, src_file in pinned_packages[conflict_pkg_name]:
                if min_ver is None:
                    conflicts.append(f"{src_file}: Package '{full_line}' {reason}.")
                else:
                    curr_ver = _parse_version_tuple(ver_str)
                    if curr_ver < min_ver:
                        conflicts.append(
                            f"{src_file}: '{full_line}' conflicts with {target_library} {target_version} ({reason})."
                        )

    return conflicts


def _add_companion_packages(
    repo_path: Path,
    target_library: str,
    target_version: str,
) -> List[str]:
    """
    Adds required companion packages (e.g. pydantic-settings for Pydantic v2) to manifests.
    """
    added: List[str] = []
    lib_name = target_library.strip().lower().replace("_", "-")
    target_major = target_version.split(".")[0]

    companions = COMPANION_PACKAGES.get(lib_name, {}).get(target_major, [])
    if not companions:
        return added

    for companion_pkg, companion_ver in companions:
        companion_spec = f"{companion_pkg}{companion_ver}"

        # 1. Update requirements*.txt
        req_files = [
            f for f in repo_path.rglob("requirements*.txt")
            if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git"))
        ]
        for req_file in req_files:
            try:
                content = req_file.read_text(encoding="utf-8", errors="ignore")
                if companion_pkg not in content:
                    addition = ("\n" if content and not content.endswith("\n") else "") + companion_spec + "\n"
                    req_file.write_text(content + addition, encoding="utf-8")
                    if companion_spec not in added:
                        added.append(f"{companion_spec} (added to {req_file.name})")
            except Exception:
                pass

        # 2. Update pyproject.toml
        pyproject_files = [
            f for f in repo_path.rglob("pyproject.toml")
            if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git"))
        ]
        for pyproject in pyproject_files:
            try:
                content = pyproject.read_text(encoding="utf-8", errors="ignore")
                if companion_pkg not in content:
                    if "[tool.poetry.dependencies]" in content:
                        clean_ver = companion_ver.lstrip(">=<~^")
                        sub_pat = r"(\[tool\.poetry\.dependencies\]\n)"
                        replacement = f'\\1{companion_pkg} = "^{clean_ver}"\n'
                        new_content = re.sub(sub_pat, replacement, content)
                        pyproject.write_text(new_content, encoding="utf-8")
                        if companion_spec not in added:
                            added.append(f"{companion_spec} (added to {pyproject.name})")
                    elif "dependencies = [" in content:
                        sub_pat = r"(dependencies\s*=\s*\[\n)"
                        replacement = f'\\1    "{companion_spec}",\n'
                        new_content = re.sub(sub_pat, replacement, content)
                        pyproject.write_text(new_content, encoding="utf-8")
                        if companion_spec not in added:
                            added.append(f"{companion_spec} (added to {pyproject.name})")
            except Exception:
                pass

    return added


def _bump_pyproject_toml(
    repo_path: Path,
    target_library: str,
    target_version: str,
) -> List[str]:
    """
    Bumps the target library version in all pyproject.toml files (Poetry and PEP 621).
    """
    updated_files: List[str] = []
    lib_name = target_library.strip().lower().replace("_", "-")

    pyprojects = [
        f for f in repo_path.rglob("pyproject.toml")
        if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git"))
    ]

    for pyproject in pyprojects:
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            original_content = content

            # 1. Poetry simple: pydantic = "^1.10" or pydantic = ">=1.10"
            pat_poetry = rf'(["\']?{re.escape(lib_name)}["\']?\s*=\s*)(["\'])([^"\']+)(["\'])'
            if re.search(pat_poetry, content, re.IGNORECASE):
                content = re.sub(pat_poetry, rf'\g<1>"^{target_version}"', content, flags=re.IGNORECASE)

            # 2. Poetry inline table: pydantic = { version = "^1.10", ... }
            pat_table = rf'({re.escape(lib_name)}\s*=\s*\{{[^}}]*version\s*=\s*)(["\'])([^"\']+)(["\'])'
            if re.search(pat_table, content, re.IGNORECASE):
                content = re.sub(pat_table, rf'\g<1>"^{target_version}"', content, flags=re.IGNORECASE)

            # 3. PEP 621 array: "pydantic>=1.10.0,<2.0.0" or 'pydantic==1.10'
            pat_pep621 = rf'["\']{re.escape(lib_name)}\s*([=><~^!]+)\s*[^"\'\s]+(?:,\s*[=><~^!]+[^"\'\s]+)?["\']'
            if re.search(pat_pep621, content, re.IGNORECASE):
                content = re.sub(pat_pep621, f'"{target_library}>={target_version}"', content, flags=re.IGNORECASE)

            if content != original_content:
                pyproject.write_text(content, encoding="utf-8")
                updated_files.append(pyproject.name)
        except Exception as exc:
            print(f"[Repo Manager] Could not bump {pyproject.name}: {exc}")

    return updated_files


def update_manifest_version(
    repo_path: Path,
    target_library: str,
    target_version: str,
) -> ManifestUpdateResult:
    """
    Updates the version string in requirements.txt and pyproject.toml to target_version.
    Adds required companion packages (e.g. pydantic-settings for Pydantic v2).
    Reports all other pinned packages that conflict with the upgrade.
    """
    lib_name = target_library.strip().lower().replace("_", "-")
    manifests_updated: List[str] = []

    # 1. Inspect conflicts before bumping
    conflicts = check_manifest_conflicts(repo_path, target_library, target_version)

    # 2. Update requirements*.txt recursively
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
                        if req_file.name not in manifests_updated:
                            manifests_updated.append(req_file.name)
                    else:
                        new_lines.append(line)
                if not matched and req_file.name == "requirements.txt":
                    new_lines.append(f"{target_library}=={target_version}")
                    if req_file.name not in manifests_updated:
                        manifests_updated.append(req_file.name)
                req_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            except Exception:
                continue

    # 3. Bump pyproject.toml
    bumped_pyprojects = _bump_pyproject_toml(repo_path, target_library, target_version)
    for p_name in bumped_pyprojects:
        if p_name not in manifests_updated:
            manifests_updated.append(p_name)

    # 4. Add companion packages (e.g., pydantic-settings)
    companions_added = _add_companion_packages(repo_path, target_library, target_version)

    updated = len(manifests_updated) > 0 or len(companions_added) > 0

    return ManifestUpdateResult(
        updated=updated,
        conflicts=conflicts,
        companion_packages_added=companions_added,
        manifests_updated=manifests_updated,
    )
