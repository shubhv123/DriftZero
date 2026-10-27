# Autonomous Dependency & AST Ecosystem Analyzer
# Automatically discovers outdated dependencies, breaking major versions, and affected files
# dynamically for ANY Python repository without hardcoding library names or version numbers.

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests


def _fetch_pypi_latest_version(lib_name: str, timeout: float = 4.0) -> Optional[str]:
    """
    Fetches the latest stable release version of ANY Python library directly from PyPI.
    Uses requests with robust timeout handling.
    """
    clean_name = lib_name.strip().lower().replace("_", "-")
    url = f"https://pypi.org/pypi/{clean_name}/json"
    headers = {"User-Agent": "DriftZero-Autonomous-Migration-Engine/2.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("info", {}).get("version")
    except Exception:
        pass
    return None


def parse_manifest_dependencies(workspace_path: Path) -> Dict[str, Optional[str]]:
    """
    Parses declared dependencies and their pinned/minimum versions from requirements.txt,
    pyproject.toml, Pipfile, and setup.py recursively across the workspace.
    """
    deps: Dict[str, Optional[str]] = {}

    # 1. requirements*.txt (recursive across repo, ignoring venv/.git)
    candidates = [
        f for f in workspace_path.rglob("requirements*.txt")
        if not any(p in f.parts for p in (".venv", "venv", "env", "node_modules", ".git", "site-packages"))
    ]
    for req_file in candidates:
        if req_file.is_file():
            try:
                for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    cleaned = line.strip().split("#")[0].strip()
                    if not cleaned or cleaned.startswith("-"):
                        continue
                    # Match name and version constraint
                    m = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*([=><~^!]+)?\s*([\w\.\-]+)?", cleaned)
                    if m:
                        name = m.group(1).lower().replace("_", "-")
                        ver = m.group(3) if m.group(3) else None
                        deps[name] = ver
            except Exception:
                continue

    # 2. pyproject.toml
    for pyproject in workspace_path.rglob("pyproject.toml"):
        if any(p in pyproject.parts for p in (".venv", "venv", "env", "node_modules", ".git")):
            continue
        try:
            content = pyproject.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'["\']?([a-zA-Z0-9_\-\.]+)["\']?\s*[:=]\s*["\']([^"\'\s]+)["\']', content):
                name = m.group(1).lower().replace("_", "-")
                if name not in ("name", "version", "description", "authors", "python", "readme", "license"):
                    ver = m.group(2).lstrip("^~=>=<")
                    deps[name] = ver
        except Exception:
            pass

    # 3. Pipfile
    for pipfile in workspace_path.rglob("Pipfile"):
        if any(p in pipfile.parts for p in (".venv", "venv", "env", "node_modules", ".git")):
            continue
        try:
            content = pipfile.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'([a-zA-Z0-9_\-\.]+)\s*=\s*["\']([^"\'\s]+)["\']', content):
                name = m.group(1).lower().replace("_", "-")
                ver = m.group(2).lstrip("^~=>=<")
                if ver != "*":
                    deps[name] = ver
                elif name not in deps:
                    deps[name] = None
        except Exception:
            pass

    return deps


def _get_stdlib_names() -> Set[str]:
    """Returns the set of standard library module names for the current Python interpreter."""
    if hasattr(sys, "stdlib_module_names"):
        return set(sys.stdlib_module_names)
    return {
        "os", "sys", "re", "json", "ast", "math", "time", "datetime", "typing",
        "pathlib", "collections", "itertools", "functools", "shutil", "tempfile",
        "subprocess", "asyncio", "logging", "unittest", "traceback", "uuid",
        "urllib", "http", "copy", "io", "inspect", "dataclasses", "contextlib",
        "abc", "threading", "multiprocessing", "hashlib", "base64", "random",
        "socket", "ssl", "email", "csv", "xml", "html", "pickle", "sqlite3",
        "platform", "signal", "shlex", "glob", "fnmatch", "decimal", "queue"
    }


def _discover_local_modules(workspace_path: Path) -> Set[str]:
    """Identifies local repository files and subdirectories to avoid confusing local code with third-party libraries."""
    local_names = set()
    for py_file in workspace_path.rglob("*.py"):
        if any(p in py_file.parts for p in (".venv", "venv", "env", ".git", "__pycache__")):
            continue
        local_names.add(py_file.stem.lower())
        # Add parent directory names
        for parent in py_file.parents:
            if parent != workspace_path:
                local_names.add(parent.name.lower())
    return local_names


