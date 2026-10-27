# Deterministic CST rewrites for mechanical migrations using LibCST
import ast
import difflib
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import libcst as cst

from backend.config import (
    PRICE_PER_1M_ULTRA_INPUT,
    PRICE_PER_1M_ULTRA_OUTPUT,
)
from backend.core.ast_scanner import FileASTVisitor
from backend.schemas.migration_models import (
    AffectedFileReport,
    BlastRadiusReport,
    FilePatchResult,
    MigrationDelta,
    MigrationRule,
    TelemetryEvent,
)


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


class PydanticV2MechanicalCodemod(cst.CSTTransformer):
    """
    Deterministic CST codemod for mechanical Pydantic v1 -> v2 migrations:
    - .dict() -> .model_dump()
    - .json() -> .model_dump_json()
    - .copy() -> .model_copy()
    - .parse_obj() -> .model_validate()
    - .parse_raw() -> .model_validate_json()
    - .schema_json() / .schema() -> .model_json_schema()
    - .construct() -> .model_construct()
    - Field(regex=...) -> Field(pattern=...)
    - Field(min_items=...) -> Field(min_length=...)
    - Field(max_items=...) -> Field(max_length=...)
    - class Config: orm_mode -> from_attributes
    - class Config: allow_population_by_field_name -> populate_by_name
    - class Config: anystr_strip_whitespace -> str_strip_whitespace
    - class Config: allow_mutation = False -> frozen = True (and allow_mutation = True -> frozen = False)
    - class Config: json_encoders -> emits @field_serializer("*", mode="plain", when_used="json")
    """

    def __init__(self):
        super().__init__()
        self.method_renames = {
            "dict": "model_dump",
            "json": "model_dump_json",
            "copy": "model_copy",
            "parse_obj": "model_validate",
            "parse_raw": "model_validate_json",
            "schema_json": "model_json_schema",
            "schema": "model_json_schema",
            "construct": "model_construct",
        }
        self.field_param_renames = {
            "regex": "pattern",
            "min_items": "min_length",
            "max_items": "max_length",
        }
        self.config_param_renames = {
            "orm_mode": "from_attributes",
            "allow_population_by_field_name": "populate_by_name",
            "anystr_strip_whitespace": "str_strip_whitespace",
        }
        self.has_config_dict = False
        self.has_field_serializer = False
        self.modified = False

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        # 1. Method call renames (.dict, .json, .copy, .parse_obj, .parse_raw, .schema_json, .construct)
        if isinstance(updated_node.func, cst.Attribute):
            attr_name = updated_node.func.attr.value
            if attr_name in self.method_renames:
                self.modified = True
                new_attr = self.method_renames[attr_name]
                return updated_node.with_changes(
                    func=updated_node.func.with_changes(attr=cst.Name(new_attr))
                )

        # 2. Field(...) parameter renames (regex, min_items, max_items)
        is_field = False
        if isinstance(updated_node.func, cst.Name) and updated_node.func.value == "Field":
            is_field = True
        elif isinstance(updated_node.func, cst.Attribute) and updated_node.func.attr.value == "Field":
            is_field = True

        if is_field:
            new_args = []
            changed = False
            for arg in updated_node.args:
                if arg.keyword and arg.keyword.value in self.field_param_renames:
                    changed = True
                    self.modified = True
                    new_kw = self.field_param_renames[arg.keyword.value]
                    new_args.append(arg.with_changes(keyword=cst.Name(new_kw)))
                else:
                    new_args.append(arg)
            if changed:
                return updated_node.with_changes(args=new_args)

        return updated_node

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        # Convert inner `class Config:` inside models to `model_config = ConfigDict(...)`
        # and emit `@field_serializer("*", mode="plain", when_used="json")` for json_encoders
        new_body = []
        class_modified = False

        for stmt in updated_node.body.body:
            if isinstance(stmt, cst.ClassDef) and stmt.name.value == "Config":
                class_modified = True
                self.modified = True
                self.has_config_dict = True

                dict_args = []
                json_enc_code = None

                for inner in stmt.body.body:
                    if isinstance(inner, cst.SimpleStatementLine):
                        for item in inner.body:
                            if isinstance(item, cst.Assign):
                                for target in item.targets:
                                    if isinstance(target.target, cst.Name):
                                        name = target.target.value
                                        val_str = cst.Module([]).code_for_node(item.value).strip()
                                        if name == "orm_mode":
                                            dict_args.append(f"from_attributes={val_str}")
                                        elif name == "allow_population_by_field_name":
                                            dict_args.append(f"populate_by_name={val_str}")
                                        elif name == "anystr_strip_whitespace":
                                            dict_args.append(f"str_strip_whitespace={val_str}")
                                        elif name == "allow_mutation":
                                            frozen_val = "True" if val_str == "False" else "False"
                                            dict_args.append(f"frozen={frozen_val}")
                                        elif name == "json_encoders":
                                            json_enc_code = val_str
                                            self.has_field_serializer = True
                                        else:
                                            dict_args.append(f"{name}={val_str}")

                args_joined = ", ".join(dict_args)
                config_str = f"model_config = ConfigDict({args_joined})"
                new_body.append(cst.parse_statement(config_str))

                if json_enc_code:
                    serializer_str = (
                        "@field_serializer(\"*\", mode=\"plain\", when_used=\"json\")\n"
                        "def _serialize_json_encoders(self, value):\n"
                        f"    _encoders = {json_enc_code}\n"
                        "    for _typ, _enc in _encoders.items():\n"
                        "        if isinstance(value, _typ):\n"
                        "            return _enc(value)\n"
                        "    return value\n"
                    )
                    for s in cst.parse_module(serializer_str.strip()).body:
                        new_body.append(s)
            else:
                new_body.append(stmt)

        if class_modified:
            return updated_node.with_changes(
                body=updated_node.body.with_changes(body=new_body)
            )

        return updated_node


