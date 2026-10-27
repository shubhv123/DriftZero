import ast
import concurrent.futures
import difflib
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from backend.config import (
    NEBIUS_API_KEY,
    NEBIUS_BASE_URL,
    NEMOTRON_SUPER_MODEL,
    NEMOTRON_ULTRA_MODEL,
    PRICE_PER_1M_SUPER_INPUT,
    PRICE_PER_1M_SUPER_OUTPUT,
    PRICE_PER_1M_ULTRA_INPUT,
    PRICE_PER_1M_ULTRA_OUTPUT,
)
from backend.core.ast_scanner import FileASTVisitor
from backend.intelligence.prompts import (
    SUPER_FAST_PATCH_SYSTEM_PROMPT,
    ULTRA_DEEP_REFACTOR_SYSTEM_PROMPT,
)
from backend.intelligence.tavily_delta_engine import build_migration_delta
from backend.schemas.migration_models import (
    AffectedFileReport,
    BlastRadiusReport,
    FilePatchResult,
    MigrationDelta,
    MigrationRule,
    Stage3MigrationReport,
    TelemetryEvent,
)


def _extract_code_block(llm_text: str) -> str:
    """Extracts python code from an LLM response block."""
    if "```python" in llm_text:
        body = llm_text.split("```python", 1)[1]
        return body.split("```", 1)[0].strip() + "\n"
    if "```" in llm_text:
        body = llm_text.split("```", 1)[1]
        return body.split("```", 1)[0].strip() + "\n"
    return llm_text.strip() + "\n"


def _is_valid_complete_replacement(candidate: str, original: str) -> bool:
    """Validates that candidate is valid Python syntax and contains code without placeholder ellipsis."""
    if not candidate or len(candidate.strip()) < 20:
        return False
    if "..." in candidate and "..." not in original:
        return False
    try:
        cand_tree = ast.parse(candidate)
        orig_tree = ast.parse(original)
        orig_classes = {n.name for n in ast.walk(orig_tree) if isinstance(n, ast.ClassDef)}
        cand_classes = {n.name for n in ast.walk(cand_tree) if isinstance(n, ast.ClassDef)}
        if not orig_classes.issubset(cand_classes):
            return False
        return True
    except Exception:
        return False


def _filter_rules_for_file(
    file_report: AffectedFileReport,
    migration_delta: MigrationDelta,
    original_code: str,
) -> List[MigrationRule]:
    """Filters migration_delta.rules to only those relevant to the specific file to cut prompt tokens."""
    usage_symbols = {u.symbol_name.lower() for u in file_report.usages}
    relevant_rules = []
    for rule in migration_delta.rules:
        sym = (rule.symbol or "").lower()
        old_syn = (rule.old_syntax or "").lower()
        clean_sym = sym.strip(".").split("(")[0].strip()

        is_relevant = False
        if old_syn and old_syn in original_code.lower():
            is_relevant = True
        elif sym and any(sym in u or u in sym for u in usage_symbols):
            is_relevant = True
        elif clean_sym and len(clean_sym) > 2 and clean_sym in original_code.lower():
            is_relevant = True

        if is_relevant:
            relevant_rules.append(rule)

    return relevant_rules if relevant_rules else migration_delta.rules[:4]


