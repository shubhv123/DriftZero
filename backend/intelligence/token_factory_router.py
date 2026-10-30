import ast
import concurrent.futures
import difflib
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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
    PROJECT_ROOT,
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
    """Extracts python code from an LLM response block, preferring the largest complete code block."""
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", llm_text, flags=re.DOTALL)
    if blocks:
        largest = max(blocks, key=len)
        return largest.strip() + "\n"
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


def _get_node_start_lineno(node: ast.AST) -> int:
    """Returns the line number where a node begins, including preceding decorators."""
    if hasattr(node, "decorator_list") and node.decorator_list:
        return node.decorator_list[0].lineno
    return node.lineno


def _splice_ast_patch(original: str, candidate: str) -> str:
    """
    Surgically splices candidate classes/functions into the original file if the candidate
    is a partial patch (e.g. only contains the fixed class/function) rather than a full rewrite.
    Preserves all original untouched classes, functions, comments, and docstrings.
    """
    try:
        if not candidate or ("..." in candidate and "..." not in original):
            return ""
        orig_tree = ast.parse(original)
        cand_tree = ast.parse(candidate)

        orig_lines = original.splitlines(keepends=True)
        cand_lines = candidate.splitlines(keepends=True)

        cand_defs = {}
        cand_imports = []
        for node in cand_tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                cand_defs[node.name] = node
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                cand_imports.append(node)

        if not cand_defs:
            return ""

        replacements = []
        orig_def_names = set()
        for node in orig_tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                orig_def_names.add(node.name)
                if node.name in cand_defs:
                    cand_node = cand_defs[node.name]
                    cand_start = _get_node_start_lineno(cand_node) - 1
                    cand_end = cand_node.end_lineno
                    cand_text = "".join(cand_lines[cand_start:cand_end]).rstrip() + "\n"

                    orig_start = _get_node_start_lineno(node) - 1
                    orig_end = node.end_lineno
                    replacements.append((orig_start, orig_end, cand_text))

        if not replacements:
            return ""

        # Apply replacements from bottom to top so line number offsets remain invariant
        replacements.sort(key=lambda x: x[0], reverse=True)
        result_lines = list(orig_lines)
        for start, end, new_text in replacements:
            result_lines[start:end] = [new_text]

        # Integrate any new top-level classes/functions introduced in candidate
        new_defs = [
            node for name, node in cand_defs.items()
            if name not in orig_def_names
        ]
        for new_node in new_defs:
            new_start = _get_node_start_lineno(new_node) - 1
            new_end = new_node.end_lineno
            new_text = "\n\n" + "".join(cand_lines[new_start:new_end]).rstrip() + "\n"
            result_lines.append(new_text)

        # Integrate any new imports from candidate not already in original
        existing_code = "".join(result_lines)
        new_import_lines = []
        for imp_node in cand_imports:
            imp_start = _get_node_start_lineno(imp_node) - 1
            imp_end = imp_node.end_lineno
            imp_text = "".join(cand_lines[imp_start:imp_end]).strip()
            if imp_text and imp_text not in existing_code:
                new_import_lines.append(imp_text + "\n")

        if new_import_lines:
            last_import_line = 0
            for node in orig_tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    last_import_line = max(last_import_line, node.end_lineno)
            result_lines.insert(last_import_line, "".join(new_import_lines))

        spliced = "".join(result_lines)
        ast.parse(spliced)
        return spliced
    except Exception:
        return ""


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


SUBPROCESS_IMPORT_RUNNER = """
import importlib.util
import sys

mod_path = sys.argv[1]
mod_name = sys.argv[2] if len(sys.argv) > 2 else "_candidate_patch"

try:
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
except (TypeError, ImportError) as exc:
    # If a ModuleNotFoundError occurred because an uninstalled third-party root package
    # is missing from this environment, do not reject the patch.
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        root_pkg = exc.name.split('.')[0]
        if not importlib.util.find_spec(root_pkg):
            sys.exit(0)
    print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(42)
except Exception:
    # Non-type/import errors at import time do not trigger rejection
    sys.exit(0)
"""


