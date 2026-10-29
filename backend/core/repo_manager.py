import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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
        auto_bumped_packages: Optional[List[str]] = None,
        status: str = "SUCCESS",
        needs_manual: bool = False,
    ):
        self.updated = updated
        self.conflicts = conflicts
        self.companion_packages_added = companion_packages_added
        self.manifests_updated = manifests_updated
        self.auto_bumped_packages = auto_bumped_packages or []
        self.status = status
        self.needs_manual = needs_manual

    def __bool__(self) -> bool:
        return self.updated

    def __iter__(self):
        yield self.updated
        yield self.conflicts

    def __repr__(self) -> str:
        return (
            f"<ManifestUpdateResult updated={self.updated} "
            f"conflicts={len(self.conflicts)} "
            f"companions={len(self.companion_packages_added)} "
            f"auto_bumped={len(self.auto_bumped_packages)} "
            f"status={self.status}>"
        )


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


def is_openai_v1_migrated(repo_path: Path) -> bool:
    """
    Detects if the repository code has been migrated to the OpenAI v1 client.
    Checks for v1 client patterns like `OpenAI()`, `from openai import OpenAI`,
    `AsyncOpenAI`, `AzureOpenAI`, `client.chat.completions.create`, etc.
    """
    v1_import_names = {"OpenAI", "AsyncOpenAI", "AzureOpenAI"}
    v1_regex = re.compile(
        r"(?:from\s+openai\s+import\s+.*?\b(OpenAI|AsyncOpenAI|AzureOpenAI)\b)|"
        r"(?:\bopenai\.(?:OpenAI|AsyncOpenAI|AzureOpenAI)\b)|"
        r"(?:\b(?:OpenAI|AsyncOpenAI|AzureOpenAI)\s*\()|"
        r"(?:\.chat\.completions\.create\s*\()|"
        r"(?:\.embeddings\.create\s*\()",
        re.DOTALL,
    )

    py_files = [
        f for f in repo_path.rglob("*.py")
        if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git", "__pycache__"))
    ]

    for py_file in py_files:
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if not content.strip():
                continue
            # Regex fast check
            if v1_regex.search(content):
                return True

            # AST parse check for deeper semantic confirmation
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == "openai":
                        if any(alias.name in v1_import_names for alias in node.names):
                            return True
                    elif isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and node.func.id in v1_import_names:
                            return True
                        elif isinstance(node.func, ast.Attribute) and node.func.attr in v1_import_names:
                            return True
            except Exception:
                pass
        except Exception:
            continue

    return False


