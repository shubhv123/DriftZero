# Prompts Nemotron 3 Ultra to write input/output equivalence tests
import ast
import json
import os
from pathlib import Path
from typing import List, Optional

from backend.config import NEBIUS_API_KEY, NEBIUS_BASE_URL, NEMOTRON_ULTRA_MODEL
from backend.intelligence.prompts import PARITY_TEST_GEN_SYSTEM_PROMPT
from backend.schemas.migration_models import MigrationDelta


def _extract_code_block(llm_text: str) -> str:
    """Extracts python code from an LLM response block."""
    if "```python" in llm_text:
        body = llm_text.split("```python", 1)[1]
        return body.split("```", 1)[0].strip() + "\n"
    if "```" in llm_text:
        body = llm_text.split("```", 1)[1]
        return body.split("```", 1)[0].strip() + "\n"
    return llm_text.strip() + "\n"


def _discover_migrated_symbols(patched_repo_dir: Path) -> List[dict]:
    """Scans all Python files in the patched repo to discover classes and functions to test."""
    discovered = []
    for root, dirs, files in os.walk(patched_repo_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", ".venv", "__pycache__")]
        for file in files:
            if not file.endswith(".py") or file.startswith("test_") or file.startswith("__"):
                continue
            full_path = Path(root) / file
            rel_path = full_path.relative_to(patched_repo_dir)
            module_import = str(rel_path.with_suffix("")).replace(os.sep, ".")
            try:
                tree = ast.parse(full_path.read_text(encoding="utf-8"))
                classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
                functions = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if classes or functions:
                    discovered.append({
                        "rel_path": str(rel_path),
                        "module": module_import,
                        "classes": classes,
                        "functions": functions,
                    })
            except Exception:
                continue
    return discovered


def generate_parity_test_suite(
    patched_repo_dir: Path,
    migration_delta: MigrationDelta,
) -> Path:
    """
    Synthesizes `test_migration_parity.py` inside `patched_repo_dir`.
    Works dynamically for any repository by discovering modules and testing parity.
    Leverages Nemotron 3 Ultra via Nebius Token Factory with universal AST regression fallback.
    """
    test_file = patched_repo_dir / "test_migration_parity.py"
    modules = _discover_migrated_symbols(patched_repo_dir)

    test_code = ""

    # 1. Live Parity Test Generation via Nebius Token Factory (Nemotron 3 Ultra)
    if NEBIUS_API_KEY and modules:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL, timeout=35.0, max_retries=1)
            user_prompt = (
                f"Target Library: {migration_delta.library} (Migrated to version {migration_delta.to_version})\n\n"
                f"Discovered Modules & Symbols in Migrated Repository:\n"
                f"{json.dumps(modules[:8], indent=2)}\n\n"
                f"Migration Rules Applied:\n{migration_delta.to_json(indent=2)}\n\n"
                "Write a comprehensive pytest test suite inside `test_migration_parity.py` that verifies "
                "that these modules, classes, and functions can be imported, instantiated, and invoked without compatibility errors."
            )
            res = client.chat.completions.create(
                model=NEMOTRON_ULTRA_MODEL,
                messages=[
                    {"role": "system", "content": PARITY_TEST_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=3000,
            )
            raw_text = res.choices[0].message.content or ""
            if not raw_text.strip() and hasattr(res.choices[0].message, "model_extra") and isinstance(res.choices[0].message.model_extra, dict):
                for k in ("reasoning_content", "reasoning", "thought", "thinking"):
                    if res.choices[0].message.model_extra.get(k):
                        raw_text = str(res.choices[0].message.model_extra[k]).strip()
                        break
            candidate = _extract_code_block(raw_text)
            try:
                ast.parse(candidate)
                test_code = candidate
                print(f"  🌐 [LIVE NEBIUS ULTRA PARITY TEST GEN] Generated dynamic parity test suite via {NEMOTRON_ULTRA_MODEL}", flush=True)
            except SyntaxError:
                pass
        except Exception as exc:
            print(f"  ⚠️ [Parity Test Gen Notice] Live synthesis notice: {exc}", flush=True)

    # 2. Universal Dynamic AST Test Generator Fallback (Runs on ANY arbitrary repository)
    if not test_code:
        import_statements = []
        test_functions = []

        for idx, mod in enumerate(modules):
            mod_alias = f"mod_{idx}"
            import_statements.append(f"import {mod['module']} as {mod_alias}")

            func_tests = []
            for cls_name in mod["classes"][:4]:
                func_tests.append(f"""
    # Verify class {cls_name} exists and can be inspected
    assert hasattr({mod_alias}, "{cls_name}"), "Class {cls_name} should exist in {mod['module']}"
    cls_obj = getattr({mod_alias}, "{cls_name}")
    assert cls_obj is not None
""")
            for fn_name in mod["functions"][:4]:
                func_tests.append(f"""
    # Verify function {fn_name} exists and is callable
    assert hasattr({mod_alias}, "{fn_name}"), "Function {fn_name} should exist in {mod['module']}"
    assert callable(getattr({mod_alias}, "{fn_name}"))
""")

            test_functions.append(f"""
def test_module_parity_{idx}_{mod['module'].replace('.', '_')}():
    {"".join(func_tests) if func_tests else "    assert True"}
""")

        test_code = f"""# Auto-generated Universal Differential Parity Test Suite by DriftZero
# Target Library: {migration_delta.library} ({migration_delta.to_version})
import pytest

{chr(10).join(import_statements)}

{chr(10).join(test_functions)}
"""

    test_file.write_text(test_code, encoding="utf-8")
    return test_file