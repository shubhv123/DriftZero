import asyncio
import json
import os
import shutil
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.core.ast_scanner import scan_repository
from backend.core.dependency_analyzer import auto_detect_migration_target
from backend.core.repo_manager import (
    cleanup_workspace,
    prepare_repo_workspace,
    update_manifest_version,
)
from backend.exporters.github_pr_client import create_migration_pr
from backend.exporters.rules_generator import generate_context_infrastructure_files
from backend.intelligence.tavily_delta_engine import build_migration_delta
from backend.intelligence.telemetry_tracker import PipelineTelemetrySummary
from backend.intelligence.token_factory_router import (
    execute_stage2_and_stage3_pipeline,
    patch_single_file_via_token_factory,
)
from backend.schemas.migration_models import Stage3MigrationReport
from backend.verification.nebius_job_runner import execute_stage4_verification_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    expected_key = (os.getenv("DRIFTZERO_API_KEY") or os.getenv("API_KEY") or "").strip()
    if not expected_key:
        raise RuntimeError(
            "DRIFTZERO_API_KEY is required at startup. Refusing to boot without it."
        )
    if expected_key.lower() in {"your_driftzero_api_key_here", "changeme", "change-me", "test", "secret"} or len(expected_key) < 16:
        raise RuntimeError(
            "DRIFTZERO_API_KEY is a placeholder or too short (<16 chars). Generate one with: openssl rand -hex 32"
        )
    yield


app = FastAPI(
    title="DriftZero API — Universal Nebius Token Factory & Tavily Orchestrator",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parent.parent


def _sse_pack(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


def _extract_github_token(
    x_github_token: Optional[str] = None,
    authorization: Optional[str] = None,
    body_token: Optional[str] = None,
    query_token: Optional[str] = None,
) -> Optional[str]:
    """
    Extracts GitHub token prioritizing headers, then POST body, query param, and env var.
    """
    if x_github_token and x_github_token.strip():
        return x_github_token.strip()
    if authorization and authorization.strip():
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if auth.lower().startswith("token "):
            return auth[6:].strip()
        return auth
    if body_token and body_token.strip():
        return body_token.strip()
    if query_token and query_token.strip():
        return query_token.strip()
    return os.getenv("GITHUB_TOKEN", None)


# Security, Rate Limiting & Concurrency controls
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
MAX_CONCURRENT_MIGRATIONS = int(os.getenv("MAX_CONCURRENT_MIGRATIONS", "2"))
CONCURRENCY_STATUS_CODE = int(os.getenv("CONCURRENCY_STATUS_CODE", "429"))

_rate_limit_lock = asyncio.Lock()
_rate_limit_records: dict[str, list[float]] = {}

_concurrency_semaphore = asyncio.BoundedSemaphore(MAX_CONCURRENT_MIGRATIONS)
_concurrency_lock = asyncio.Lock()


def _check_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key_query: Optional[str] = Query(None, alias="api_key"),
) -> str:
    """
    Validates X-API-Key header or api_key query parameter.
    DRIFTZERO_API_KEY is required at startup and must match.
    """
    provided_key = (x_api_key or api_key_query or "").strip()
    expected_key = (os.getenv("DRIFTZERO_API_KEY") or os.getenv("API_KEY") or "").strip()

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DRIFTZERO_API_KEY is not configured on server",
        )

    if not provided_key or provided_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
        )

    return provided_key


def _get_client_ip(request: Request) -> str:
    """
    Returns the client IP. Forwarding headers are trusted ONLY when TRUSTED_PROXY=1
    (backend sits behind a proxy you control); then the right-most X-Forwarded-For hop
    (the one appended by that proxy) is used, never the client-supplied left-most value.
    """
    if os.getenv("TRUSTED_PROXY", "0") == "1":
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hop = forwarded.split(",")[-1].strip()
            if hop:
                return hop
        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