def _bump_pyproject_toml(
    repo_path: Path,
    target_library: str,
    target_version: str,
    packages_to_bump: Optional[Dict[str, Tuple[str, Tuple[int, ...], str]]] = None,
    auto_bumped_packages: Optional[List[str]] = None,
) -> List[str]:
    """
    Bumps the target library version and ecosystem packages in all pyproject.toml files (Poetry and PEP 621).
    """
    updated_files: List[str] = []
    lib_name = target_library.strip().lower().replace("_", "-")
    packages_to_bump = packages_to_bump or {}

    pyprojects = [
        f for f in repo_path.rglob("pyproject.toml")
        if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git"))
    ]

    for pyproject in pyprojects:
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            original_content = content

            # 1. Bump target library
            # Poetry simple: pydantic = "^1.10" or pydantic = ">=1.10"
            pat_poetry = rf'(["\']?{re.escape(lib_name)}["\']?\s*=\s*)(["\'])([^"\']+)(["\'])'
            if re.search(pat_poetry, content, re.IGNORECASE):
                content = re.sub(pat_poetry, rf'\g<1>"^{target_version}"', content, flags=re.IGNORECASE)

            # Poetry inline table: pydantic = { version = "^1.10", ... }
            pat_table = rf'({re.escape(lib_name)}\s*=\s*\{{[^}}]*version\s*=\s*)(["\'])([^"\']+)(["\'])'
            if re.search(pat_table, content, re.IGNORECASE):
                content = re.sub(pat_table, rf'\g<1>"^{target_version}"', content, flags=re.IGNORECASE)

            # PEP 621 array: "pydantic>=1.10.0,<2.0.0" or 'pydantic==1.10'
            pat_pep621 = rf'["\']{re.escape(lib_name)}\s*([=><~^!]+)\s*[^"\'\s]+(?:,\s*[=><~^!]+[^"\'\s]+)?["\']'
            if re.search(pat_pep621, content, re.IGNORECASE):
                content = re.sub(pat_pep621, f'"{target_library}>={target_version}"', content, flags=re.IGNORECASE)

            # 2. Bump packages_to_bump
            for pkg_key, (display_name, min_ver_tuple, min_ver_str) in packages_to_bump.items():
                # Poetry simple
                pkg_pat_poetry = rf'(["\']?{re.escape(pkg_key)}["\']?\s*=\s*)(["\'])([^"\']+)(["\'])'

                def _sub_poetry(match):
                    orig_ver = match.group(3).lstrip("^~=>=<")
                    curr = _parse_version_tuple(orig_ver) if orig_ver else (0,)
                    if curr < min_ver_tuple or "<" in match.group(3) or orig_ver in ("*", ""):
                        bump_spec = f"{display_name}>={min_ver_str}"
                        if auto_bumped_packages is not None and bump_spec not in auto_bumped_packages:
                            auto_bumped_packages.append(bump_spec)
                        return rf'{match.group(1)}"^{min_ver_str}"'
                    return match.group(0)

                content = re.sub(pkg_pat_poetry, _sub_poetry, content, flags=re.IGNORECASE)

                # Poetry table
                pkg_pat_table = rf'({re.escape(pkg_key)}\s*=\s*\{{[^}}]*version\s*=\s*)(["\'])([^"\']+)(["\'])'

                def _sub_table(match):
                    orig_ver = match.group(3).lstrip("^~=>=<")
                    curr = _parse_version_tuple(orig_ver) if orig_ver else (0,)
                    if curr < min_ver_tuple or "<" in match.group(3) or orig_ver in ("*", ""):
                        bump_spec = f"{display_name}>={min_ver_str}"
                        if auto_bumped_packages is not None and bump_spec not in auto_bumped_packages:
                            auto_bumped_packages.append(bump_spec)
                        return rf'{match.group(1)}"^{min_ver_str}"'
                    return match.group(0)

                content = re.sub(pkg_pat_table, _sub_table, content, flags=re.IGNORECASE)

                # PEP 621 array
                pkg_pat_pep621 = rf'["\']{re.escape(pkg_key)}(\[[^\]]*\])?\s*([=><~^!]+)?\s*([^"\'\s,]*)(?:,\s*[=><~^!]+[^"\'\s]+)?["\']'

                def _sub_pep621(match):
                    extras = match.group(1) or ""
                    orig_ver = match.group(3) or ""
                    curr = _parse_version_tuple(orig_ver) if orig_ver else (0,)
                    if curr < min_ver_tuple or "<" in match.group(0) or not orig_ver:
                        bump_spec = f"{display_name}>={min_ver_str}"
                        if auto_bumped_packages is not None and bump_spec not in auto_bumped_packages:
                            auto_bumped_packages.append(bump_spec)
                        return f'"{display_name}{extras}>={min_ver_str}"'
                    return match.group(0)

                content = re.sub(pkg_pat_pep621, _sub_pep621, content, flags=re.IGNORECASE)

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
    Auto-bumps every package found in ECOSYSTEM_CONFLICTS to its recommended minimum,
    and openai if the codebase was migrated to the v1 client.
    Adds required companion packages (e.g. pydantic-settings for Pydantic v2).
    Runs `pip install --dry-run -r` and reports failure as NEEDS_MANUAL.
    """
    lib_name = target_library.strip().lower().replace("_", "-")
    target_major = target_version.lstrip("v").split(".")[0]
    manifests_updated: List[str] = []
    auto_bumped_packages: List[str] = []

    # 1. Determine packages to auto-bump
    packages_to_bump: Dict[str, Tuple[str, Tuple[int, ...], str]] = {}

    # Packages from ECOSYSTEM_CONFLICTS
    rules = ECOSYSTEM_CONFLICTS.get(lib_name, {}).get(target_major, [])
    if not rules and len(ECOSYSTEM_CONFLICTS.get(lib_name, {})) == 1:
        rules = next(iter(ECOSYSTEM_CONFLICTS[lib_name].values()))

    for conflict_pkg, min_ver, reason in rules:
        if min_ver is not None:
            min_ver_str = ".".join(str(n) for n in min_ver)
            conflict_key = conflict_pkg.strip().lower().replace("_", "-")
            packages_to_bump[conflict_key] = (conflict_pkg, min_ver, min_ver_str)

    # OpenAI v1 client migration auto-bump
    if is_openai_v1_migrated(repo_path) and lib_name != "openai":
        packages_to_bump["openai"] = ("openai", (1, 0, 0), "1.0.0")

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
                matched_target = False
                file_modified = False

                for line in lines:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        new_lines.append(line)
                        continue

                    cleaned = stripped.split("#")[0].strip()
                    comment = ("  #" + stripped.split("#", 1)[1]) if "#" in stripped else ""

                    # Match target library
                    target_pat = rf"^{re.escape(lib_name)}(\[[^\]]*\])?\s*([=><~^!].*)?$"
                    target_m = re.match(target_pat, cleaned, re.IGNORECASE)
                    if target_m:
                        extras = target_m.group(1) or ""
                        new_line = f"{target_library}{extras}=={target_version}{comment}"
                        new_lines.append(new_line)
                        matched_target = True
                        if new_line != line:
                            file_modified = True
                        continue

                    # Match packages to auto-bump
                    bump_m = re.match(r"^([\w\.\-]+)(\[[^\]]*\])?\s*([=><~^!]+)?\s*([\w\.\-]*)", cleaned)
                    if bump_m:
                        pkg_key = bump_m.group(1).lower().replace("_", "-")
                        if pkg_key in packages_to_bump:
                            display_name, min_ver_tuple, min_ver_str = packages_to_bump[pkg_key]
                            extras = bump_m.group(2) or ""
                            op = bump_m.group(3) or ""
                            ver_str = bump_m.group(4) or ""
                            curr_ver = _parse_version_tuple(ver_str) if ver_str else (0,)

                            needs_bump = (
                                not ver_str
                                or "<" in cleaned
                                or curr_ver < min_ver_tuple
                                or (op == "==" and curr_ver <= min_ver_tuple)
                            )
                            if needs_bump:
                                bump_spec = f"{display_name}{extras}>={min_ver_str}"
                                new_line = f"{bump_spec}{comment}"
                                new_lines.append(new_line)
                                if bump_spec not in auto_bumped_packages:
                                    auto_bumped_packages.append(bump_spec)
                                file_modified = True
                                continue

                    new_lines.append(line)

                if not matched_target and req_file.name == "requirements.txt":
                    new_lines.append(f"{target_library}=={target_version}")
                    file_modified = True

                if file_modified:
                    req_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    if req_file.name not in manifests_updated:
                        manifests_updated.append(req_file.name)
            except Exception as exc:
                print(f"[Repo Manager] Could not update {req_file.name}: {exc}")

    # 3. Bump pyproject.toml
    bumped_pyprojects = _bump_pyproject_toml(
        repo_path,
        target_library,
        target_version,
        packages_to_bump=packages_to_bump,
        auto_bumped_packages=auto_bumped_packages,
    )
    for p_name in bumped_pyprojects:
        if p_name not in manifests_updated:
            manifests_updated.append(p_name)

    # 4. Add companion packages (e.g., pydantic-settings)
    companions_added = _add_companion_packages(repo_path, target_library, target_version)

    # 5. Check remaining manifest conflicts (e.g. unresolvable packages with min_ver=None)
    conflicts = check_manifest_conflicts(repo_path, target_library, target_version)

    # 6. Run pip install --dry-run -r on updated requirements files and report failure as NEEDS_MANUAL
    status = "SUCCESS"
    needs_manual = False

    for req_file in req_candidates:
        if not req_file.is_file():
            continue
        try:
            cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "-r", str(req_file.resolve())]
            proc = subprocess.run(
                cmd,
                cwd=str(req_file.parent.resolve()),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                err_output = (proc.stderr or proc.stdout).strip()
                status = "NEEDS_MANUAL"
                needs_manual = True
                conflicts.append(
                    f"NEEDS_MANUAL: pip install --dry-run -r {req_file.name} failed (exit code {proc.returncode}):\n{err_output}"
                )
        except subprocess.TimeoutExpired:
            status = "NEEDS_MANUAL"
            needs_manual = True
            conflicts.append(
                f"NEEDS_MANUAL: pip install --dry-run -r {req_file.name} timed out after 30s."
            )
        except Exception as exc:
            status = "NEEDS_MANUAL"
            needs_manual = True
            conflicts.append(
                f"NEEDS_MANUAL: pip install --dry-run -r {req_file.name} error: {exc}"
            )

    updated = len(manifests_updated) > 0 or len(companions_added) > 0

    return ManifestUpdateResult(
        updated=updated,
        conflicts=conflicts,
        companion_packages_added=companions_added,
        manifests_updated=manifests_updated,
        auto_bumped_packages=auto_bumped_packages,
        status=status,
        needs_manual=needs_manual,
    )