class PydanticImportUpdater(cst.CSTTransformer):
    """Ensures ConfigDict and field_serializer are imported from pydantic when added by codemods."""

    def __init__(self, need_config_dict: bool, need_field_serializer: bool):
        super().__init__()
        self.need_config_dict = need_config_dict
        self.need_field_serializer = need_field_serializer
        self.pydantic_import_found = False

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
        if isinstance(updated_node.module, cst.Name) and updated_node.module.value == "pydantic":
            self.pydantic_import_found = True
            existing_names = {alias.name.value for alias in updated_node.names}
            new_aliases = list(updated_node.names)
            if self.need_config_dict and "ConfigDict" not in existing_names:
                new_aliases.append(cst.ImportAlias(name=cst.Name("ConfigDict")))
            if self.need_field_serializer and "field_serializer" not in existing_names:
                new_aliases.append(cst.ImportAlias(name=cst.Name("field_serializer")))
            return updated_node.with_changes(names=new_aliases)
        return updated_node

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if not self.pydantic_import_found and (self.need_config_dict or self.need_field_serializer):
            names = []
            if self.need_config_dict:
                names.append("ConfigDict")
            if self.need_field_serializer:
                names.append("field_serializer")
            imp_stmt = cst.parse_statement(f"from pydantic import {', '.join(names)}")
            return updated_node.with_changes(body=[imp_stmt] + list(updated_node.body))
        return updated_node


def apply_deterministic_codemods(code: str, target_library: str = "pydantic") -> Tuple[str, bool]:
    """
    Applies deterministic libcst codemods to source code.
    Returns (transformed_code, was_modified).
    """
    if not code.strip():
        return code, False

    try:
        tree = cst.parse_module(code)
    except Exception:
        return code, False

    transformer = PydanticV2MechanicalCodemod()
    transformed_tree = tree.visit(transformer)

    if not transformer.modified:
        return code, False

    if transformer.has_config_dict or transformer.has_field_serializer:
        import_updater = PydanticImportUpdater(
            need_config_dict=transformer.has_config_dict,
            need_field_serializer=transformer.has_field_serializer,
        )
        transformed_tree = transformed_tree.visit(import_updater)

    return transformed_tree.code, True