async def _check_rate_limit(request: Request, client_key: Optional[str] = None) -> None:
    """
    Per-client sliding-window rate limiter keyed by client IP only (requests per minute).
    Evicts stale IP buckets whose timestamps are outside the active window.
    """
    client_ip = _get_client_ip(request)
    now = time.time()
    window = 60.0

    async with _rate_limit_lock:
        # Evict old buckets
        expired_keys = [
            ip for ip, ts_list in _rate_limit_records.items()
            if not ts_list or (now - ts_list[-1] >= window)
        ]
        for ip in expired_keys:
            _rate_limit_records.pop(ip, None)

        timestamps = _rate_limit_records.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: maximum {RATE_LIMIT_PER_MINUTE} requests per minute.",
            )
        timestamps.append(now)
        _rate_limit_records[client_ip] = timestamps


async def _acquire_concurrency_slot() -> None:
    """
    Non-blocking check and acquisition of a concurrency slot for a live migration stream.
    """
    async with _concurrency_lock:
        if _concurrency_semaphore._value <= 0:
            raise HTTPException(
                status_code=CONCURRENCY_STATUS_CODE,
                detail=f"Concurrency cap reached: maximum {MAX_CONCURRENT_MIGRATIONS} simultaneous migrations allowed. Please retry later.",
            )
        await _concurrency_semaphore.acquire()


def _release_concurrency_slot() -> None:
    """
    Safely releases a concurrency slot when stream closes or fails.
    """
    try:
        _concurrency_semaphore.release()
    except ValueError:
        pass


def _reset_limits_for_testing() -> None:
    """
    Utility for tests to reset rate-limit records and concurrency state.
    """
    global _rate_limit_records, _concurrency_semaphore
    _rate_limit_records.clear()
    _concurrency_semaphore = asyncio.BoundedSemaphore(MAX_CONCURRENT_MIGRATIONS)


class MigrationStreamRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL or local repository path")
    target_library: Optional[str] = Field(None, description="Optional target library override (auto-detected if omitted)")
    target_version: Optional[str] = Field(None, description="Optional target version override (auto-detected if omitted)")
    source_version: Optional[str] = Field(None, description="Optional source version override")
    github_token: Optional[str] = Field(None, description="Optional GitHub token for PR creation (can also use X-GitHub-Token header)")
    skip_unverified_pr: Optional[bool] = Field(False, description="Whether to skip PR creation when Stage 4 parity check fails")
    build_log: Optional[str] = Field(None, description="Optional build or test failure log")


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "engine": "DriftZero Universal Migration Engine",
        "cloud": "Nebius Token Factory & Serverless Jobs",
        "intelligence": "Tavily Live Delta Engine",
    }


