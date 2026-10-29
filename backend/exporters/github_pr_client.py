# Creates a new branch, commits patched files + rules, opens PR
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from backend.schemas.migration_models import MigrationDelta, Stage4VerificationReport


def _run_git(
    args: list[str],
    cwd: Path,
    check: bool = True,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """
    Executes a git command and checks the returncode.
    Ensures non-interactive operation via GIT_TERMINAL_PROMPT=0.
    """
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    full_env["GIT_TERMINAL_PROMPT"] = "0"

    proc = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=full_env,
    )
    if check and proc.returncode != 0:
        cmd_str = " ".join(["git"] + args)
        err = proc.stderr.strip() or proc.stdout.strip()
        raise subprocess.CalledProcessError(
            proc.returncode, cmd_str, output=proc.stdout, stderr=err
        )
    return proc


def _ensure_gitignore_filters(workspace_dir: Path) -> None:
    """
    Adds filters for __pycache__, bytecode, and .pytest_cache to .gitignore
    and untracks any cached directories from the git index.
    """
    gitignore_path = workspace_dir / ".gitignore"
    existing_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""

    required_filters = [
        "__pycache__/",
        "*.py[cod]",
        "*$py.class",
        ".pytest_cache/",
        "golden.json",
    ]
    entries_to_add = [p for p in required_filters if p not in existing_text]

    if entries_to_add:
        prefix = "\n" if existing_text and not existing_text.endswith("\n") else ""
        addition = prefix + "# DriftZero Caches & Runtime Artifacts\n" + "\n".join(entries_to_add) + "\n"
        gitignore_path.write_text(existing_text + addition, encoding="utf-8")

    # Untrack cache files/directories from git index if already tracked
    proc_rm = _run_git(
        ["rm", "-r", "--cached", "--ignore-unmatch", "__pycache__", ".pytest_cache"],
        cwd=workspace_dir,
        check=False,
    )
    if proc_rm.returncode not in (0, 1):
        print(f"[PR Client] Notice during git rm --cached: {proc_rm.stderr.strip()}")

    # Clean local cache directories on disk
    for cache_dir in workspace_dir.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
    for cache_dir in workspace_dir.rglob(".pytest_cache"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)


def _detect_default_branch(
    workspace_dir: Path,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> str:
    """
    Detects the default branch of the target repository.
    Tries GitHub API first, then git remote origin HEAD, current branch, or main/master.
    """
    # 1. Query GitHub API
    if owner and repo:
        try:
            headers = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = httpx.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10)
            if r.status_code == 200:
                branch = r.json().get("default_branch")
                if branch:
                    return branch
        except Exception as exc:
            print(f"[PR Client] Notice: GitHub default branch query skipped: {exc}")

    # 2. Check git remote origin HEAD symbolic-ref
    proc_sym = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=workspace_dir, check=False)
    if proc_sym.returncode == 0 and proc_sym.stdout.strip():
        return proc_sym.stdout.strip().split("/")[-1]

    # 3. Check currently checked out branch
    proc_head = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_dir, check=False)
    if proc_head.returncode == 0:
        current = proc_head.stdout.strip()
        if current and current not in ("HEAD", "") and not current.startswith("driftzero/"):
            return current

    # 4. Check if main or master exist
    proc_main = _run_git(["show-ref", "--verify", "--quiet", "refs/heads/main"], cwd=workspace_dir, check=False)
    if proc_main.returncode == 0:
        return "main"
    proc_master = _run_git(["show-ref", "--verify", "--quiet", "refs/heads/master"], cwd=workspace_dir, check=False)
    if proc_master.returncode == 0:
        return "master"

    return "main"