def partition_files_for_stage3(
    blast_report: BlastRadiusReport,
    migration_delta: MigrationDelta,
) -> Tuple[List[FilePatchResult], List[AffectedFileReport]]:
    """
    Runs deterministic libcst codemods BEFORE Stage 3:
    - If a file only contains mechanical changes and is completely resolved by libcst
      (leaving zero deprecated symbols according to FileASTVisitor), it is marked
      SUCCESS immediately with 0 tokens used and not sent to Nemotron.
    - Files with residual complex symbols (or files not modified by codemods) are
      returned as residual files to be sent to Nemotron.
    """
    resolved_patches: List[FilePatchResult] = []
    residual_files: List[AffectedFileReport] = []

    # Map of all deprecated symbols from migration rules
    deprecated_patterns: Set[str] = set()
    for rule in migration_delta.rules:
        if rule.symbol:
            deprecated_patterns.add(rule.symbol.strip().lower())
        if rule.old_syntax:
            deprecated_patterns.add(rule.old_syntax.strip().lower())

    for file_rep in blast_report.affected_files:
        original_code = file_rep.source_code or Path(file_rep.file_path).read_text(encoding="utf-8")
        codemodded_code, modified = apply_deterministic_codemods(original_code, migration_delta.library)

        if not modified:
            residual_files.append(file_rep)
            continue

        # Check if codemodded code is syntactically valid Python
        try:
            cand_tree = ast.parse(codemodded_code)
        except SyntaxError:
            residual_files.append(file_rep)
            continue

        # Re-run FileASTVisitor to verify if any deprecated symbols remain
        source_lines = codemodded_code.splitlines()
        visitor = FileASTVisitor(source_lines, migration_delta.library)
        visitor.visit(cand_tree)

        remaining_deprecated = []
        for usage in visitor.usages:
            sym = usage.symbol_name.strip().lower()
            for dep in deprecated_patterns:
                if dep and (dep == sym or dep in sym or sym in dep):
                    remaining_deprecated.append(usage.symbol_name)
                    break

        # Check for lingering deprecated syntax strings
        for rule in migration_delta.rules:
            if rule.old_syntax and rule.old_syntax in codemodded_code:
                remaining_deprecated.append(rule.old_syntax)

        if not remaining_deprecated:
            # Completely resolved deterministically! Zero prompt tokens!
            start_t = time.perf_counter()
            est_tokens = len(original_code) // 4
            hypothetical_cost = round(
                ((est_tokens / 1_000_000) * PRICE_PER_1M_ULTRA_INPUT)
                + ((est_tokens / 1_000_000) * PRICE_PER_1M_ULTRA_OUTPUT),
                6,
            )
            telemetry = TelemetryEvent(
                file_path=file_rep.relative_path,
                assigned_tier=file_rep.assigned_tier,
                model_used="LIBCST_DETERMINISTIC_CODEMOD",
                complexity_score=file_rep.normalized_score,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.5,
                actual_cost_usd=0.0,
                hypothetical_ultra_cost_usd=hypothetical_cost,
                cost_saved_usd=hypothetical_cost,
                execution_mode="LIBCST_DETERMINISTIC_CODEMOD",
            )
            patch_res = FilePatchResult(
                relative_path=file_rep.relative_path,
                original_code=original_code,
                patched_code=codemodded_code,
                unified_diff=_compute_unified_diff(original_code, codemodded_code, file_rep.relative_path),
                assigned_tier=file_rep.assigned_tier,
                model_used="LIBCST_DETERMINISTIC_CODEMOD",
                syntax_valid=True,
                telemetry=telemetry,
                status="SUCCESS",
            )
            resolved_patches.append(patch_res)
            print(f"  ⚡ [LibCST Codemod] Deterministically migrated {file_rep.relative_path} (0 LLM tokens used!)", flush=True)
        else:
            # Residual file: keep codemodded code as source to help LLM, and send to Nemotron
            file_rep.source_code = codemodded_code
            residual_files.append(file_rep)
            print(f"  🔄 [LibCST Codemod] Applied mechanical renames to {file_rep.relative_path}; {len(remaining_deprecated)} residual symbols sent to Nemotron", flush=True)

    return resolved_patches, residual_files
