# Differential equivalence test generator with golden.json verification
import ast
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _discover_migrated_symbols(repo_dir: Path) -> List[Dict[str, Any]]:
    """
    Scans all Python files in the repo to discover public classes, their constructor/fields,
    and public functions with their argument names and type annotations.
    """
    discovered = []
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", ".venv", "__pycache__", ".pytest_cache")]
        for file in sorted(files):
            if not file.endswith(".py") or file.startswith("test_") or file.startswith("__"):
                continue
            full_path = Path(root) / file
            rel_path = full_path.relative_to(repo_dir)
            module_import = str(rel_path.with_suffix("")).replace(os.sep, ".")
            try:
                tree = ast.parse(full_path.read_text(encoding="utf-8"))

                classes = []
                for n in tree.body:
                    if isinstance(n, ast.ClassDef) and not n.name.startswith("_") and n.name not in ("Config", "Meta"):
                        fields: Dict[str, str] = {}
                        for item in n.body:
                            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                type_name = ast.unparse(item.annotation) if hasattr(ast, "unparse") else ""
                                fields[item.target.id] = type_name
                            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
                                for a in item.args.args:
                                    if a.arg not in ("self", "cls"):
                                        t = ast.unparse(a.annotation) if getattr(a, "annotation", None) and hasattr(ast, "unparse") else ""
                                        fields[a.arg] = t
                        classes.append({
                            "name": n.name,
                            "fields": fields,
                        })

                functions = []
                for n in tree.body:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
                        args: Dict[str, str] = {}
                        for a in n.args.args:
                            if a.arg not in ("self", "cls"):
                                t = ast.unparse(a.annotation) if getattr(a, "annotation", None) and hasattr(ast, "unparse") else ""
                                args[a.arg] = t
                        functions.append({
                            "name": n.name,
                            "args": args,
                        })

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


def _generate_sample_input_for_field(field_name: str, type_annotation: str = "") -> Any:
    """Generates realistic sample values based on AST field name heuristics and type annotations."""
    name = field_name.lower()
    t_clean = type_annotation.lower().strip()

    # Special handling for common field names
    if name == "postal_code":
        return "94105"
    if "email" in name:
        return "dev@example.com"
    if name in ("sender_country", "receiver_country", "country"):
        return "US"
    if name in ("amount", "amount_usd", "price", "rate", "fee", "balance", "cost", "fx_rate"):
        return 100.0
    if name in ("quantity", "count", "limit", "retries", "max_retries", "timeout_seconds", "attempt_count", "unit_price_cents", "qty"):
        return 5
    if name in ("created_at", "updated_at", "timestamp", "date"):
        return "2025-01-01T12:00:00"
    if name in ("is_", "has_", "active", "enabled", "requires_manual_compliance_review"):
        return False
    if name in ("tags", "list", "categories"):
        return ["item_1", "item_2"]
    if name in ("payload", "data", "meta", "metadata", "config", "data_object", "payload_snapshot"):
        return {"status": "ok", "env": "test"}
    if name == "street":
        return "123 Market St"
    if name == "city":
        return "San Francisco"
    if name == "sku":
        return "SKU-TEST-001"
    if name.endswith("_id") or name == "id" or name in ("order_id", "customer_id", "transaction_id", "trace_id", "actor_id", "event_id"):
        return f"test_{field_name}_123"
    if name == "primary_address":
        return {"street": "123 Market St", "city": "San Francisco", "postal_code": "94105"}
    if name == "items":
        return [{"sku": "SKU-001", "quantity": 2, "unit_price_cents": 500}]
    if name == "order":
        return {"order_id": "test_order_123", "created_at": "2025-01-01T12:00:00", "items": [{"sku": "SKU-1", "quantity": 1, "unit_price_cents": 100}]}
    if name == "raw_dict":
        return {"order_id": "test_order_123", "created_at": "2025-01-01T12:00:00", "items": [{"sku": "SKU-1", "quantity": 1, "unit_price_cents": 100}]}
    if name == "raw_json":
        return json.dumps({"order_id": "test_order_123", "created_at": "2025-01-01T12:00:00", "items": [{"sku": "SKU-1", "quantity": 1, "unit_price_cents": 100}]})
    if name == "prompt":
        return {"system_instruction": "Summarize", "user_transcript": "User dialog", "temperature": 0.5}
    if name == "scores" or name == "__root__":
        return [1.0, 2.5, 3.0]

    # Type-directed synthesis
    if "int" in t_clean:
        return 5
    if "float" in t_clean:
        return 10.5
    if "bool" in t_clean:
        return True
    if "list" in t_clean:
        return ["item_1", "item_2"]
    if "dict" in t_clean:
        return {"key": "value"}
    if "datetime" in t_clean or "date" in t_clean:
        return "2025-01-01T12:00:00"

    return f"sample_{field_name}"