def _validate_patch(
    candidate: str,
    original: str,
    target_library: str,
    relevant_rules: List[MigrationRule],
) -> Tuple[bool, Optional[str]]:
    """
    Validates that candidate patch:
    1. Parses cleanly with ast.parse() (AST validation).
    2. Preserves all top-level classes and functions from original (structural validation).
    3. Re-runs FileASTVisitor on the result and requires zero deprecated symbols.
    """
    if not candidate or len(candidate.strip()) < 20:
        return False, "Candidate patch is empty or truncated."
    if "..." in candidate and "..." not in original:
        return False, "Candidate patch contains ellipsis '...' placeholder."

    try:
        cand_tree = ast.parse(candidate)
    except SyntaxError as e:
        return False, f"AST SyntaxError: {e}"

    try:
        orig_tree = ast.parse(original)
    except Exception:
        orig_tree = None

    if orig_tree:
        # Check top-level classes are preserved
        orig_classes = {
            node.name for node in orig_tree.body
            if isinstance(node, ast.ClassDef) and node.name not in ("Config", "Meta")
        }
        cand_classes = {
            node.name for node in cand_tree.body
            if isinstance(node, ast.ClassDef)
        }
        missing_classes = orig_classes - cand_classes
        if missing_classes:
            return False, f"Structural validation failed: missing original classes {list(missing_classes)}"

        orig_funcs = {
            node.name for node in orig_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        cand_funcs = {
            node.name for node in cand_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        missing_funcs = orig_funcs - cand_funcs
        if missing_funcs:
            return False, f"Structural validation failed: missing original functions {list(missing_funcs)}"

    # Re-run FileASTVisitor on the result and require zero deprecated symbols
    source_lines = candidate.splitlines()
    visitor = FileASTVisitor(source_lines, target_library)
    visitor.visit(cand_tree)

    deprecated_patterns = set()
    for rule in relevant_rules:
        if rule.symbol:
            deprecated_patterns.add(rule.symbol.strip().lower())
        if rule.old_syntax:
            deprecated_patterns.add(rule.old_syntax.strip().lower())

    deprecated_found = []
    for usage in visitor.usages:
        sym = usage.symbol_name.strip().lower()
        for dep in deprecated_patterns:
            if dep and (dep == sym or dep in sym or sym in dep):
                deprecated_found.append(f"{usage.symbol_name} (line {usage.line_number})")
                break

    # Also check candidate text for remaining explicit deprecated syntax
    for rule in relevant_rules:
        if rule.old_syntax and rule.old_syntax in candidate:
            deprecated_found.append(f"Deprecated syntax '{rule.old_syntax}' still present")

    if deprecated_found:
        unique_dep = list(dict.fromkeys(deprecated_found))
        return False, f"Deprecated symbols remaining in patched file: {unique_dep}"

    return True, None


def _compute_unified_diff(original: str, patched: str, rel_path: str) -> str:
    """Computes a unified diff between original and patched code, ensuring all lines end with newline."""
    orig_lines = [line if line.endswith("\n") else line + "\n" for line in original.splitlines(keepends=True)]
    patch_lines = [line if line.endswith("\n") else line + "\n" for line in patched.splitlines(keepends=True)]
    diff_lines = list(difflib.unified_diff(
        orig_lines,
        patch_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    ))
    return "".join(line if line.endswith("\n") else line + "\n" for line in diff_lines)


def patch_single_file_via_token_factory(
    file_report: AffectedFileReport,
    migration_delta: MigrationDelta,
) -> FilePatchResult:
    """
    Routes a single file to either Nemotron Super (<=5) or Nemotron 3 Ultra (>5)
    on Nebius Token Factory.
    Filters migration_delta.rules per file to cut prompt tokens.
    If a patch fails AST, structural validation, or contains deprecated symbols,
    retries once with the error. If retry fails, marks the file FAILED.
    Zero silent fallbacks.
    """
    start_time = time.perf_counter()
    original_code = Path(file_report.file_path).read_text(encoding="utf-8")
    is_ultra = file_report.assigned_tier == "NEMOTRON_ULTRA"

    model_id = NEMOTRON_ULTRA_MODEL if is_ultra else NEMOTRON_SUPER_MODEL
    system_prompt = ULTRA_DEEP_REFACTOR_SYSTEM_PROMPT if is_ultra else SUPER_FAST_PATCH_SYSTEM_PROMPT

    if not NEBIUS_API_KEY:
        raise RuntimeError(
            f"Stage 3 Error: NEBIUS_API_KEY is not configured. Live Nebius Token Factory "
            f"inference is required to patch '{file_report.relative_path}'."
        )

    # Filter rules to only those relevant to this file to cut prompt tokens
    relevant_rules = _filter_rules_for_file(file_report, migration_delta, original_code)
    filtered_rules_json = json.dumps([r.__dict__ for r in relevant_rules], indent=2)

    prompt_tokens = 0
    completion_tokens = 0
    patched_code = ""
    status = "SUCCESS"
    final_error = None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL, timeout=40.0, max_retries=1)
        messages = [
            {
                "role": "system",
                "content": system_prompt + "\n\nCRITICAL: DO NOT output any internal reasoning or thoughts. Start output immediately with ```python and output the complete upgraded code.",
            },
            {
                "role": "user",
                "content": (
                    f"File: {file_report.relative_path}\n"
                    f"Target Library: {migration_delta.library} (Upgrade {migration_delta.from_version} -> {migration_delta.to_version})\n"
                    f"Detected Usages: {[u.symbol_name for u in file_report.usages]}\n\n"
                    f"Migration Rules for this file:\n{filtered_rules_json}\n\n"
                    f"Original Python Source:\n```python\n{original_code}\n```"
                ),
            },
        ]

        print(f"  🌐 [LIVE NEBIUS TOKEN FACTORY] Patching {file_report.relative_path} via {model_id} ({len(relevant_rules)} rules)...", flush=True)
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.05,
            max_tokens=4000,
        )

        if response.usage:
            prompt_tokens += response.usage.prompt_tokens
            completion_tokens += response.usage.completion_tokens

        msg = response.choices[0].message
        raw_reply = msg.content or ""
        if not raw_reply.strip() and hasattr(msg, "reasoning_content"):
            raw_reply = getattr(msg, "reasoning_content", "") or ""
        if not raw_reply.strip() and hasattr(msg, "model_extra") and isinstance(msg.model_extra, dict):
            for k in ("reasoning_content", "reasoning", "thought", "thinking"):
                if msg.model_extra.get(k):
                    raw_reply = str(msg.model_extra[k]).strip()
                    break

        candidate_code = _extract_code_block(raw_reply)
        is_valid, validation_error = _validate_patch(
            candidate_code, original_code, migration_delta.library, relevant_rules
        )

        if is_valid:
            patched_code = candidate_code
            print(f"  ✅ [LIVE NEBIUS TOKEN FACTORY] Successfully patched {file_report.relative_path}", flush=True)
        else:
            # Retry once with the error!
            print(f"  ⚠️ [Validation Notice] {file_report.relative_path} failed: {validation_error}. Retrying once with error...", flush=True)
            retry_prompt = (
                f"Your previous patch for '{file_report.relative_path}' failed validation:\n"
                f"ERROR: {validation_error}\n\n"
                f"Please fix this error immediately. Ensure:\n"
                f"1. Valid Python syntax with all original classes, methods, and functions preserved.\n"
                f"2. ZERO deprecated symbols remain from target library {migration_delta.library}.\n"
                f"3. Start output immediately with ```python and output the complete upgraded file."
            )
            messages.append({"role": "assistant", "content": f"```python\n{candidate_code}\n```"})
            messages.append({"role": "user", "content": retry_prompt})

            retry_response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.05,
                max_tokens=4000,
            )
            if retry_response.usage:
                prompt_tokens += retry_response.usage.prompt_tokens
                completion_tokens += retry_response.usage.completion_tokens

            retry_msg = retry_response.choices[0].message
            retry_reply = retry_msg.content or ""
            if not retry_reply.strip() and hasattr(retry_msg, "reasoning_content"):
                retry_reply = getattr(retry_msg, "reasoning_content", "") or ""
            if not retry_reply.strip() and hasattr(retry_msg, "model_extra") and isinstance(retry_msg.model_extra, dict):
                for k in ("reasoning_content", "reasoning", "thought", "thinking"):
                    if retry_msg.model_extra.get(k):
                        retry_reply = str(retry_msg.model_extra[k]).strip()
                        break

            retry_candidate = _extract_code_block(retry_reply)
            retry_is_valid, retry_error = _validate_patch(
                retry_candidate, original_code, migration_delta.library, relevant_rules
            )

            if retry_is_valid:
                patched_code = retry_candidate
                print(f"  ✅ [LIVE NEBIUS TOKEN FACTORY] Successfully patched {file_report.relative_path} on retry", flush=True)
            else:
                # Mark the file FAILED and never write it!
                status = "FAILED"
                final_error = f"Validation failed after retry: {retry_error}"
                patched_code = ""
                print(f"  ❌ [NEBIUS TOKEN FACTORY] File {file_report.relative_path} FAILED: {final_error}", flush=True)

    except Exception as exc:
        status = "FAILED"
        final_error = f"Inference exception: {exc}"
        patched_code = ""
        print(f"  ❌ [NEBIUS TOKEN FACTORY] Exception patching {file_report.relative_path}: {exc}", flush=True)

    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    input_rate = PRICE_PER_1M_ULTRA_INPUT if is_ultra else PRICE_PER_1M_SUPER_INPUT
    output_rate = PRICE_PER_1M_ULTRA_OUTPUT if is_ultra else PRICE_PER_1M_SUPER_OUTPUT

    actual_cost = round(
        ((prompt_tokens / 1_000_000) * input_rate)
        + ((completion_tokens / 1_000_000) * output_rate),
        6,
    )

    hypothetical_ultra_cost = round(
        ((prompt_tokens / 1_000_000) * PRICE_PER_1M_ULTRA_INPUT)
        + ((completion_tokens / 1_000_000) * PRICE_PER_1M_ULTRA_OUTPUT),
        6,
    )

    cost_saved = round(max(0.0, hypothetical_ultra_cost - actual_cost), 6)

    syntax_ok = status == "SUCCESS" and bool(patched_code)

    telemetry = TelemetryEvent(
        file_path=file_report.relative_path,
        assigned_tier=file_report.assigned_tier,
        model_used=model_id,
        complexity_score=file_report.normalized_score,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        actual_cost_usd=actual_cost,
        hypothetical_ultra_cost_usd=hypothetical_ultra_cost,
        cost_saved_usd=cost_saved,
        execution_mode="NEBIUS_TOKEN_FACTORY",
    )

    return FilePatchResult(
        relative_path=file_report.relative_path,
        original_code=original_code,
        patched_code=patched_code,
        unified_diff=_compute_unified_diff(original_code, patched_code, file_report.relative_path) if patched_code else "",
        assigned_tier=file_report.assigned_tier,
        model_used=model_id,
        syntax_valid=syntax_ok,
        telemetry=telemetry,
        status=status,
        error_message=final_error,
    )