async def _run_event_stream_pipeline(
    repo_url: str,
    target_library: Optional[str] = None,
    target_version: Optional[str] = None,
    source_version: Optional[str] = None,
    github_token: Optional[str] = None,
    skip_unverified_pr: bool = False,
    build_log: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    workspace_path = None
    patched_workspace = None

    try:
        # PREPARATION: Workspace Setup
        if not repo_url or not repo_url.strip():
            yield _sse_pack("pipeline_error", {
                "error": "Missing required parameter 'repo_url'. Please provide a GitHub repository URL or local path."
            })
            return

        yield _sse_pack("pipeline_init", {
            "message": f"Cloning and analyzing repository: {repo_url}",
            "repo_url": repo_url,
        })

        try:
            workspace_path, _ = await asyncio.to_thread(
                prepare_repo_workspace, repo_url, None, None, github_token
            )
        except Exception as exc:
            yield _sse_pack("pipeline_error", {
                "error": str(exc)
            })
            return

        active_lib = target_library
        active_ver = target_version
        active_src_ver = source_version

        # AUTONOMOUS DEPENDENCY & AST ECOSYSTEM DISCOVERY
        if not active_lib or not active_lib.strip() or not active_ver or not active_ver.strip():
            yield _sse_pack("discovery_start", {
                "message": "Scanning manifests and Python AST imports to detect outdated libraries...",
            })
            detected_lib, detected_ver, detected_old, all_candidates = await asyncio.to_thread(
                auto_detect_migration_target, workspace_path
            )
            if not active_lib or not active_lib.strip():
                active_lib = detected_lib
            if not active_ver or not active_ver.strip():
                active_ver = detected_ver
            if not active_src_ver and detected_old:
                active_src_ver = detected_old

            yield _sse_pack("discovery_complete", {
                "target_library": active_lib,
                "target_version": active_ver,
                "source_version": active_src_ver,
                "all_candidates": all_candidates,
                "message": f"Autonomous discovery identified target: {active_lib} ({active_src_ver or 'legacy'} -> {active_ver})"
            })

        patched_workspace = workspace_path.parent / f"patched_{workspace_path.name}"
        shutil.copytree(workspace_path, patched_workspace, dirs_exist_ok=True)

        # STAGE 1: Deterministic AST Blast-Radius Scan (Zero Tokens)
        yield _sse_pack("stage_start", {"stage": 1, "title": "Deterministic AST Blast-Radius Scan"})
        blast_report = await asyncio.to_thread(
            scan_repository, str(workspace_path), active_lib, active_ver
        )
        if active_src_ver:
            blast_report.old_version = active_src_ver

        yield _sse_pack("stage1_complete", blast_report.to_dict())
        await asyncio.sleep(0.15)

        # STAGE 2: Tavily Live Delta Engine
        yield _sse_pack("stage_start", {"stage": 2, "title": "Tavily Live Documentation Delta"})

        # Call execute_stage2_and_stage3_pipeline's parallel patch path instead of the sequential loop
        migration_delta, s3_report = await asyncio.to_thread(
            execute_stage2_and_stage3_pipeline,
            blast_report,
            patched_workspace,
            build_log,
        )
        yield _sse_pack("stage2_complete", migration_delta.to_dict())
        await asyncio.sleep(0.15)

        # STAGE 3: LibCST Deterministic Codemods + Nebius Token Factory Smart Router
        yield _sse_pack("stage_start", {"stage": 3, "title": "LibCST Codemods & Nebius Token Factory Smart Router"})

        for patch_res in s3_report.patches:
            yield _sse_pack("stage3_file_patched", {
                "relative_path": patch_res.relative_path,
                "assigned_tier": patch_res.assigned_tier,
                "model_used": patch_res.model_used,
                "original_code": patch_res.original_code,
                "unified_diff": patch_res.unified_diff,
                "patched_code": patch_res.patched_code,
                "telemetry": patch_res.telemetry.__dict__,
            })

        # Update manifest files (requirements.txt, pyproject.toml, companion packages & conflicts)
        manifest_res = update_manifest_version(patched_workspace, active_lib, active_ver)
        if manifest_res.conflicts:
            for conflict in manifest_res.conflicts:
                print(f"⚠️ [Manifest Conflict] {conflict}")
            yield _sse_pack("manifest_conflicts", {
                "conflicts": manifest_res.conflicts,
                "companion_packages_added": manifest_res.companion_packages_added,
                "manifests_updated": manifest_res.manifests_updated,
                "needs_manual": manifest_res.needs_manual,
            })

        yield _sse_pack("stage3_complete", s3_report.to_dict())
        await asyncio.sleep(0.15)

        # STAGE 4: Nebius Serverless Jobs Sandbox & Self-Healer
        yield _sse_pack("stage_start", {"stage": 4, "title": "Nebius Serverless Jobs Parity Sandbox"})
        stage4_report = await asyncio.to_thread(
            execute_stage4_verification_loop, patched_workspace, migration_delta, 3, workspace_path
        )

        # If any patch is FAILED, set final_status="FAILED" and open only a draft PR (or none)
        if any(p.status == "FAILED" for p in s3_report.patches):
            stage4_report.final_status = "FAILED"
            print("⚠️ [Pipeline Notice] One or more Stage 3 patches failed. Overriding final_status to 'FAILED'.", flush=True)

        if manifest_res.needs_manual:
            stage4_report.final_status = "NEEDS_MANUAL"
            print("⚠️ [Pipeline Notice] Manifest resolution required manual intervention. Setting final_status to 'NEEDS_MANUAL'.", flush=True)

        yield _sse_pack("stage4_complete", stage4_report.to_dict())
        await asyncio.sleep(0.15)

        # STAGE 5: Context Infrastructure (.cursorrules & CLAUDE.md) first, then GitHub PR
        yield _sse_pack("stage_start", {"stage": 5, "title": "Exporting .cursorrules, CLAUDE.md & PR"})

        # 1. Generate context infrastructure files so they are written before git commit
        stage5_bundle = await asyncio.to_thread(
            generate_context_infrastructure_files,
            patched_workspace,
            migration_delta,
            stage4_report,
            None,
        )

        # 2. Create migration PR (commits everything including living rules, pushes/forks, opens PR or Draft PR)
        pr_url = await asyncio.to_thread(
            create_migration_pr,
            patched_workspace,
            migration_delta,
            stage4_report,
            repo_url,
            github_token,
            skip_unverified_pr,
        )
        stage5_bundle.pr_url = pr_url

        yield _sse_pack("stage5_complete", stage5_bundle.to_dict())
        yield _sse_pack("pipeline_done", {
            "status": "COMPLETED",
            "pr_url": stage5_bundle.pr_url,
            "pr_branch": stage5_bundle.pr_branch_name,
            "final_verification": stage4_report.final_status,
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        yield _sse_pack("pipeline_error", {"error": f"Pipeline stage error: {exc}"})
        return
    finally:
        _release_concurrency_slot()
        # Delete ephemeral workspaces when done
        if workspace_path:
            cleanup_workspace(workspace_path)


@app.post("/api/migrate/stream")
async def stream_migration_pipeline_post(
    request: Request,
    body: MigrationStreamRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None, alias="api_key"),
    x_github_token: Optional[str] = Header(None, alias="X-GitHub-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> StreamingResponse:
    client_key = _check_api_key(x_api_key=x_api_key, api_key_query=api_key)
    await _check_rate_limit(request, client_key)
    await _acquire_concurrency_slot()

    token = _extract_github_token(
        x_github_token=x_github_token,
        authorization=authorization,
        body_token=body.github_token,
    )
    return StreamingResponse(
        _run_event_stream_pipeline(
            repo_url=body.repo_url,
            target_library=body.target_library,
            target_version=body.target_version,
            source_version=body.source_version,
            github_token=token,
            skip_unverified_pr=body.skip_unverified_pr or False,
            build_log=body.build_log,
        ),
        media_type="text/event-stream",
    )


@app.get("/api/migrate/stream")
async def stream_migration_pipeline_get(
    request: Request,
    repo_url: str = Query(..., description="GitHub repository URL or local repository path"),
    target_library: Optional[str] = Query(None, description="Optional target library override (auto-detected if omitted)"),
    target_version: Optional[str] = Query(None, description="Optional target version override (auto-detected if omitted)"),
    source_version: Optional[str] = Query(None, description="Optional source version override"),
    github_token: Optional[str] = Query(None, description="Optional GitHub token (deprecated: prefer X-GitHub-Token header or POST body)"),
    skip_unverified_pr: Optional[bool] = Query(False, description="Whether to skip PR creation when Stage 4 parity check fails"),
    build_log: Optional[str] = Query(None, description="Optional build or test failure log"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None, alias="api_key"),
    x_github_token: Optional[str] = Header(None, alias="X-GitHub-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> StreamingResponse:
    client_key = _check_api_key(x_api_key=x_api_key, api_key_query=api_key)
    await _check_rate_limit(request, client_key)
    await _acquire_concurrency_slot()

    token = _extract_github_token(
        x_github_token=x_github_token,
        authorization=authorization,
        query_token=github_token,
    )
    return StreamingResponse(
        _run_event_stream_pipeline(
            repo_url=repo_url,
            target_library=target_library,
            target_version=target_version,
            source_version=source_version,
            github_token=token,
            skip_unverified_pr=skip_unverified_pr or False,
            build_log=build_log,
        ),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m backend.main <repo_url_or_path> [target_library] [target_version]")
        print("Example (Autonomous): python -m backend.main https://github.com/shubhv123/pdf-rag")
        print("Example (Explicit):   python -m backend.main ./demo_target_repo pydantic 2.9.2")
        sys.exit(1)

    repo_arg = sys.argv[1]
    lib_arg = sys.argv[2] if len(sys.argv) > 2 else None
    ver_arg = sys.argv[3] if len(sys.argv) > 3 else None
    source_ver_arg = sys.argv[4] if len(sys.argv) > 4 else None

    ws_dir = None
    patched_ws = None

    try:
        # Workspace setup
        ws_dir, _ = prepare_repo_workspace(repo_arg)
        patched_ws = ws_dir.parent / f"patched_{ws_dir.name}"
        shutil.copytree(ws_dir, patched_ws, dirs_exist_ok=True)

        # Autonomous discovery if library/version not specified
        if not lib_arg or not ver_arg:
            print("🔍 Running Autonomous AST & Manifest Dependency Discovery...")
            detected_lib, detected_ver, detected_old, all_cands = auto_detect_migration_target(ws_dir)
            lib_arg = lib_arg or detected_lib
            ver_arg = ver_arg or detected_ver
            if not source_ver_arg and detected_old:
                source_ver_arg = detected_old
            print(f"🎯 Auto-Detected Target: {lib_arg} ({source_ver_arg or 'legacy'} -> {ver_arg})")

        print("=" * 84)
        print(f"⚡ DRIFTZERO UNIVERSAL 5-STAGE PIPELINE EXECUTION")
        print(f"Target: {lib_arg} -> {ver_arg} | Repo: {repo_arg}")
        print("=" * 84)

        # 1. Stage 1
        blast = scan_repository(str(ws_dir), lib_arg, ver_arg)
        if source_ver_arg:
            blast.old_version = source_ver_arg
        print(f"[Stage 1] Scanned: {blast.total_files_scanned} files | Affected: {blast.affected_files_count} files ({blast.scan_duration_ms} ms)")

        # 2 & 3. Stage 2 & Stage 3: Parallel Patch Path via execute_stage2_and_stage3_pipeline
        delta, s3_report = execute_stage2_and_stage3_pipeline(blast, patched_ws)
        print(f"[Stage 2] Tavily Grounded: {len(delta.rules)} rules synthesized | Citations: {len(delta.citations)}")
        print(f"[Stage 3] Patched: {len(s3_report.patches)} files | Saved: {s3_report.total_cost_savings_pct}% token cost | Codemods: {s3_report.codemod_files_count}")

        manifest_res = update_manifest_version(patched_ws, lib_arg, ver_arg)
        if manifest_res.conflicts:
            for conf in manifest_res.conflicts:
                print(f"⚠️ [Manifest Conflict] {conf}")
        if manifest_res.companion_packages_added:
            for comp in manifest_res.companion_packages_added:
                print(f"📦 [Companion Package] {comp}")

        # 4. Stage 4: Parity Sandbox
        s4 = execute_stage4_verification_loop(patched_ws, delta, max_retries=3, baseline_repo_dir=ws_dir)
        if any(p.status == "FAILED" for p in s3_report.patches):
            s4.final_status = "FAILED"
            print("⚠️ [Pipeline Notice] One or more Stage 3 patches failed. Overriding final_status to 'FAILED'.", flush=True)

        if manifest_res.needs_manual:
            s4.final_status = "NEEDS_MANUAL"
            print("⚠️ [Pipeline Notice] Manifest resolution required manual intervention. Setting final_status to 'NEEDS_MANUAL'.", flush=True)

        for att in s4.attempts:
            status_icon = "✅ PASSED" if att.exit_code == 0 else "❌ FAILED"
            print(f"[Stage 4 | Attempt #{att.attempt_number}] {att.job_id} -> {status_icon} | Passed: {att.passed_count}, Failed: {att.failed_count}")

        # 5. Stage 5: Living rules first, then PR
        s5 = generate_context_infrastructure_files(patched_ws, delta, s4, None)
        pr_link = create_migration_pr(patched_ws, delta, s4, repo_arg, os.getenv("GITHUB_TOKEN"))
        s5.pr_url = pr_link

        print("-" * 84)
        print(f"🛡️ Stage 5 Exported: {s5.cursorrules_path}")
        print(f"🛡️ Stage 5 Exported: {s5.claude_md_path}")
        print(f"🚀 PR Branch Ready : {s5.pr_branch_name}")
        print(f"🔗 Pull Request URL: {s5.pr_url or 'None (Draft or Skipped/No Token)'}")
        print(f"🎉 FINAL VERDICT   : {s4.final_status}")
        print("=" * 84)
    finally:
        # Delete ephemeral workspaces when done
        if ws_dir:
            cleanup_workspace(ws_dir)