def _build_model_sample_inputs(cls_obj: Any, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Builds sample inputs at test runtime from the model's own fields
    (model_fields in Pydantic v2 or __fields__ in Pydantic v1, including all inherited ones).
    """
    fields_dict = getattr(cls_obj, "model_fields", None)
    if fields_dict is None:
        fields_dict = getattr(cls_obj, "__fields__", None)

    if not isinstance(fields_dict, dict):
        return dict(fallback or {})

    kwargs = dict(fallback or {})
    for f_name, f_info in fields_dict.items():
        type_str = ""
        # Pydantic v2: FieldInfo.annotation
        if hasattr(f_info, "annotation") and f_info.annotation is not None:
            type_str = getattr(f_info.annotation, "__name__", str(f_info.annotation))
        # Pydantic v1: ModelField.type_ / outer_type_
        elif hasattr(f_info, "type_") and f_info.type_ is not None:
            type_str = getattr(f_info.type_, "__name__", str(f_info.type_))
        elif hasattr(f_info, "outer_type_") and f_info.outer_type_ is not None:
            type_str = getattr(f_info.outer_type_, "__name__", str(f_info.outer_type_))

        if f_name not in kwargs or kwargs[f_name] is None:
            kwargs[f_name] = _generate_sample_input_for_field(f_name, type_str)
        elif kwargs[f_name] == f"sample_{f_name}" and type_str:
            kwargs[f_name] = _generate_sample_input_for_field(f_name, type_str)

    return kwargs


def _serialize_value(val: Any, mode: str = "json") -> Any:
    """
    Serializes models and Python values across legacy and modern library versions.
    Normalizes both sides through mode='json' (converts Decimals, datetimes, Enums, sets to JSON-safe types).
    """
    if val is None:
        return None

    # Handle Enums
    if hasattr(val, "value"):
        return _serialize_value(val.value, mode=mode)

    # Modern Pydantic v2 / dataclasses with model_dump
    if hasattr(val, "model_dump") and callable(getattr(val, "model_dump")):
        if mode == "json":
            try:
                dumped = val.model_dump(mode="json")
                return _serialize_value(dumped, mode=mode)
            except Exception:
                try:
                    return json.loads(json.dumps(val.model_dump(), default=str))
                except Exception:
                    pass
        return val.model_dump()

    # Legacy Pydantic v1 / objects with dict()
    if hasattr(val, "dict") and callable(getattr(val, "dict")):
        if mode == "json":
            if hasattr(val, "json") and callable(getattr(val, "json")):
                try:
                    return json.loads(val.json())
                except Exception:
                    pass
            try:
                return json.loads(json.dumps(val.dict(), default=str))
            except Exception:
                pass
        return val.dict()

    if isinstance(val, (int, float, str, bool)):
        return val
    if isinstance(val, (list, tuple)):
        return [_serialize_value(item, mode=mode) for item in val]
    if isinstance(val, (set, frozenset)):
        serialized_items = [_serialize_value(item, mode=mode) for item in val]
        return sorted(serialized_items, key=lambda x: json.dumps(x, default=str) if isinstance(x, (dict, list)) else str(x))
    if isinstance(val, dict):
        return {str(k): _serialize_value(v, mode=mode) for k, v in val.items()}
    if isinstance(val, Decimal):
        return str(val) if mode == "json" else float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()

    return str(val)


def dry_run_candidate_in_record_mode(candidate_code: str, repo_dir: Path) -> bool:
    """
    Dry-runs an LLM-generated test suite in record mode (DRIFTZERO_RECORD_GOLDEN=1)
    before accepting it. Returns True if dry-run succeeds, False if it fails.
    If it fails, cleans up any temporary test files or partial golden files.
    """
    temp_test = repo_dir / "_dry_run_parity_test.py"
    golden_file = repo_dir / "golden.json"
    had_golden = golden_file.exists()
    old_golden_bytes = golden_file.read_bytes() if had_golden else None

    try:
        # Check AST first
        ast.parse(candidate_code)

        temp_test.write_text(candidate_code, encoding="utf-8")
        env = os.environ.copy()
        env["DRIFTZERO_RECORD_GOLDEN"] = "1"
        repo_str = str(repo_dir)
        src_str = str(repo_dir / "src")
        env["PYTHONPATH"] = f"{repo_str}:{src_str}:{env.get('PYTHONPATH', '')}"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(temp_test), "-q"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            env=env,
            timeout=25,
        )
        if proc.returncode == 0:
            print("  ✅ [LLM Parity Test] Dry-run in record mode PASSED. Accepting candidate.", flush=True)
            return True
        else:
            print(f"  ❌ [LLM Parity Test Rejected] Dry-run in record mode failed (exit {proc.returncode}): {proc.stderr[:300] or proc.stdout[:300]}", flush=True)
            return False
    except Exception as exc:
        print(f"  ❌ [LLM Parity Test Rejected] Dry-run exception: {exc}", flush=True)
        return False
    finally:
        if temp_test.exists():
            temp_test.unlink(missing_ok=True)
        if not had_golden and golden_file.exists():
            golden_file.unlink(missing_ok=True)
        elif had_golden and old_golden_bytes is not None and golden_file.exists():
            golden_file.write_bytes(old_golden_bytes)


def _build_deterministic_differential_suite(
    modules: List[Dict[str, Any]],
    migration_delta: MigrationDelta,
) -> str:
    """
    Builds a robust, deterministic differential parity test suite:
    1. Emits sample_kwargs with repr() (not json.dumps).
    2. Builds inputs at test runtime from the model's own fields (model_fields or __fields__, including inherited ones).
    3. Maintains a module-level global _golden_store dictionary.
    4. Normalizes both sides through _serialize_value(mode="json") before comparing.
    5. Compares error type only, not message.
    """
    test_functions = []

    for idx, mod in enumerate(modules):
        mod_name = mod["module"]
        fn_name_safe = mod_name.replace(".", "_")

        class_names = [c["name"] for c in mod["classes"]]
        func_names = [f["name"] for f in mod["functions"]]
        all_symbols = class_names + func_names
        if not all_symbols:
            continue

        symbol_tests = []

        # 1. Model differential tests
        for cls_info in mod["classes"]:
            c_name = cls_info["name"]
            flds = cls_info["fields"]
            sample_inputs = {f: _generate_sample_input_for_field(f, flds.get(f, "")) for f in flds} if flds else {}
            sample_repr = repr(sample_inputs)

            symbol_tests.append(f"""
    # Differential Parity Test for Model: {mod_name}.{c_name}
    sample_kwargs = _build_model_sample_inputs({c_name}, fallback={sample_repr})
    verify_model_differential("{mod_name}", "{c_name}", {c_name}, sample_kwargs)
""")

        # 2. Function differential tests
        for fn_info in mod["functions"]:
            f_name = fn_info["name"]
            args = fn_info["args"]
            sample_kwargs = {a: _generate_sample_input_for_field(a, args.get(a, "")) for a in args} if args else {}
            sample_repr = repr(sample_kwargs)

            symbol_tests.append(f"""
    # Differential Parity Test for Function: {mod_name}.{f_name}
    sample_kwargs = {sample_repr}
    verify_function_differential("{mod_name}", "{f_name}", {f_name}, sample_kwargs)
""")

        import_statement = f"from {mod_name} import {', '.join(all_symbols)}"

        test_functions.append(f"""
def test_differential_parity_{idx}_{fn_name_safe}():
    # Direct import: fails loudly with ImportError if the symbol was renamed or deleted
    {import_statement}
{"".join(symbol_tests)}
""")

    code = f'''# Auto-generated Differential Parity Test Suite by DriftZero
# Target Library: {migration_delta.library} ({migration_delta.to_version})
import os
import sys
import json
import pytest
from pathlib import Path
from decimal import Decimal
from datetime import datetime, date

# Ensure workspace root and src directory are on sys.path
_repo_root = Path(__file__).resolve().parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
_src_dir = _repo_root / "src"
if _src_dir.exists() and str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

GOLDEN_FILE = Path(__file__).resolve().parent / "golden.json"
IS_RECORDING = os.getenv("DRIFTZERO_RECORD_GOLDEN", "0") == "1"

# Module-level golden store
_golden_store: dict = {{}}

def _load_golden() -> dict:
    global _golden_store
    if _golden_store:
        return _golden_store
    if not GOLDEN_FILE.exists():
        if not IS_RECORDING:
            raise AssertionError(
                f"Golden baseline file '{{GOLDEN_FILE}}' not found! "
                "The old-version container must run with DRIFTZERO_RECORD_GOLDEN=1 first "
                "to record golden baseline outputs before running differential verification."
            )
        return _golden_store
    try:
        _golden_store.clear()
        _golden_store.update(json.loads(GOLDEN_FILE.read_text(encoding="utf-8")))
        return _golden_store
    except Exception as exc:
        raise AssertionError(f"Failed to read golden baseline file: {{exc}}")

def _save_golden():
    global _golden_store
    GOLDEN_FILE.write_text(json.dumps(_golden_store, indent=2, default=str), encoding="utf-8")


def _generate_sample_input_for_field(field_name: str, type_annotation: str = ""):
    name = field_name.lower()
    t_clean = type_annotation.lower().strip()

    if name == "postal_code":
        return "94105"
    if "email" in name:
        return "dev@example.com"
    if name in ("sender_country", "receiver_country", "country"):
        return "US"
    if name in ("amount", "amount_usd", "price", "rate", "fee", "balance", "cost", "fx_rate"):
        return 100.0
    if name in ("quantity", "count", "limit", "retries", "max_retries", "timeout_seconds", "attempt_count", "unit_price_cents", "qty"):
        return 5
    if name in ("created_at", "updated_at", "timestamp", "date"):
        return "2025-01-01T12:00:00"
    if name in ("is_", "has_", "active", "enabled", "requires_manual_compliance_review"):
        return False
    if name in ("tags", "list", "categories"):
        return ["item_1", "item_2"]
    if name in ("payload", "data", "meta", "metadata", "config", "data_object", "payload_snapshot"):
        return {{"status": "ok", "env": "test"}}
    if name == "street":
        return "123 Market St"
    if name == "city":
        return "San Francisco"
    if name == "sku":
        return "SKU-TEST-001"
    if name.endswith("_id") or name == "id" or name in ("order_id", "customer_id", "transaction_id", "trace_id", "actor_id", "event_id"):
        return f"test_{{field_name}}_123"
    if name == "primary_address":
        return {{"street": "123 Market St", "city": "San Francisco", "postal_code": "94105"}}
    if name == "items":
        return [{{"sku": "SKU-001", "quantity": 2, "unit_price_cents": 500}}]
    if name == "order":
        return {{"order_id": "test_order_123", "created_at": "2025-01-01T12:00:00", "items": [{{"sku": "SKU-1", "quantity": 1, "unit_price_cents": 100}}]}}
    if name == "raw_dict":
        return {{"order_id": "test_order_123", "created_at": "2025-01-01T12:00:00", "items": [{{"sku": "SKU-1", "quantity": 1, "unit_price_cents": 100}}]}}
    if name == "raw_json":
        return json.dumps({{"order_id": "test_order_123", "created_at": "2025-01-01T12:00:00", "items": [{{"sku": "SKU-1", "quantity": 1, "unit_price_cents": 100}}]}})
    if name == "prompt":
        return {{"system_instruction": "Summarize", "user_transcript": "User dialog", "temperature": 0.5}}
    if name == "scores" or name == "__root__":
        return [1.0, 2.5, 3.0]

    if "int" in t_clean:
        return 5
    if "float" in t_clean:
        return 10.5
    if "bool" in t_clean:
        return True
    if "list" in t_clean:
        return ["item_1", "item_2"]
    if "dict" in t_clean:
        return {{"key": "value"}}
    if "datetime" in t_clean or "date" in t_clean:
        return "2025-01-01T12:00:00"

    return f"sample_{{field_name}}"


def _build_model_sample_inputs(cls_obj, fallback=None):
    """
    Builds sample inputs at test runtime from the model's own fields
    (model_fields in Pydantic v2 or __fields__ in Pydantic v1, including all inherited ones).
    """
    fields_dict = getattr(cls_obj, "model_fields", None)
    if fields_dict is None:
        fields_dict = getattr(cls_obj, "__fields__", None)

    if not isinstance(fields_dict, dict):
        return dict(fallback or {{}})

    kwargs = dict(fallback or {{}})
    for f_name, f_info in fields_dict.items():
        type_str = ""
        if hasattr(f_info, "annotation") and f_info.annotation is not None:
            type_str = getattr(f_info.annotation, "__name__", str(f_info.annotation))
        elif hasattr(f_info, "type_") and f_info.type_ is not None:
            type_str = getattr(f_info.type_, "__name__", str(f_info.type_))
        elif hasattr(f_info, "outer_type_") and f_info.outer_type_ is not None:
            type_str = getattr(f_info.outer_type_, "__name__", str(f_info.outer_type_))

        if f_name not in kwargs or kwargs[f_name] is None:
            kwargs[f_name] = _generate_sample_input_for_field(f_name, type_str)
        elif kwargs[f_name] == f"sample_{{f_name}}" and type_str:
            kwargs[f_name] = _generate_sample_input_for_field(f_name, type_str)

    return kwargs


def _serialize_value(val, mode="json"):
    """
    Serializes models and Python values across legacy and modern library versions.
    Normalizes both sides through mode='json' (converts Decimals, datetimes, Enums, sets to JSON-safe types).
    """
    if val is None:
        return None

    if hasattr(val, "value"):
        return _serialize_value(val.value, mode=mode)

    # Modern Pydantic v2 / dataclasses with model_dump
    if hasattr(val, "model_dump") and callable(getattr(val, "model_dump")):
        if mode == "json":
            try:
                dumped = val.model_dump(mode="json")
                return _serialize_value(dumped, mode=mode)
            except Exception:
                try:
                    return json.loads(json.dumps(val.model_dump(), default=str))
                except Exception:
                    pass
        return val.model_dump()

    # Legacy Pydantic v1 / objects with dict()
    if hasattr(val, "dict") and callable(getattr(val, "dict")):
        if mode == "json":
            if hasattr(val, "json") and callable(getattr(val, "json")):
                try:
                    return json.loads(val.json())
                except Exception:
                    pass
            try:
                return json.loads(json.dumps(val.dict(), default=str))
            except Exception:
                pass
        return val.dict()

    if isinstance(val, (int, float, str, bool)):
        return val
    if isinstance(val, (list, tuple)):
        return [_serialize_value(item, mode=mode) for item in val]
    if isinstance(val, (set, frozenset)):
        serialized_items = [_serialize_value(item, mode=mode) for item in val]
        return sorted(serialized_items, key=lambda x: json.dumps(x, default=str) if isinstance(x, (dict, list)) else str(x))
    if isinstance(val, dict):
        return {{str(k): _serialize_value(v, mode=mode) for k, v in val.items()}}
    if isinstance(val, Decimal):
        return str(val) if mode == "json" else float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()

    return str(val)


def verify_model_differential(mod_name: str, cls_name: str, cls_obj, sample_kwargs: dict = None):
    """
    Differential parity assertion for models:
    - Builds inputs at test runtime from model_fields or __fields__, including inherited ones.
    - Normalizes both sides through _serialize_value(mode="json") before comparing.
    - Compares error type only, not message.
    """
    global _golden_store
    golden_key = f"model::{{mod_name}}.{{cls_name}}"

    runtime_inputs = _build_model_sample_inputs(cls_obj, fallback=sample_kwargs)
    final_kwargs = runtime_inputs if runtime_inputs else dict(sample_kwargs or {{}})

    if IS_RECORDING:
        record = {{}}
        try:
            instance = cls_obj(**final_kwargs)
            record["status"] = "ok"
            record["output"] = _serialize_value(instance, mode="json")
            record["model_dump"] = record["output"]
            record["model_dump_json"] = record["output"]
        except Exception as exc:
            record["status"] = "error"
            record["error_type"] = type(exc).__name__

        # Record error type on invalid / empty inputs
        try:
            cls_obj()
            record["empty_input_status"] = "ok"
        except Exception as exc:
            record["empty_input_status"] = "error"
            record["invalid_error_type"] = type(exc).__name__

        _load_golden()
        _golden_store[golden_key] = record
        _save_golden()
        return

    # Verification Mode (New Container)
    golden = _load_golden()
    if golden_key not in golden:
        raise AssertionError(
            f"Missing golden baseline entry for model '{{mod_name}}.{{cls_name}}'. "
            "Old-version container must record golden.json before verification."
        )

    expected = golden[golden_key]

    if expected["status"] == "ok":
        instance = cls_obj(**final_kwargs)

        # Normalize both sides through _serialize_value(mode="json") before comparing
        actual_norm = _serialize_value(instance, mode="json")
        expected_norm = _serialize_value(
            expected.get("output") or expected.get("model_dump_json") or expected.get("model_dump"),
            mode="json"
        )
        assert actual_norm == expected_norm, (
            f"Differential Model Parity Mismatch for '{{cls_name}}':\\n"
            f"Expected (Golden): {{expected_norm}}\\n"
            f"Actual   (New):    {{actual_norm}}"
        )
    else:
        # Expected error on instantiation: compare error type only, not message
        with pytest.raises(Exception) as exc_info:
            cls_obj(**final_kwargs)
        actual_type = exc_info.value.__class__.__name__
        expected_type = expected["error_type"]
        assert actual_type == expected_type, (
            f"Error type mismatch for '{{cls_name}}': expected {{expected_type}}, got {{actual_type}}"
        )

    # Assert error type on invalid / empty inputs: compare error type only, not message
    if expected.get("empty_input_status") == "error":
        with pytest.raises(Exception) as exc_info:
            cls_obj()
        actual_type = exc_info.value.__class__.__name__
        expected_type = expected["invalid_error_type"]
        assert actual_type == expected_type, (
            f"Validation error type mismatch for invalid inputs on '{{cls_name}}': "
            f"expected {{expected_type}}, got {{actual_type}}"
        )


def verify_function_differential(mod_name: str, fn_name: str, fn_obj, sample_kwargs: dict = None):
    """
    Differential parity assertion for functions:
    - Normalizes both sides through _serialize_value(mode="json") before comparing.
    - Compares error type only, not message.
    """
    global _golden_store
    golden_key = f"func::{{mod_name}}.{{fn_name}}"
    final_kwargs = dict(sample_kwargs or {{}})

    if IS_RECORDING:
        record = {{}}
        try:
            res = fn_obj(**final_kwargs)
            record["status"] = "ok"
            record["output"] = _serialize_value(res, mode="json")
        except Exception as exc:
            record["status"] = "error"
            record["error_type"] = type(exc).__name__

        _load_golden()
        _golden_store[golden_key] = record
        _save_golden()
        return

    # Verification Mode (New Container)
    golden = _load_golden()
    if golden_key not in golden:
        raise AssertionError(
            f"Missing golden baseline entry for function '{{mod_name}}.{{fn_name}}'. "
            "Old-version container must record golden.json before verification."
        )

    expected = golden[golden_key]

    if expected["status"] == "ok":
        res = fn_obj(**final_kwargs)
        actual_norm = _serialize_value(res, mode="json")
        expected_norm = _serialize_value(expected["output"], mode="json")
        assert actual_norm == expected_norm, (
            f"Differential Function Parity Mismatch for '{{fn_name}}':\\n"
            f"Expected (Golden): {{expected_norm}}\\n"
            f"Actual   (New):    {{actual_norm}}"
        )
    else:
        # Expected error on function execution: compare error type only, not message
        with pytest.raises(Exception) as exc_info:
            fn_obj(**final_kwargs)
        actual_type = exc_info.value.__class__.__name__
        expected_type = expected["error_type"]
        assert actual_type == expected_type, (
            f"Function error type mismatch for '{{fn_name}}': expected {{expected_type}}, got {{actual_type}}"
        )

{chr(10).join(test_functions)}
'''
    return code


def generate_parity_test_suite(
    patched_repo_dir: Path,
    migration_delta: MigrationDelta,
) -> Path:
    """
    Synthesizes `test_migration_parity.py` inside `patched_repo_dir`.
    - If ENABLE_LLM_PARITY_GEN=1 is set, prompts Nemotron 3 Ultra and dry-runs the candidate
      in record mode before accepting it. If dry-run fails, deletes/rejects it.
    - Otherwise, deletes the LLM path and uses the deterministic differential test generator.
    - Builds inputs at test runtime from model's own fields (model_fields or __fields__, including inherited ones).
    - Normalizes both sides through _serialize_value(mode="json") before comparing.
    - Compares error type only, not message.
    - Emits sample_kwargs with repr(), not json.dumps.
    - Uses module-level _golden_store dictionary.
    """
    test_file = patched_repo_dir / "test_migration_parity.py"
    modules = _discover_migrated_symbols(patched_repo_dir)

    test_code = ""

    # Optional Live LLM Parity Test Generation path
    # Dry-run in record mode is strictly required before accepting it!
    # If dry-run fails or LLM is disabled, delete/reject candidate and use deterministic generator.
    use_llm = os.getenv("ENABLE_LLM_PARITY_GEN", "0") == "1"
    if use_llm and NEBIUS_API_KEY and modules:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=NEBIUS_API_KEY, base_url=NEBIUS_BASE_URL, timeout=35.0, max_retries=1)
            user_prompt = (
                f"Target Library: {migration_delta.library} (Migrated to version {migration_delta.to_version})\n\n"
                f"Discovered Modules & Symbols in Repository:\n"
                f"{json.dumps(modules[:8], indent=2)}\n\n"
                f"Migration Rules Applied:\n{migration_delta.to_json(indent=2)}\n\n"
                "Write a differential test suite inside `test_migration_parity.py`:\n"
                "- In the old-version container (DRIFTZERO_RECORD_GOLDEN=1), call each public function and model "
                "on generated inputs and write outputs to `golden.json`.\n"
                "- In the new container, assert equality against `golden.json`, normalizing both sides through "
                "`_serialize_value(mode='json')`.\n"
                "- Compare error type only, not message.\n"
                "- Build inputs at test runtime from model_fields or __fields__.\n"
                "- Use module-level _golden_store.\n"
                "- Delete any hasattr/callable fallback and fail loudly with AssertionError on any mismatch."
            )
            res = client.chat.completions.create(
                model=NEMOTRON_ULTRA_MODEL,
                messages=[
                    {"role": "system", "content": PARITY_TEST_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=3500,
            )
            raw_text = res.choices[0].message.content or ""
            if not raw_text.strip() and hasattr(res.choices[0].message, "model_extra") and isinstance(res.choices[0].message.model_extra, dict):
                for k in ("reasoning_content", "reasoning", "thought", "thinking"):
                    if res.choices[0].message.model_extra.get(k):
                        raw_text = str(res.choices[0].message.model_extra[k]).strip()
                        break
            candidate = _extract_code_block(raw_text)
            if candidate and "golden.json" in candidate:
                if dry_run_candidate_in_record_mode(candidate, patched_repo_dir):
                    test_code = candidate
                    print(f"  🌐 [LIVE NEBIUS ULTRA PARITY TEST GEN] Dry-run passed; accepted parity test suite via {NEMOTRON_ULTRA_MODEL}", flush=True)
                else:
                    print("  ⚠️ [Parity Test Gen] Dry-run in record mode failed. Deleting LLM test candidate.", flush=True)
        except Exception as exc:
            print(f"  ⚠️ [Parity Test Gen Notice] Live synthesis notice: {exc}", flush=True)

    # Deterministic Differential Parity Test Generator
    if not test_code:
        test_code = _build_deterministic_differential_suite(modules, migration_delta)

    test_file.write_text(test_code, encoding="utf-8")
    return test_file