def execute_stage2_and_stage3_pipeline(
    blast_report: BlastRadiusReport,
    output_dir: Path,
) -> Tuple[MigrationDelta, Stage3MigrationReport]:
    """
    Runs Stage 2 (Tavily Live Delta) + Stage 3 (Nebius Token Factory Smart Router).
    1. Runs LibCST deterministic codemods before Stage 3 for mechanical renames.
    2. Sends only residual files with complex/architectural patterns to Nemotron.
    3. Processes residual files in parallel using ThreadPoolExecutor.
    4. Writes successfully patched files to `output_dir`. Never writes failed files.
    """
    migration_delta = build_migration_delta(blast_report)

    output_dir.mkdir(parents=True, exist_ok=True)
    patches_by_path: Dict[str, FilePatchResult] = {}

    # Run deterministic LibCST codemods before Stage 3
    from backend.core.codemods import partition_files_for_stage3
    resolved_patches, residual_files = partition_files_for_stage3(blast_report, migration_delta)

    for p in resolved_patches:
        patches_by_path[p.relative_path] = p

    # Send only residual files to Nemotron
    if residual_files:
        max_workers = min(8, len(residual_files))
        print(f"  ⚡ [ThreadPoolExecutor] Dispatching {len(residual_files)} residual files across {max_workers} threads to Nemotron...", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_rep = {
                executor.submit(patch_single_file_via_token_factory, file_rep, migration_delta): file_rep
                for file_rep in residual_files
            }
            for future in concurrent.futures.as_completed(future_to_rep):
                file_rep = future_to_rep[future]
                try:
                    patch_res = future.result()
                except Exception as exc:
                    print(f"  ❌ [ThreadPoolExecutor] Exception on {file_rep.relative_path}: {exc}", flush=True)
                    patch_res = FilePatchResult(
                        relative_path=file_rep.relative_path,
                        original_code=file_rep.source_code or "",
                        patched_code="",
                        unified_diff="",
                        assigned_tier=file_rep.assigned_tier,
                        model_used="UNKNOWN",
                        syntax_valid=False,
                        telemetry=TelemetryEvent(
                            file_path=file_rep.relative_path,
                            assigned_tier=file_rep.assigned_tier,
                            model_used="UNKNOWN",
                            complexity_score=file_rep.normalized_score,
                            prompt_tokens=0,
                            completion_tokens=0,
                            latency_ms=0.0,
                            actual_cost_usd=0.0,
                            hypothetical_ultra_cost_usd=0.0,
                            cost_saved_usd=0.0,
                            execution_mode="NEBIUS_TOKEN_FACTORY",
                        ),
                        status="FAILED",
                        error_message=str(exc),
                    )
                patches_by_path[patch_res.relative_path] = patch_res

    patches: List[FilePatchResult] = []
    super_count = 0
    ultra_count = 0
    total_latency = 0.0
    total_actual_cost = 0.0
    total_hypo_cost = 0.0

    # Preserve original ordering
    for file_rep in blast_report.affected_files:
        patch_res = patches_by_path.get(file_rep.relative_path)
        if not patch_res:
            continue
        patches.append(patch_res)

        if patch_res.assigned_tier == "NEMOTRON_SUPER":
            super_count += 1
        else:
            ultra_count += 1

        total_latency += patch_res.telemetry.latency_ms
        total_actual_cost += patch_res.telemetry.actual_cost_usd
        total_hypo_cost += patch_res.telemetry.hypothetical_ultra_cost_usd

        # Write successfully patched files; NEVER write failed files
        if patch_res.status == "SUCCESS" and patch_res.syntax_valid and patch_res.patched_code:
            dest_file = output_dir / patch_res.relative_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(patch_res.patched_code, encoding="utf-8")
        else:
            print(f"  ❌ Skipping writing failed patch for '{patch_res.relative_path}' (status={patch_res.status})", flush=True)

    savings_pct = (
        round(((total_hypo_cost - total_actual_cost) / total_hypo_cost) * 100.0, 1)
        if total_hypo_cost > 0
        else 0.0
    )

    stage3_report = Stage3MigrationReport(
        total_files_patched=len([p for p in patches if p.status == "SUCCESS"]),
        super_files_count=super_count,
        ultra_files_count=ultra_count,
        total_latency_ms=round(total_latency, 2),
        total_actual_cost_usd=round(total_actual_cost, 6),
        total_hypothetical_ultra_cost_usd=round(total_hypo_cost, 6),
        total_cost_savings_pct=savings_pct,
        patches=patches,
    )

    return migration_delta, stage3_report