def create_migration_pr(
    workspace_dir: Path,
    migration_delta: MigrationDelta,
    stage4_report: Stage4VerificationReport,
    repo_url: Optional[str] = None,
    github_token: Optional[str] = None,
    skip_on_fail: bool = False,
) -> Optional[str]:
    """
    Creates a new branch, commits all patched files, tests, and living rules (.cursorrules, CLAUDE.md).
    Opens a GitHub Pull Request if github_token is available.
    - If stage4_report.final_status != 'PASSED':
        * If skip_on_fail is True, skips opening PR and returns None.
        * Otherwise, opens PR as a Draft PR on GitHub.
    - Forks the repo if direct push access is missing.
    - Returns None instead of a fabricated URL when a PR is not created.
    """
    token = github_token or os.getenv("GITHUB_TOKEN", "")
    is_draft = stage4_report.final_status != "PASSED"

    if is_draft and skip_on_fail and stage4_report.final_status != "NEEDS_MANUAL":
        print(f"[PR Client] Verification status is '{stage4_report.final_status}'. Skipping PR creation.")
        return None

    branch_name = f"driftzero/migrate-{migration_delta.library.lower()}-{migration_delta.to_version}"
    commit_msg = (
        f"chore(deps): autonomous migration of {migration_delta.library} to {migration_delta.to_version}\n\n"
        f"Verified by DriftZero Nebius Token Factory Parity Sandbox ({stage4_report.final_status})."
    )

    try:
        # 1. Initialize git if not already a repo
        if not (workspace_dir / ".git").exists():
            proc_init = _run_git(["init"], cwd=workspace_dir, check=True)
            if proc_init.returncode != 0:
                print(f"[PR Client] git init failed: {proc_init.stderr}")
                return None

        # Always configure git user.* and verify returncodes
        proc_name = _run_git(["config", "user.name", "DriftZero Bot"], cwd=workspace_dir, check=True)
        if proc_name.returncode != 0:
            print(f"[PR Client] Failed to set user.name: {proc_name.stderr}")
            return None

        proc_email = _run_git(["config", "user.email", "bot@driftzero.ai"], cwd=workspace_dir, check=True)
        if proc_email.returncode != 0:
            print(f"[PR Client] Failed to set user.email: {proc_email.stderr}")
            return None

        # 2. Add .gitignore filter for __pycache__ and .pytest_cache
        _ensure_gitignore_filters(workspace_dir)

        # 3. Parse owner/repo if remote GitHub repo
        owner: Optional[str] = None
        repo: Optional[str] = None
        if repo_url and "github.com" in repo_url:
            match = re.search(r"github\.com[/:]([\w\.\-]+)/([\w\.\-]+?)(?:\.git)?$", repo_url)
            if match:
                owner, repo = match.group(1), match.group(2)

        # 4. Detect default branch
        default_branch = _detect_default_branch(workspace_dir, owner, repo, token)

        # 5. Checkout branch & commit
        proc_chk = _run_git(["checkout", "-b", branch_name], cwd=workspace_dir, check=False)
        if proc_chk.returncode != 0:
            proc_switch = _run_git(["checkout", branch_name], cwd=workspace_dir, check=True)
            if proc_switch.returncode != 0:
                print(f"[PR Client] Failed to checkout branch {branch_name}")
                return None

        proc_add = _run_git(["add", "."], cwd=workspace_dir, check=True)
        if proc_add.returncode != 0:
            print(f"[PR Client] Failed to stage files: {proc_add.stderr}")
            return None

        proc_diff = _run_git(["diff", "--cached", "--quiet"], cwd=workspace_dir, check=False)
        if proc_diff.returncode != 0:
            proc_commit = _run_git(["commit", "-m", commit_msg], cwd=workspace_dir, check=True)
            if proc_commit.returncode != 0:
                print(f"[PR Client] git commit failed: {proc_commit.stderr}")
                return None
        else:
            proc_log = _run_git(["log", "-n", "1"], cwd=workspace_dir, check=False)
            if proc_log.returncode != 0:
                proc_empty = _run_git(["commit", "--allow-empty", "-m", commit_msg], cwd=workspace_dir, check=True)
                if proc_empty.returncode != 0:
                    print(f"[PR Client] Empty commit failed: {proc_empty.stderr}")
                    return None

        # 6. If remote GitHub repo and token present, push and open PR
        if not (token and owner and repo):
            # Local repo or no token: return None instead of fabricated URL
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

        # Check authenticated user
        current_user = None
        try:
            user_res = httpx.get("https://api.github.com/user", headers=headers, timeout=10)
            if user_res.status_code == 200:
                current_user = user_res.json().get("login")
        except Exception as exc:
            print(f"[PR Client] Notice: GitHub user resolution failed: {exc}")

        # Check push permissions on target repo
        can_push = False
        try:
            repo_res = httpx.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10)
            if repo_res.status_code == 200:
                repo_data = repo_res.json()
                permissions = repo_data.get("permissions", {})
                can_push = permissions.get("push", False)
                if current_user and repo_data.get("owner", {}).get("login", "").lower() == current_user.lower():
                    can_push = True
        except Exception as exc:
            print(f"[PR Client] Notice: GitHub repo permissions lookup failed: {exc}")

        head_ref = branch_name
        pushed = False

        if can_push:
            auth_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
            proc_push = _run_git(["push", "-u", auth_url, branch_name, "--force"], cwd=workspace_dir, check=False)
            if proc_push.returncode == 0:
                pushed = True
            else:
                print(f"[PR Client] Direct push to {owner}/{repo} failed (exit code {proc_push.returncode}). Fallback to fork...")

        # Fork when push access is missing or direct push failed
        if not pushed:
            print(f"[PR Client] Push access missing for {owner}/{repo}. Initiating repository fork...")
            fork_res = httpx.post(f"https://api.github.com/repos/{owner}/{repo}/forks", headers=headers, timeout=15)
            if fork_res.status_code in (200, 202):
                fork_data = fork_res.json()
                fork_owner = fork_data.get("owner", {}).get("login", current_user)
                fork_repo = fork_data.get("name", repo)

                if not fork_owner:
                    print("[PR Client] Could not identify fork owner.")
                    return None

                # Poll until fork is ready (up to 10 seconds)
                for _ in range(6):
                    chk_fork = httpx.get(f"https://api.github.com/repos/{fork_owner}/{fork_repo}", headers=headers, timeout=10)
                    if chk_fork.status_code == 200:
                        break
                    time.sleep(1.5)

                fork_auth_url = f"https://x-access-token:{token}@github.com/{fork_owner}/{fork_repo}.git"
                proc_fork_push = _run_git(["push", "-u", fork_auth_url, branch_name, "--force"], cwd=workspace_dir, check=False)
                if proc_fork_push.returncode == 0:
                    pushed = True
                    head_ref = f"{fork_owner}:{branch_name}"
                else:
                    print(f"[PR Client] Push to fork {fork_owner}/{fork_repo} failed (exit code {proc_fork_push.returncode}): {proc_fork_push.stderr.strip()}")
                    return None
            else:
                print(f"[PR Client] Fork creation failed (status {fork_res.status_code}): {fork_res.text}")
                return None

        if not pushed:
            return None

        # 7. Create Pull Request (or Draft PR)
        pr_title = (
            f"[DRAFT] chore(deps): upgrade {migration_delta.library} to {migration_delta.to_version}"
            if is_draft
            else f"chore(deps): upgrade {migration_delta.library} to {migration_delta.to_version}"
        )

        draft_notice = (
            f"> ⚠️ **Verification Notice**: Stage 4 Parity Sandbox completed with status `{stage4_report.final_status}`. "
            f"This PR was created as a **Draft** for manual inspection and verification.\n\n"
            if is_draft
            else ""
        )

        pr_payload = {
            "title": pr_title,
            "head": head_ref,
            "base": default_branch,
            "draft": is_draft,
            "body": (
                f"## ⚡ DriftZero Autonomous Library Migration\n\n"
                f"{draft_notice}"
                f"- **Target Library**: `{migration_delta.library}`\n"
                f"- **Upgrade Path**: `{migration_delta.from_version}` ➔ `{migration_delta.to_version}`\n"
                f"- **Verification**: {stage4_report.final_status} ({stage4_report.total_attempts} sandbox attempts)\n"
                f"- **PR Mode**: {'Draft (Unverified/Partial Parity)' if is_draft else 'Ready (Parity Verified)'}\n"
                f"- **Living Rules Generated**: `.cursorrules` and `CLAUDE.md` included\n\n"
                f"Grounded by **Tavily Live Delta Engine** and verified via **Nebius Serverless Jobs**."
            ),
        }

        r = httpx.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            json=pr_payload,
            headers=headers,
            timeout=15,
        )
        if r.status_code in (200, 201):
            return r.json().get("html_url")
        elif r.status_code == 422:
            # PR may already exist for this branch -> lookup existing open PR
            try:
                existing = httpx.get(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls?head={head_ref}&state=open",
                    headers=headers,
                    timeout=10,
                )
                if existing.status_code == 200 and existing.json():
                    return existing.json()[0].get("html_url")
            except Exception:
                pass
            print(f"[PR Client] PR creation response: {r.status_code} {r.text}")
        else:
            print(f"[PR Client] PR creation failed ({r.status_code}): {r.text}")

    except Exception as exc:
        print(f"[PR Client Notice] Error during migration PR creation: {exc}")

    # Return None instead of fabricated URL
    return None
