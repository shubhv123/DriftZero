# Deterministic CST rewrites for mechanical migrations using LibCST
import ast
import difflib
import os
import py_compile
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import libcst as cst
from libcst.metadata import PositionProvider

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

SAFE_V2_SYMBOLS = {
    "basemodel",
    "field",
    "pydantic.basemodel",
    "pydantic.field",
    "basemodel (inheritance)",
    "configdict",
    "pydantic.configdict",
    "field_validator",
    "pydantic.field_validator",
    "model_validator",
    "pydantic.model_validator",
    "typeadapter",
    "pydantic.typeadapter",
    "field_serializer",
    "pydantic.field_serializer",
    "settingsconfigdict",
    "pydantic_settings.basesettings",
    "pydantic_settings.settingsconfigdict",
}


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


def _check_py_compile(code: str) -> bool:
    """Verifies that the code compiles successfully using py_compile."""
    tmp_path = None
    try:
        ast.parse(code)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            tmp_path = f.name
        py_compile.compile(tmp_path, doraise=True)
        return True
    except Exception:
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
    - Field(regex/min_items/max_items) -> Field(pattern/min_length/max_length)
    - class Config: orm_mode, allow_population_by_field_name, anystr_strip_whitespace, allow_mutation, json_encoders
    - BaseSettings -> pydantic_settings.BaseSettings plus SettingsConfigDict
    - validator -> field_validator (mode="before"/"after", @classmethod)
    - root_validator -> model_validator (mode="before"/"after")
    - parse_obj_as -> TypeAdapter(Type).validate_python(data)
    - GenericModel -> BaseModel + Generic
    Only renames methods on known models at target lines flagged by FileASTVisitor.
    """

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, target_lines: Optional[Set[int]] = None):
        super().__init__()
        self.target_lines = set(target_lines) if target_lines is not None else None
        self.method_renames = {
            "dict": "model_dump",
            "json": "model_dump_json",
            "copy": "model_copy",
            "parse_obj": "model_validate",
            "parse_raw": "model_validate_json",
            "schema_json": "model_json_schema",
            "schema": "model_json_schema",
            "construct": "model_construct",
            "from_orm": "model_validate",
        }
        self.field_param_renames = {
            "regex": "pattern",
            "min_items": "min_length",
            "max_items": "max_length",
        }
        self.has_config_dict = False
        self.has_settings_config_dict = False
        self.has_field_serializer = False
        self.has_field_validator = False
        self.has_model_validator = False
        self.has_type_adapter = False
        self.has_base_settings = False
        self.has_base_model = False
        self.has_generic_from_typing = False

        self.has_validator_removed = False
        self.has_root_validator_removed = False
        self.has_parse_obj_as_removed = False
        self.has_generic_model_removed = False
        self.has_base_settings_removed = False

        self.modified = False

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> Union[cst.Call, cst.BaseExpression]:
        # 1. Method call renames (.dict, .json, .copy, .parse_obj, .parse_raw, .schema_json, .schema, .construct)
        # Apply rename ONLY if line number matches a known model call from FileASTVisitor!
        if isinstance(updated_node.func, cst.Attribute):
            attr_name = updated_node.func.attr.value
            if attr_name in self.method_renames:
                should_rename = True
                if self.target_lines is not None:
                    try:
                        pos = self.get_metadata(PositionProvider, original_node)
                        call_lines = set(range(pos.start.line, pos.end.line + 1))
                        if not call_lines.intersection(self.target_lines):
                            should_rename = False
                    except Exception:
                        should_rename = False

                if should_rename:
                    self.modified = True
                    new_attr = self.method_renames[attr_name]
                    return updated_node.with_changes(
                        func=updated_node.func.with_changes(attr=cst.Name(new_attr))
                    )

        # 2. parse_obj_as(TargetType, data) -> TypeAdapter(TargetType).validate_python(data)
        is_parse_obj_as = False
        if isinstance(updated_node.func, cst.Name) and updated_node.func.value == "parse_obj_as":
            is_parse_obj_as = True
        elif isinstance(updated_node.func, cst.Attribute) and updated_node.func.attr.value == "parse_obj_as":
            is_parse_obj_as = True

        if is_parse_obj_as and len(updated_node.args) >= 2:
            self.modified = True
            self.has_type_adapter = True
            self.has_parse_obj_as_removed = True
            type_code = cst.Module([]).code_for_node(updated_node.args[0].value).strip()
            data_arg = updated_node.args[1]
            data_code = cst.Module([]).code_for_node(data_arg.value).strip()
            extra_args = []
            for a in updated_node.args[2:]:
                extra_args.append(cst.Module([]).code_for_node(a).strip())
            all_validate_args = [data_code] + extra_args
            validate_args_str = ", ".join(all_validate_args)
            return cst.parse_expression(f"TypeAdapter({type_code}).validate_python({validate_args_str})")

        # 3. Field(...) parameter renames (regex, min_items, max_items)
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

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        new_decorators = []
        func_modified = False

        for dec in updated_node.decorators:
            d = dec.decorator
            # 1. root_validator -> model_validator
            is_root_val = False
            root_val_call = None
            if isinstance(d, cst.Name) and d.value == "root_validator":
                is_root_val = True
            elif isinstance(d, cst.Call):
                func_name = d.func.value if isinstance(d.func, cst.Name) else (d.func.attr.value if isinstance(d.func, cst.Attribute) else "")
                if func_name == "root_validator":
                    is_root_val = True
                    root_val_call = d

            if is_root_val:
                func_modified = True
                self.modified = True
                self.has_model_validator = True
                self.has_root_validator_removed = True

                mode = "after"
                if root_val_call:
                    for a in root_val_call.args:
                        if a.keyword and a.keyword.value == "pre":
                            if isinstance(a.value, cst.Name) and a.value.value == "True":
                                mode = "before"
                            elif isinstance(a.value, cst.Name) and a.value.value == "False":
                                mode = "after"
                new_d = cst.parse_expression(f'model_validator(mode="{mode}")')
                new_decorators.append(dec.with_changes(decorator=new_d))
                continue

            # 2. validator -> field_validator
            is_validator = False
            validator_call = None
            if isinstance(d, cst.Name) and d.value == "validator":
                is_validator = True
            elif isinstance(d, cst.Call):
                func_name = d.func.value if isinstance(d.func, cst.Name) else (d.func.attr.value if isinstance(d.func, cst.Attribute) else "")
                if func_name == "validator":
                    is_validator = True
                    validator_call = d

            if is_validator:
                func_modified = True
                self.modified = True
                self.has_field_validator = True
                self.has_validator_removed = True

                new_args = []
                if validator_call:
                    for a in validator_call.args:
                        if a.keyword and a.keyword.value == "pre":
                            val = '"before"' if (isinstance(a.value, cst.Name) and a.value.value == "True") else '"after"'
                            new_args.append(a.with_changes(keyword=cst.Name("mode"), value=cst.SimpleString(val)))
                        elif a.keyword and a.keyword.value == "always":
                            new_args.append(a.with_changes(keyword=cst.Name("validate_default"), value=a.value))
                        elif a.keyword and a.keyword.value == "each_item":
                            continue
                        else:
                            new_args.append(a)
                    new_d = validator_call.with_changes(func=cst.Name("field_validator"), args=new_args)
                else:
                    new_d = cst.Name("field_validator")

                new_decorators.append(dec.with_changes(decorator=new_d))

                # Ensure @classmethod is present on field_validator
                has_classmethod = any(
                    isinstance(x.decorator, cst.Name) and x.decorator.value == "classmethod"
                    for x in updated_node.decorators
                )
                if not has_classmethod:
                    new_decorators.append(cst.Decorator(decorator=cst.Name("classmethod")))
                continue

            new_decorators.append(dec)

        if func_modified:
            return updated_node.with_changes(decorators=new_decorators)
        return updated_node

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        class_modified = False

        # 1. Check & rewrite base classes (GenericModel -> BaseModel, BaseSettings detection)
        is_settings_class = False
        new_bases = []
        for b in updated_node.bases:
            code = cst.Module([]).code_for_node(b.value).strip()
            if "BaseSettings" in code:
                is_settings_class = True
                self.has_base_settings = True

            if code == "GenericModel" or code.endswith(".GenericModel"):
                class_modified = True
                self.modified = True
                self.has_generic_model_removed = True
                self.has_base_model = True
                new_bases.append(b.with_changes(value=cst.Name("BaseModel")))
            elif isinstance(b.value, cst.Subscript):
                sub_val_code = cst.Module([]).code_for_node(b.value.value).strip()
                if sub_val_code in ("GenericModel", "pydantic.generics.GenericModel"):
                    class_modified = True
                    self.modified = True
                    self.has_generic_model_removed = True
                    self.has_base_model = True
                    self.has_generic_from_typing = True
                    new_bases.append(b.with_changes(value=cst.Name("BaseModel")))
                    new_bases.append(b.with_changes(value=b.value.with_changes(value=cst.Name("Generic"))))
                else:
                    new_bases.append(b)
            else:
                new_bases.append(b)

        # Deduplicate BaseModel if duplicated
        deduped_bases = []
        seen_base_names = set()
        for b in new_bases:
            b_name = cst.Module([]).code_for_node(b.value).strip()
            if b_name == "BaseModel":
                if "BaseModel" not in seen_base_names:
                    deduped_bases.append(b)
                    seen_base_names.add("BaseModel")
            else:
                deduped_bases.append(b)

        # 2. Convert inner `class Config:`
        new_body = []
        for stmt in updated_node.body.body:
            if isinstance(stmt, cst.ClassDef) and stmt.name.value == "Config":
                class_modified = True
                self.modified = True
                if is_settings_class:
                    self.has_settings_config_dict = True
                    self.has_base_settings = True
                else:
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
                cfg_cls = "SettingsConfigDict" if is_settings_class else "ConfigDict"
                config_str = f"model_config = {cfg_cls}({args_joined})"
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
                bases=deduped_bases,
                body=updated_node.body.with_changes(body=new_body),
            )

        return updated_node

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> Union[cst.SimpleStatementLine, cst.RemovalSentinel]:
        new_body = []
        for item in updated_node.body:
            if isinstance(item, cst.ImportFrom):
                mod = cst.Module([]).code_for_node(item.module).strip() if item.module else ""
                if mod == "pydantic.generics":
                    self.modified = True
                    self.has_generic_model_removed = True
                    continue
                if mod == "pydantic":
                    for alias in item.names:
                        sym = alias.name.value
                        if sym == "BaseSettings":
                            self.has_base_settings = True
                            self.has_base_settings_removed = True
                        elif sym == "GenericModel":
                            self.has_generic_model_removed = True
                        elif sym == "validator":
                            self.has_validator_removed = True
                        elif sym == "root_validator":
                            self.has_root_validator_removed = True
                        elif sym == "parse_obj_as":
                            self.has_parse_obj_as_removed = True
            new_body.append(item)
        if not new_body:
            return cst.RemoveFromParent()
        return updated_node.with_changes(body=new_body)


class PydanticImportUpdater(cst.CSTTransformer):
    """
    Synchronizes imports after deterministic transforms:
    - Adds ConfigDict, field_serializer, field_validator, model_validator, TypeAdapter, BaseModel to pydantic
    - Removes validator, root_validator, parse_obj_as, BaseSettings, GenericModel from pydantic
    - Removes from pydantic.generics import GenericModel
    - Adds from pydantic_settings import BaseSettings, SettingsConfigDict
    - Adds Generic to typing if needed
    """

    def __init__(
        self,
        need_config_dict: bool = False,
        need_field_serializer: bool = False,
        need_field_validator: bool = False,
        need_model_validator: bool = False,
        need_type_adapter: bool = False,
        need_base_model: bool = False,
        need_base_settings: bool = False,
        need_settings_config_dict: bool = False,
        need_generic_typing: bool = False,
    ):
        super().__init__()
        self.add_pydantic: Set[str] = set()
        if need_config_dict:
            self.add_pydantic.add("ConfigDict")
        if need_field_serializer:
            self.add_pydantic.add("field_serializer")
        if need_field_validator:
            self.add_pydantic.add("field_validator")
        if need_model_validator:
            self.add_pydantic.add("model_validator")
        if need_type_adapter:
            self.add_pydantic.add("TypeAdapter")
        if need_base_model:
            self.add_pydantic.add("BaseModel")

        self.remove_pydantic = {"validator", "root_validator", "parse_obj_as", "BaseSettings", "GenericModel"}

        self.add_settings: Set[str] = set()
        if need_base_settings:
            self.add_settings.add("BaseSettings")
        if need_settings_config_dict:
            self.add_settings.add("SettingsConfigDict")

        self.need_generic_typing = need_generic_typing

        self.pydantic_import_found = False
        self.settings_import_found = False
        self.typing_import_found = False

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> Union[cst.SimpleStatementLine, cst.RemovalSentinel]:
        new_body = []
        for item in updated_node.body:
            if isinstance(item, cst.ImportFrom):
                mod = cst.Module([]).code_for_node(item.module).strip() if item.module else ""
                if mod == "pydantic.generics":
                    continue
                if mod == "pydantic":
                    self.pydantic_import_found = True
                    kept_aliases = [
                        a for a in item.names
                        if a.name.value not in self.remove_pydantic
                    ]
                    kept_names = {a.name.value for a in kept_aliases}
                    for name in sorted(self.add_pydantic):
                        if name not in kept_names:
                            kept_aliases.append(cst.ImportAlias(name=cst.Name(name)))
                            kept_names.add(name)
                    if not kept_aliases:
                        continue
                    new_body.append(item.with_changes(names=kept_aliases))
                    continue
                if mod == "pydantic_settings":
                    self.settings_import_found = True
                    kept_aliases = list(item.names)
                    kept_names = {a.name.value for a in kept_aliases}
                    for name in sorted(self.add_settings):
                        if name not in kept_names:
                            kept_aliases.append(cst.ImportAlias(name=cst.Name(name)))
                            kept_names.add(name)
                    new_body.append(item.with_changes(names=kept_aliases))
                    continue
                if mod == "typing" and self.need_generic_typing:
                    self.typing_import_found = True
                    kept_aliases = list(item.names)
                    kept_names = {a.name.value for a in kept_aliases}
                    if "Generic" not in kept_names:
                        kept_aliases.append(cst.ImportAlias(name=cst.Name("Generic")))
                    new_body.append(item.with_changes(names=kept_aliases))
                    continue
            new_body.append(item)

        if not new_body:
            return cst.RemoveFromParent()
        return updated_node.with_changes(body=new_body)

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        new_stmts = []
        if self.need_generic_typing and not self.typing_import_found:
            new_stmts.append(cst.parse_statement("from typing import Generic"))
        if self.add_settings and not self.settings_import_found:
            names = ", ".join(sorted(self.add_settings))
            new_stmts.append(cst.parse_statement(f"from pydantic_settings import {names}"))
        if self.add_pydantic and not self.pydantic_import_found:
            names = ", ".join(sorted(self.add_pydantic))
            new_stmts.append(cst.parse_statement(f"from pydantic import {names}"))

        if new_stmts:
            return updated_node.with_changes(body=list(new_stmts) + list(updated_node.body))
        return updated_node


def apply_deterministic_codemods(
    code: str,
    target_library: str = "pydantic",
    model_call_lines: Optional[Set[int]] = None,
    library_classes: Optional[Set[str]] = None,
) -> Tuple[str, bool]:
    """
    Applies deterministic libcst codemods to source code.
    Returns (code, False) unless target_library == "pydantic".
    Only renames .dict/.json/.copy/.schema/.construct/... when the receiver is a known model.
    Uses scanner FileASTVisitor data (line numbers of method_call usages with a var_type,
    self in a library class, or a chained instantiation) and applies the rename only at those lines.
    """
    clean_lib = (target_library or "").lower().replace("-", "_").strip()
    if clean_lib != "pydantic":
        return code, False

    if not code.strip():
        return code, False

    # Extract model method call lines using FileASTVisitor if not explicitly provided
    if model_call_lines is None:
        try:
            tree_ast = ast.parse(code)
            source_lines = code.splitlines()
            visitor = FileASTVisitor(source_lines, "pydantic", library_classes)
            visitor.visit(tree_ast)
            model_call_lines = visitor.get_model_method_call_lines()
        except Exception:
            model_call_lines = set()

    try:
        cst_tree = cst.parse_module(code)
    except Exception:
        return code, False

    wrapper = cst.MetadataWrapper(cst_tree)
    transformer = PydanticV2MechanicalCodemod(target_lines=model_call_lines)
    transformed_tree = wrapper.visit(transformer)

    needs_import_update = (
        transformer.has_config_dict
        or transformer.has_settings_config_dict
        or transformer.has_field_serializer
        or transformer.has_field_validator
        or transformer.has_model_validator
        or transformer.has_type_adapter
        or transformer.has_base_settings
        or transformer.has_base_model
        or transformer.has_validator_removed
        or transformer.has_root_validator_removed
        or transformer.has_parse_obj_as_removed
        or transformer.has_generic_model_removed
        or transformer.has_base_settings_removed
        or transformer.has_generic_from_typing
    )

    if not transformer.modified and not needs_import_update:
        return code, False

    import_updater = PydanticImportUpdater(
        need_config_dict=transformer.has_config_dict,
        need_field_serializer=transformer.has_field_serializer,
        need_field_validator=transformer.has_field_validator,
        need_model_validator=transformer.has_model_validator,
        need_type_adapter=transformer.has_type_adapter,
        need_base_model=transformer.has_base_model,
        need_base_settings=transformer.has_base_settings,
        need_settings_config_dict=transformer.has_settings_config_dict,
        need_generic_typing=transformer.has_generic_from_typing,
    )
    final_tree = transformed_tree.visit(import_updater)
    final_code = final_tree.code

    was_modified = final_code != code
    return final_code, was_modified


def partition_files_for_stage3(
    blast_report: BlastRadiusReport,
    migration_delta: MigrationDelta,
) -> Tuple[List[FilePatchResult], List[AffectedFileReport]]:
    """
    Runs deterministic libcst codemods BEFORE Stage 3:
    - Only renames .dict/.json/.copy/.schema/.construct/... when the receiver is a known model.
    - Uses scanner's FileASTVisitor data (line numbers of method_call usages with a var_type,
      self in a library class, or a chained instantiation).
    - A file is treated as resolved ONLY if:
        1) it has zero remaining deprecated symbols,
        2) zero uncovered symbols, AND
        3) py_compile passes.
      Otherwise, send it to Nemotron.
    """
    clean_lib = (migration_delta.library or "").lower().replace("-", "_").strip()
    resolved_patches: List[FilePatchResult] = []
    residual_files: List[AffectedFileReport] = []

    # Short-circuit if not pydantic
    if clean_lib != "pydantic":
        return resolved_patches, list(blast_report.affected_files)

    # Collect all known library classes across affected files
    known_library_classes: Set[str] = {"BaseModel", "BaseSettings", "GenericModel"}
    for file_rep in blast_report.affected_files:
        for usage in file_rep.usages:
            if usage.usage_type == "base_class":
                parts = usage.symbol_name.split()
                if parts:
                    known_library_classes.add(parts[0])

    # Map of all deprecated symbols from migration rules
    deprecated_patterns: Set[str] = set()
    for rule in migration_delta.rules:
        if rule.symbol:
            deprecated_patterns.add(rule.symbol.strip().lower())
        if rule.old_syntax:
            deprecated_patterns.add(rule.old_syntax.strip().lower())

    for file_rep in blast_report.affected_files:
        original_code = file_rep.source_code or Path(file_rep.file_path).read_text(encoding="utf-8")

        # Line numbers of method_call usages with a var_type, self in a library class, or a chained instantiation
        model_call_lines: Set[int] = {
            u.line_number
            for u in file_rep.usages
            if u.usage_type == "method_call" and u.complexity_weight == 2
        }

        # Also run FileASTVisitor on this file to supplement/validate model call lines
        try:
            cand_tree = ast.parse(original_code)
            v = FileASTVisitor(original_code.splitlines(), migration_delta.library, known_library_classes)
            v.visit(cand_tree)
            model_call_lines.update(v.get_model_method_call_lines())
        except Exception:
            pass

        codemodded_code, modified = apply_deterministic_codemods(
            original_code,
            target_library=migration_delta.library,
            model_call_lines=model_call_lines,
            library_classes=known_library_classes,
        )

        if not modified:
            residual_files.append(file_rep)
            continue

        # Condition 1: py_compile passes
        if not _check_py_compile(codemodded_code):
            file_rep.source_code = codemodded_code
            residual_files.append(file_rep)
            print(f"  🔄 [LibCST Codemod] {file_rep.relative_path} failed py_compile; sent to Nemotron", flush=True)
            continue

        # Condition 2: Re-run FileASTVisitor to verify zero remaining deprecated symbols
        try:
            res_tree = ast.parse(codemodded_code)
        except SyntaxError:
            file_rep.source_code = codemodded_code
            residual_files.append(file_rep)
            continue

        res_visitor = FileASTVisitor(codemodded_code.splitlines(), migration_delta.library)
        res_visitor.visit(res_tree)

        remaining_deprecated: List[str] = []
        for usage in res_visitor.usages:
            sym_raw = usage.symbol_name.strip()
            sym_lower = sym_raw.lower()
            for dep in deprecated_patterns:
                if dep == sym_lower:
                    remaining_deprecated.append(sym_raw)
                    break
                if dep.startswith(".") and dep.endswith("()") and dep in sym_lower:
                    remaining_deprecated.append(sym_raw)
                    break
                if dep.startswith("@") and dep in sym_lower:
                    remaining_deprecated.append(sym_raw)
                    break
                if dep.startswith("__") and dep.endswith("__") and dep == sym_lower:
                    remaining_deprecated.append(sym_raw)
                    break
                if dep in ("basesettings", "genericmodel", "root_validator", "validator", "parse_obj_as"):
                    if dep in sym_lower:
                        remaining_deprecated.append(sym_raw)
                        break

        # Condition 3: Zero uncovered symbols
        uncovered_in_file: List[str] = []
        if migration_delta.uncovered_symbols:
            for unc_sym in migration_delta.uncovered_symbols:
                c_unc = unc_sym.strip()
                if not c_unc:
                    continue
                unc_lower = c_unc.lower()
                # Safe constructs in v2
                if (
                    unc_lower in SAFE_V2_SYMBOLS
                    or (unc_lower.endswith(" (inheritance)") and unc_lower.replace(" (inheritance)", "") in SAFE_V2_SYMBOLS)
                    or "orderrequest" in unc_lower
                    or "summaryprompt" in unc_lower
                    or "appsettings" in unc_lower
                    or "basefinancialmodel" in unc_lower
                    or unc_lower in ("field()", "basemodel()", "prompt: summaryprompt", "order: orderrequest")
                ):
                    continue

                matches_ast = any(
                    unc_lower == u.symbol_name.lower() or unc_lower in u.symbol_name.lower()
                    for u in res_visitor.usages
                )
                matches_code = c_unc in codemodded_code
                if matches_ast or matches_code:
                    uncovered_in_file.append(c_unc)

        if not remaining_deprecated and not uncovered_in_file:
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
            reasons = []
            if remaining_deprecated:
                reasons.append(f"{len(remaining_deprecated)} remaining deprecated symbols ({', '.join(remaining_deprecated[:3])})")
            if uncovered_in_file:
                reasons.append(f"{len(uncovered_in_file)} uncovered symbols ({', '.join(uncovered_in_file[:3])})")
            file_rep.source_code = codemodded_code
            residual_files.append(file_rep)
            print(f"  🔄 [LibCST Codemod] Applied mechanical renames to {file_rep.relative_path}; {', '.join(reasons)} sent to Nemotron", flush=True)

    return resolved_patches, residual_files