def scan_python_imports(workspace_path: Path) -> Dict[str, List[Path]]:
    """
    Scans all Python files using AST to identify all third-party packages imported,
    filtering out stdlib and internal local project files.
    Returns: mapping of package_name -> list of files importing it.
    """
    package_to_files: Dict[str, List[Path]] = {}
    stdlib = _get_stdlib_names()
    local_modules = _discover_local_modules(workspace_path)

    for py_file in workspace_path.rglob("*.py"):
        parts = py_file.parts
        if any(p in (".venv", "venv", "env", ".git", "__pycache__", "node_modules", "site-packages") for p in parts):
            continue

        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content)
        except Exception:
            continue

        file_packages = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_name = alias.name.split(".")[0].lower()
                    if top_name not in stdlib and top_name not in local_modules:
                        file_packages.add(top_name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_name = node.module.split(".")[0].lower()
                if top_name not in stdlib and top_name not in local_modules:
                    file_packages.add(top_name)

        for pkg in file_packages:
            clean_pkg = pkg.replace("_", "-")
            if clean_pkg not in package_to_files:
                package_to_files[clean_pkg] = []
            package_to_files[clean_pkg].append(py_file)

    return package_to_files


def calculate_version_delta(
    current_ver: Optional[str],
    latest_ver: Optional[str],
) -> Tuple[bool, int, str]:
    """
    Computes whether an upgrade is needed, its architectural severity score (0-100),
    and a descriptive explanation of the breaking-change delta.
    """
    if not latest_ver:
        return False, 0, "No latest release version available from PyPI."

    if not current_ver or current_ver.lower() == "legacy":
        return True, 50, "Unversioned import in repository; upgrade recommended."

    c_parts = re.findall(r"\d+", current_ver)
    l_parts = re.findall(r"\d+", latest_ver)

    if not c_parts or not l_parts:
        if current_ver != latest_ver:
            return True, 30, f"Version mismatch: {current_ver} -> {latest_ver}"
        return False, 0, "Already up-to-date."

    c_major = int(c_parts[0])
    l_major = int(l_parts[0])

    # 1. Major version bump (e.g. 1.x -> 2.x, or 0.x -> 1.x)
    if l_major > c_major:
        return True, 100, f"Major breaking version upgrade: v{c_major}.x -> v{l_major}.x"

    # 2. Minor version bump for 0.x libraries (e.g. 0.2 -> 0.3 or 0.115 -> 0.162)
    if c_major == 0 and l_major == 0:
        c_minor = int(c_parts[1]) if len(c_parts) > 1 else 0
        l_minor = int(l_parts[1]) if len(l_parts) > 1 else 0
        if l_minor > c_minor:
            return True, 80, f"Significant 0.x breaking evolution: 0.{c_minor} -> 0.{l_minor}"

    # 3. Standard minor version bump (e.g. 1.10 -> 1.12 or 2.3 -> 2.9)
    c_minor = int(c_parts[1]) if len(c_parts) > 1 else 0
    l_minor = int(l_parts[1]) if len(l_parts) > 1 else 0
    if l_minor > c_minor:
        return True, 50, f"Minor feature and deprecation update: {current_ver} -> {latest_ver}"

    # 4. Patch release (e.g. 2.9.1 -> 2.9.2)
    c_patch = int(c_parts[2]) if len(c_parts) > 2 else 0
    l_patch = int(l_parts[2]) if len(l_parts) > 2 else 0
    if l_patch > c_patch:
        return True, 20, f"Patch maintenance release: {current_ver} -> {latest_ver}"

    return False, 0, f"Current version {current_ver} is up to date with PyPI {latest_ver}."


def auto_detect_migration_target(
    workspace_path: Path,
) -> Tuple[str, str, Optional[str], List[Dict]]:
    """
    Universal Autonomous Dependency Discovery Engine:
    1. Scans manifests (requirements.txt, pyproject.toml, Pipfile) and all AST imports.
    2. Queries PyPI for latest releases of each detected third-party package.
    3. Dynamically ranks candidates by blast-radius (file count) and breaking version delta.
    4. Automatically selects the primary target without requiring manual user input.

    Returns:
        (target_library, target_version, old_version, all_candidates)
    """
    manifest_deps = parse_manifest_dependencies(workspace_path)
    imported_pkgs = scan_python_imports(workspace_path)

    # Normalize package names (e.g. 'pydantic-settings' vs 'pydantic_settings')
    all_candidate_names = set(manifest_deps.keys()) | set(imported_pkgs.keys())

    if not all_candidate_names:
        raise ValueError(
            f"No third-party Python dependencies or imports were detected in repository '{workspace_path.name}'."
        )

    scored_candidates = []

    for lib_name in all_candidate_names:
        affected_files = imported_pkgs.get(lib_name, [])
        # Also check with underscores/hyphens if not matched directly
        if not affected_files:
            alt_name = lib_name.replace("-", "_") if "-" in lib_name else lib_name.replace("_", "-")
            affected_files = imported_pkgs.get(alt_name, [])

        file_count = len(affected_files)
        current_ver = manifest_deps.get(lib_name)

        # Query live PyPI
        latest_ver = _fetch_pypi_latest_version(lib_name)
        if not latest_ver:
            alt_name = lib_name.replace("-", "_") if "-" in lib_name else lib_name.replace("_", "-")
            latest_ver = _fetch_pypi_latest_version(alt_name)

        # If PyPI is completely unreachable for this package, fall back to current_ver
        if not latest_ver:
            if current_ver:
                latest_ver = current_ver
            else:
                continue

        is_outdated, severity_score, reason = calculate_version_delta(current_ver, latest_ver)

        # Dynamic ranking score:
        # - Files importing it in the project (30 pts per file)
        # - Breaking version delta severity (up to 100 pts)
        # - Explicitly declared in requirements manifest (25 pts)
        total_score = (file_count * 30) + severity_score + (25 if lib_name in manifest_deps else 0)

        scored_candidates.append({
            "library": lib_name,
            "current_version": current_ver or "legacy",
            "latest_version": latest_ver,
            "affected_files_count": file_count,
            "needs_major_upgrade": is_outdated,
            "severity_score": severity_score,
            "upgrade_reason": reason,
            "ranking_score": total_score,
            "affected_file_paths": [str(p.relative_to(workspace_path)) for p in affected_files],
        })

    # Sort candidates: prioritize packages that actually have importing files in the codebase,
    # then rank by score (outdated severity + blast radius)
    scored_candidates.sort(
        key=lambda c: (c["affected_files_count"] > 0, c["ranking_score"]),
        reverse=True,
    )

    if not scored_candidates:
        raise ValueError(
            f"Unable to resolve valid third-party Python dependencies for repository '{workspace_path.name}'."
        )

    primary = scored_candidates[0]
    target_lib = primary["library"]
    target_ver = primary["latest_version"]
    old_ver = primary["current_version"] if primary["current_version"] != "legacy" else None

    return target_lib, target_ver, old_ver, scored_candidates