def _get_target_python() -> str:
    """
    Returns the python executable for the target version virtual environment
    (the same venv the sandbox uses).
    Checks explicit environment overrides, active VIRTUAL_ENV, workspace virtualenvs (env/.venv),
    sandbox venvs in tempdir, and falls back to sys.executable.
    """
    # 1. Explicit override via environment variables
    for env_var in ("DRIFTZERO_TARGET_PYTHON", "TARGET_VENV_PYTHON", "SANDBOX_VENV_PYTHON"):
        val = os.getenv(env_var, "").strip()
        if val and Path(val).exists():
            return val

    # 2. VIRTUAL_ENV environment variable
    if os.getenv("VIRTUAL_ENV"):
        venv_py = Path(os.environ["VIRTUAL_ENV"]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if venv_py.exists():
            return str(venv_py)

    # 3. Project virtual environments (./env, ./.venv, ./venv)
    for candidate in ("env", ".venv", "venv"):
        cand_py = PROJECT_ROOT / candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if cand_py.exists():
            return str(cand_py)

    # 5. Fallback to sys.executable
    return sys.executable


def _validate_patch(
    candidate: str,
    original: str,
    target_library: str,
    relevant_rules: List[MigrationRule],
    target_python: Optional[str] = None,
    file_path: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Validates that candidate patch:
    1. Parses cleanly with ast.parse() (AST validation).
    2. Preserves all top-level classes and functions from original (structural validation).
    3. Re-runs FileASTVisitor on the result and requires zero deprecated symbols.
    4. Writes candidate to a temp file and runs py_compile.
    5. Imports candidate in a subprocess under target version (same venv sandbox uses),
       rejecting on TypeError or ImportError.
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

    # Re-run FileASTVisitor on candidate and require zero deprecated symbols via exact symbol / AST matching
    clean_target = (target_library or "").lower().replace("-", "_")
    source_lines = candidate.splitlines()
    visitor = FileASTVisitor(source_lines, target_library)
    visitor.visit(cand_tree)

    # Collect exact method rules to match against method_call usages
    deprecated_methods: Set[str] = set()
    for rule in relevant_rules:
        sym = (rule.symbol or "").strip()
        if sym.startswith("."):
            deprecated_methods.add(sym.rstrip("()").lstrip(".").lower())
        elif sym in ("dict", "json", "copy", "parse_obj", "parse_raw", "schema", "schema_json", "construct"):
            deprecated_methods.add(sym.lower())
        if rule.old_syntax and "." in rule.old_syntax and "(" in rule.old_syntax:
            m = rule.old_syntax.split("(")[0].split(".")[-1].strip().lower()
            if m:
                deprecated_methods.add(m)

    user_defined_methods: Set[str] = {
        node.name.lower()
        for node in ast.walk(cand_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    deprecated_found: List[str] = []

    # 1. Exact symbol matching on method calls from FileASTVisitor
    for usage in visitor.usages:
        if usage.usage_type == "method_call" and usage.symbol_name.startswith("."):
            m_name = usage.symbol_name.rstrip("()").lstrip(".").lower()
            if m_name in user_defined_methods:
                continue
            if m_name in deprecated_methods:
                deprecated_found.append(f"Method call `.{m_name}()` (line {usage.line_number})")

    # 2. Exact AST matching for decorators, imports, base classes, Config attrs, and calls
    for node in ast.walk(cand_tree):
        # Decorators: exact match against @validator, @root_validator
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                dec_func = dec.func if isinstance(dec, ast.Call) else dec
                dec_name = ast.unparse(dec_func) if hasattr(ast, "unparse") else ""
                clean_name = dec_name.strip().lstrip("@")
                if clean_name in ("validator", "root_validator", f"{clean_target}.validator", f"{clean_target}.root_validator"):
                    deprecated_found.append(f"Decorator `@{clean_name}` (line {dec.lineno})")
                elif clean_name in ("model_validator", f"{clean_target}.model_validator") and not isinstance(dec, ast.Call):
                    deprecated_found.append(f"Bare `@model_validator` decorator without mode parameter (line {dec.lineno}). Use `@model_validator(mode='after')` or `@model_validator(mode='before')`.")

        # Imports: exact match for deprecated names from target library
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower().replace("-", "_")
            if mod == clean_target:
                for alias in node.names:
                    if alias.name in ("validator", "root_validator", "parse_obj_as", "BaseSettings", "GenericModel"):
                        deprecated_found.append(f"Import `{alias.name}` from `{node.module}` (line {node.lineno})")
            elif mod == f"{clean_target}.generics":
                for alias in node.names:
                    if alias.name == "GenericModel":
                        deprecated_found.append(f"Import `GenericModel` from `{node.module}` (line {node.lineno})")

        # ClassDef bases & inner Config classes
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ast.unparse(base) if hasattr(ast, "unparse") else ""
                if base_name in ("GenericModel", f"{clean_target}.generics.GenericModel"):
                    deprecated_found.append(f"Inheritance from `{base_name}` in class `{node.name}` (line {node.lineno})")
                elif base_name in ("BaseSettings", f"{clean_target}.BaseSettings") and "BaseSettings" in visitor.imported_symbols:
                    deprecated_found.append(f"Inheritance from `{base_name}` in class `{node.name}` (line {node.lineno})")

            # Check deprecated inner Config attributes
            if node.name == "Config":
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name) and target.id in (
                                "orm_mode",
                                "allow_population_by_field_name",
                                "anystr_strip_whitespace",
                                "allow_mutation",
                                "schema_extra",
                            ):
                                deprecated_found.append(f"Config attribute `{target.id}` in class Config (line {item.lineno})")

        # Special fields: __root__
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__root__":
            deprecated_found.append(f"Special field `__root__` in class model (line {node.lineno})")

        # Function / Method calls
        elif isinstance(node, ast.Call):
            call_func = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            if call_func in ("parse_obj_as", f"{clean_target}.parse_obj_as"):
                deprecated_found.append(f"Function call `{call_func}()` (line {node.lineno})")
            elif call_func in ("Field", f"{clean_target}.Field"):
                for kw in node.keywords:
                    if kw.arg in ("regex", "min_items", "max_items"):
                        deprecated_found.append(f"Deprecated Field argument `{kw.arg}` (line {node.lineno})")

    if deprecated_found:
        unique_dep = list(dict.fromkeys(deprecated_found))
        return False, f"Deprecated symbols remaining in patched file: {unique_dep}"

    # 4. Write candidate to temp file and run py_compile
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp_f:
            tmp_path = Path(tmp_f.name)
            tmp_f.write(candidate)
            tmp_f.flush()

        try:
            py_compile.compile(str(tmp_path), doraise=True)
        except Exception as exc:
            return False, f"py_compile error: {exc}"

        # 5. Import candidate in a subprocess under target version (same venv the sandbox uses)
        py_exec = target_python or _get_target_python()
        mod_name = re.sub(r"[^a-zA-Z0-9_]", "_", Path(file_path).stem) if file_path else "_candidate_patch"

        env = os.environ.copy()
        cwd_dir = None
        if file_path:
            fp = Path(file_path).resolve()
            parent_dir = fp.parent
            if parent_dir.exists():
                cwd_dir = str(parent_dir)
                search_paths = [str(parent_dir)]
                curr = parent_dir
                for _ in range(3):
                    curr = curr.parent
                    search_paths.append(str(curr))
                search_paths.append(str(PROJECT_ROOT))
                existing_pp = env.get("PYTHONPATH", "")
                if existing_pp:
                    search_paths.append(existing_pp)
                env["PYTHONPATH"] = ":".join(search_paths)
        if not cwd_dir:
            env["PYTHONPATH"] = f"{PROJECT_ROOT}:{env.get('PYTHONPATH', '')}"

        try:
            proc = subprocess.run(
                [py_exec, "-c", SUBPROCESS_IMPORT_RUNNER, str(tmp_path), mod_name],
                cwd=cwd_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 42:
                err_detail = proc.stderr.strip() or "TypeError or ImportError during module import"
                return False, f"Import error under target version: {err_detail}"
        except subprocess.TimeoutExpired:
            return False, "Subprocess import timed out after 15 seconds"
        except Exception as exc:
            return False, f"Import check could not run under target python ({py_exec}): {exc}"
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

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
    original_code = file_report.source_code or Path(file_report.file_path).read_text(encoding="utf-8")
    is_ultra = file_report.assigned_tier == "NEMOTRON_ULTRA"

    model_id = NEMOTRON_ULTRA_MODEL if is_ultra else NEMOTRON_SUPER_MODEL
    system_prompt = ULTRA_DEEP_REFACTOR_SYSTEM_PROMPT if is_ultra else SUPER_FAST_PATCH_SYSTEM_PROMPT

    if not NEBIUS_API_KEY:
        final_error = (
            f"Stage 3 Error: NEBIUS_API_KEY is not configured. Live Nebius Token Factory "
            f"inference is required to patch '{file_report.relative_path}'."
        )
        print(f"  ❌ [NEBIUS TOKEN FACTORY] File {file_report.relative_path} FAILED: {final_error}", flush=True)
        return FilePatchResult(
            relative_path=file_report.relative_path,
            original_code=original_code,
            patched_code="",
            unified_diff="",
            assigned_tier=file_report.assigned_tier,
            model_used=model_id,
            syntax_valid=False,
            telemetry=TelemetryEvent(
                file_path=file_report.relative_path,
                assigned_tier=file_report.assigned_tier,
                model_used=model_id,
                complexity_score=file_report.normalized_score,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                actual_cost_usd=0.0,
                hypothetical_ultra_cost_usd=0.0,
                cost_saved_usd=0.0,
                execution_mode="NEBIUS_TOKEN_FACTORY",
            ),
            status="FAILED",
            error_message=final_error,
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
        pydantic_guidance = ""
        if "pydantic" in (migration_delta.library or "").lower():
            pydantic_guidance = (
                "\nKey Pydantic v2 Architecture Rules:\n"
                "- GenericModel is deprecated/removed in v2: generic models must inherit directly from (BaseModel, Generic[T]). Never import or use GenericModel.\n"
                "- __root__ is deprecated/removed in v2: custom root models must inherit from RootModel[T] (from pydantic import RootModel). Access value via .root instead of .__root__.\n"
                "- parse_obj_as(Type, data) -> TypeAdapter(Type).validate_python(data) (from pydantic import TypeAdapter).\n"
                "- @validator -> @field_validator: E.g. `@field_validator('field')\n@classmethod\ndef check_field(cls, v: int) -> int: return v`. Never pass always=True, each_item=True, or `field` parameter.\n"
                "- @root_validator -> @model_validator: NEVER use bare @model_validator without mode! Always specify mode parameter:\n"
                "  * For mode='after': `@model_validator(mode='after')\ndef verify(self): ... return self` (INSTANCE method taking self, returning self).\n"
                "  * For mode='before': `@model_validator(mode='before')\n@classmethod\ndef verify(cls, data: Any) -> Any: ... return data` (CLASS method taking cls, data).\n"
                "- `.construct()` -> `.model_construct()` drops unknown keys unless the model sets extra='allow'; preserve v1 behavior (set extra='allow' or copy extras explicitly).\n"
                "- BaseSettings is in pydantic_settings (from pydantic_settings import BaseSettings, SettingsConfigDict).\n"
            )

        messages = [
            {
                "role": "system",
                "content": system_prompt + "\n\nCRITICAL: DO NOT output any conversational text, explanations, or multiple snippets. Output ONLY a single python code block starting with ```python containing the complete, upgraded file with all original classes and functions preserved. NEVER use ellipses (...) or placeholder comments (e.g. '# ... existing code ...'). You must provide the full file implementation.",
            },
            {
                "role": "user",
                "content": (
                    f"File: {file_report.relative_path}\n"
                    f"Target Library: {migration_delta.library} (Upgrade {migration_delta.from_version} -> {migration_delta.to_version})\n"
                    f"Detected Usages: {[u.symbol_name for u in file_report.usages]}\n\n"
                    f"Migration Rules for this file:\n{filtered_rules_json}\n"
                    f"{pydantic_guidance}\n"
                    f"Original Python Source:\n```python\n{original_code}\n```"
                ),
            },
        ]

        calc_max_tokens = 8192 if (is_ultra or len(original_code.splitlines()) > 120) else 4000
        print(f"  🌐 [LIVE NEBIUS TOKEN FACTORY] Patching {file_report.relative_path} via {model_id} ({len(relevant_rules)} rules, max_tokens={calc_max_tokens})...", flush=True)
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.05,
            max_tokens=calc_max_tokens,
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
            candidate_code,
            original_code,
            migration_delta.library,
            relevant_rules,
            file_path=file_report.file_path,
        )

        # If candidate is missing original classes or functions, attempt surgical AST grafting
        if not is_valid and ("missing original classes" in (validation_error or "") or "missing original functions" in (validation_error or "")):
            spliced = _splice_ast_patch(original_code, candidate_code)
            if spliced:
                spliced_valid, spliced_error = _validate_patch(
                    spliced,
                    original_code,
                    migration_delta.library,
                    relevant_rules,
                    file_path=file_report.file_path,
                )
                if spliced_valid:
                    candidate_code = spliced
                    is_valid = True
                    validation_error = None
                    print(f"  🔧 [AST Graft] Surgically preserved original classes/functions in {file_report.relative_path}", flush=True)

        if is_valid:
            patched_code = candidate_code
            print(f"  ✅ [LIVE NEBIUS TOKEN FACTORY] Successfully patched {file_report.relative_path}", flush=True)
        else:
            # Retry once with the error!
            print(f"  ⚠️ [Validation Notice] {file_report.relative_path} failed: {validation_error}. Retrying once with error...", flush=True)
            hints = []
            if "ellipsis" in validation_error:
                hints.append("NEVER use ellipsis (...) or truncated placeholders like '# ... rest of code ...'. You must output the entire, unabridged code for the file.")
            if "GenericModel" in validation_error:
                hints.append("GenericModel is removed in v2: generic classes must inherit directly from (BaseModel, Generic[T]). Never import GenericModel.")
            if "__root__" in validation_error:
                hints.append("`__root__` is removed in v2: models with a single root must inherit from RootModel[T] (from pydantic import RootModel). Access via .root.")
            if "validator" in validation_error:
                hints.append("@validator -> @field_validator and @root_validator -> @model_validator in v2.")
            if "model_validator" in validation_error or "takes 0 positional arguments" in validation_error:
                hints.append("NEVER use bare `@model_validator` without arguments! Specify `@model_validator(mode='after')` on instance method `def method(self): ... return self` or `@model_validator(mode='before')` on `@classmethod def method(cls, data): ... return data`.")
            hints_str = ("\nFix Guidance:\n" + "\n".join(f"- {h}" for h in hints) + "\n") if hints else ""

            retry_prompt = (
                f"Your previous patch for '{file_report.relative_path}' failed validation:\n"
                f"ERROR: {validation_error}\n"
                f"{hints_str}\n"
                f"Please fix this error immediately. Ensure:\n"
                f"1. Valid Python syntax with ALL original classes, methods, and functions preserved. Do not omit or truncate any class or function.\n"
                f"2. ZERO deprecated symbols remain from target library {migration_delta.library}.\n"
                f"3. Start output immediately with ```python and output the complete upgraded file."
            )
            messages.append({"role": "assistant", "content": f"```python\n{candidate_code}\n```"})
            messages.append({"role": "user", "content": retry_prompt})

            retry_response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.05,
                max_tokens=8192,
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
                retry_candidate,
                original_code,
                migration_delta.library,
                relevant_rules,
                file_path=file_report.file_path,
            )

            # If retry candidate is missing original classes or functions, attempt surgical AST grafting
            if not retry_is_valid and ("missing original classes" in (retry_error or "") or "missing original functions" in (retry_error or "")):
                spliced_retry = _splice_ast_patch(original_code, retry_candidate)
                if spliced_retry:
                    spliced_valid, spliced_err = _validate_patch(
                        spliced_retry,
                        original_code,
                        migration_delta.library,
                        relevant_rules,
                        file_path=file_report.file_path,
                    )
                    if spliced_valid:
                        retry_candidate = spliced_retry
                        retry_is_valid = True
                        retry_error = None
                        print(f"  🔧 [AST Graft] Surgically preserved original classes/functions on retry for {file_report.relative_path}", flush=True)

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
    build_log: Optional[str] = None,
) -> Tuple[MigrationDelta, Stage3MigrationReport]:
    """
    Runs Stage 2 (Tavily Live Delta) + Stage 3 (Nebius Token Factory Smart Router).
    1. Runs LibCST deterministic codemods before Stage 3 for mechanical renames.
    2. Sends only residual files with complex/architectural patterns to Nemotron.
    3. Processes residual files in parallel using ThreadPoolExecutor.
    4. Writes successfully patched files to `output_dir`. Never writes failed files.
    """
    migration_delta = build_migration_delta(blast_report, build_log)

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
                        original_code=file_rep.source_code or (
                            Path(file_rep.file_path).read_text(encoding="utf-8")
                            if file_rep.file_path and Path(file_rep.file_path).exists()
                            else ""
                        ),
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
    codemod_count = 0
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

        if patch_res.model_used == "LIBCST_DETERMINISTIC_CODEMOD" or getattr(patch_res.telemetry, "execution_mode", "") == "LIBCST_DETERMINISTIC_CODEMOD":
            codemod_count += 1
        elif patch_res.assigned_tier == "NEMOTRON_SUPER":
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
        codemod_files_count=codemod_count,
        super_files_count=super_count,
        ultra_files_count=ultra_count,
        total_latency_ms=round(total_latency, 2),
        total_actual_cost_usd=round(total_actual_cost, 6),
        total_hypothetical_ultra_cost_usd=round(total_hypo_cost, 6),
        total_cost_savings_pct=savings_pct,
        patches=patches,
    )

    return migration_delta, stage3_report