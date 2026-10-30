# Deterministic CST rewrites for mechanical migrations using LibCST
import ast
import difflib
import os
import py_compile
import re
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

SAFE_BUILTIN_TYPES = {
    "str", "int", "float", "bool", "dict", "list", "set", "tuple", "any", "none", "decimal", "datetime", "bytes"
}


def _is_safe_uncovered_symbol(
    unc_sym: str,
    known_classes: Set[str],
    res_visitor: Optional[FileASTVisitor] = None,
) -> bool:
    c_unc = unc_sym.strip()
    if not c_unc:
        return True
    unc_lower = c_unc.lower()

    # Rule 1: Safe library constructs (strip inheritance and calls)
    base_sym = unc_lower.replace(" (inheritance)", "").replace("(inheritance)", "").rstrip("()")
    if unc_lower in SAFE_V2_SYMBOLS or base_sym in SAFE_V2_SYMBOLS:
        return True

    # Rule 2: User-defined model classes from known_library_classes
    known_lower = {k.lower() for k in known_classes}
    if unc_lower in known_lower or base_sym in known_lower:
        return True

    # Rule 3: Typed parameters / annotations
    if ":" in unc_lower:
        type_part = unc_lower.split(":", 1)[1].strip()
        clean_type = type_part.replace("[", " ").replace("]", " ").split()[-1] if type_part else ""
        if clean_type in known_lower or clean_type in SAFE_V2_SYMBOLS or clean_type in SAFE_BUILTIN_TYPES:
            return True

    if res_visitor:
        if any(u.usage_type == "typed_parameter" and (u.symbol_name.lower() == unc_lower or unc_lower in u.symbol_name.lower()) for u in res_visitor.usages):
            return True

    # Rule 4: User-defined methods on model classes
    if c_unc.startswith(".") and c_unc.endswith("()"):
        method_name = c_unc[1:-2].strip()
        if method_name and res_visitor and any(f"def {method_name}(" in line for line in res_visitor.source_lines):
            return True

    # Rule 5: Variable assignment / instantiation of known library class (e.g. settings = AppSettings())
    if "=" in unc_lower:
        rhs = unc_lower.split("=")[-1].strip().rstrip("()")
        rhs_sym = rhs.split()[-1] if rhs else ""
        if rhs in known_lower or rhs_sym in known_lower or rhs in SAFE_V2_SYMBOLS or rhs_sym in SAFE_V2_SYMBOLS:
            return True

    return False


class ValuesToValidationInfoTransformer(cst.CSTTransformer):
    """
    Rewrites references to the legacy validator `values` parameter to `info.data`.
    E.g. values.get(x) -> info.data.get(x), values[x] -> info.data[x].
    """
    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.Attribute:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.parse_expression("info.data"))
        return updated_node

    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.Subscript:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.parse_expression("info.data"))
        return updated_node

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        new_targets = []
        for t in updated_node.targets:
            target = t.target
            if isinstance(target, cst.Subscript) and isinstance(target.value, cst.Name) and target.value.value == "values":
                new_targets.append(t.with_changes(target=target.with_changes(value=cst.parse_expression("info.data"))))
            else:
                new_targets.append(t)
        return updated_node.with_changes(targets=new_targets)

    def leave_Comparison(self, original_node: cst.Comparison, updated_node: cst.Comparison) -> cst.Comparison:
        new_left = updated_node.left
        if isinstance(new_left, cst.Name) and new_left.value == "values":
            new_left = cst.parse_expression("info.data")
        new_comps = []
        for comp in updated_node.comparisons:
            if isinstance(comp.comparator, cst.Name) and comp.comparator.value == "values":
                new_comps.append(comp.with_changes(comparator=cst.parse_expression("info.data")))
            else:
                new_comps.append(comp)
        return updated_node.with_changes(left=new_left, comparisons=new_comps)

    def leave_Arg(self, original_node: cst.Arg, updated_node: cst.Arg) -> cst.Arg:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.parse_expression("info.data"))
        return updated_node

    def leave_Return(self, original_node: cst.Return, updated_node: cst.Return) -> cst.Return:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.parse_expression("info.data"))
        return updated_node

    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> cst.If:
        if isinstance(updated_node.test, cst.Name) and updated_node.test.value == "values":
            return updated_node.with_changes(test=cst.parse_expression("info.data"))
        return updated_node

    def leave_For(self, original_node: cst.For, updated_node: cst.For) -> cst.For:
        if isinstance(updated_node.iter, cst.Name) and updated_node.iter.value == "values":
            return updated_node.with_changes(iter=cst.parse_expression("info.data"))
        return updated_node


class RootValidatorBeforeTransformer(cst.CSTTransformer):
    """
    Rewrites references from `values` to `data` in mode='before' model validators.
    """
    def leave_Return(self, original_node: cst.Return, updated_node: cst.Return) -> cst.Return:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.Name("data"))
        return updated_node

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.Attribute:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.Name("data"))
        return updated_node

    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.Subscript:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.Name("data"))
        return updated_node

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        new_targets = []
        for t in updated_node.targets:
            target = t.target
            if isinstance(target, cst.Subscript):
                if isinstance(target.value, cst.Name) and target.value.value == "values":
                    new_targets.append(t.with_changes(target=target.with_changes(value=cst.Name("data"))))
                    continue
            new_targets.append(t)
        return updated_node.with_changes(targets=new_targets)

    def leave_Comparison(self, original_node: cst.Comparison, updated_node: cst.Comparison) -> cst.Comparison:
        new_left = updated_node.left
        if isinstance(new_left, cst.Name) and new_left.value == "values":
            new_left = cst.Name("data")
        new_comps = []
        for comp in updated_node.comparisons:
            if isinstance(comp.comparator, cst.Name) and comp.comparator.value == "values":
                new_comps.append(comp.with_changes(comparator=cst.Name("data")))
            else:
                new_comps.append(comp)
        return updated_node.with_changes(left=new_left, comparisons=new_comps)

    def leave_Arg(self, original_node: cst.Arg, updated_node: cst.Arg) -> cst.Arg:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.Name("data"))
        return updated_node

    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> cst.If:
        if isinstance(updated_node.test, cst.Name) and updated_node.test.value == "values":
            return updated_node.with_changes(test=cst.Name("data"))
        return updated_node

    def leave_For(self, original_node: cst.For, updated_node: cst.For) -> cst.For:
        if isinstance(updated_node.iter, cst.Name) and updated_node.iter.value == "values":
            return updated_node.with_changes(iter=cst.Name("data"))
        return updated_node


class RootValidatorAfterTransformer(cst.CSTTransformer):
    """
    Rewrites references in mode='after' model validators to instance attributes on self.
    """
    def leave_Return(self, original_node: cst.Return, updated_node: cst.Return) -> cst.Return:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.Name("self"))
        return updated_node

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> Union[cst.Call, cst.BaseExpression]:
        if isinstance(updated_node.func, cst.Attribute):
            if isinstance(updated_node.func.value, cst.Name) and updated_node.func.value.value == "values":
                if updated_node.func.attr.value == "get":
                    args = list(updated_node.args)
                    if len(args) == 1:
                        args.append(cst.Arg(value=cst.Name("None")))
                    call_args = [cst.Arg(value=cst.Name("self"))] + args
                    return cst.Call(func=cst.Name("getattr"), args=call_args)
        return updated_node

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        new_targets = []
        for t in updated_node.targets:
            target = t.target
            if isinstance(target, cst.Subscript):
                if isinstance(target.value, cst.Name) and target.value.value == "values":
                    if len(target.slice) == 1 and isinstance(target.slice[0].slice, cst.Index):
                        idx_val = target.slice[0].slice.value
                        if isinstance(idx_val, cst.SimpleString):
                            field_name = idx_val.evaluated_value
                            if field_name.isidentifier():
                                new_target = cst.Attribute(value=cst.Name("self"), attr=cst.Name(field_name))
                                new_targets.append(t.with_changes(target=new_target))
                                continue
            new_targets.append(t)
        return updated_node.with_changes(targets=new_targets)

    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.BaseExpression:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            if len(updated_node.slice) == 1 and isinstance(updated_node.slice[0].slice, cst.Index):
                idx_val = updated_node.slice[0].slice.value
                if isinstance(idx_val, cst.SimpleString):
                    field_name = idx_val.evaluated_value
                    if field_name.isidentifier():
                        return cst.Attribute(value=cst.Name("self"), attr=cst.Name(field_name))
                else:
                    return cst.Call(
                        func=cst.Name("getattr"),
                        args=[cst.Arg(value=cst.Name("self")), cst.Arg(value=idx_val)],
                    )
        return updated_node

    def leave_Comparison(self, original_node: cst.Comparison, updated_node: cst.Comparison) -> Union[cst.Comparison, cst.Call]:
        left = updated_node.left
        for comp in updated_node.comparisons:
            if isinstance(comp.operator, cst.In):
                if isinstance(comp.comparator, cst.Name) and comp.comparator.value == "values":
                    return cst.Call(
                        func=cst.Name("hasattr"),
                        args=[cst.Arg(value=cst.Name("self")), cst.Arg(value=left)],
                    )
        return updated_node

    def leave_Arg(self, original_node: cst.Arg, updated_node: cst.Arg) -> cst.Arg:
        if isinstance(updated_node.value, cst.Name) and updated_node.value.value == "values":
            return updated_node.with_changes(value=cst.Name("self"))
        return updated_node

    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> cst.If:
        if isinstance(updated_node.test, cst.Name) and updated_node.test.value == "values":
            return updated_node.with_changes(test=cst.Name("self"))
        return updated_node


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
            "schema": "model_json_schema",
            "construct": "model_construct",
            "from_orm": "model_validate",
        }
        self.field_param_renames = {
            "regex": "pattern",
            "min_items": "min_length",
            "max_items": "max_length",
            "env": "validation_alias",
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
        self.has_field = False
        self.has_validation_info = False
        self.has_json_import = False
        self.has_self_import = False

        self.has_validator_removed = False
        self.has_root_validator_removed = False
        self.has_parse_obj_as_removed = False
        self.has_generic_model_removed = False
        self.has_base_settings_removed = False

        self.can_resolve = True
        self.class_always_fields_stack: List[Optional[Set[str]]] = []

        self.modified = False

    def visit_ClassDef(self, node: cst.ClassDef) -> Optional[bool]:
        if node.name.value == "Config":
            self.class_always_fields_stack.append(None)
        else:
            self.class_always_fields_stack.append(set())
        return True

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> Union[cst.Call, cst.BaseExpression]:
        # 1. Method call renames (.dict, .json, .copy, .parse_obj, .parse_raw, .schema_json, .schema, .construct)
        # Apply rename ONLY if line number matches a known model call from FileASTVisitor!
        if isinstance(updated_node.func, cst.Attribute):
            attr_name = updated_node.func.attr.value
            if attr_name == "schema_json":
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
                    self.has_json_import = True
                    receiver = updated_node.func.value
                    model_json_schema_call = cst.Call(
                        func=cst.Attribute(value=receiver, attr=cst.Name("model_json_schema")),
                        args=[],
                    )
                    dumps_args = [cst.Arg(value=model_json_schema_call)] + list(updated_node.args)
                    return cst.Call(
                        func=cst.Attribute(value=cst.Name("json"), attr=cst.Name("dumps")),
                        args=dumps_args,
                    )
            elif attr_name in self.method_renames:
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
        is_root_val = False
        root_val_call = None
        root_val_mode = "after"

        is_validator = False
        validator_call = None

        for dec in updated_node.decorators:
            d = dec.decorator
            func_name = ""
            call_node = None
            if isinstance(d, cst.Name):
                func_name = d.value
            elif isinstance(d, cst.Call):
                call_node = d
                if isinstance(d.func, cst.Name):
                    func_name = d.func.value
                elif isinstance(d.func, cst.Attribute):
                    func_name = d.func.attr.value

            if func_name == "root_validator":
                is_root_val = True
                root_val_call = call_node
                if call_node:
                    for a in call_node.args:
                        if a.keyword and a.keyword.value == "pre" and isinstance(a.value, cst.Name) and a.value.value == "True":
                            root_val_mode = "before"
            elif func_name == "validator":
                is_validator = True
                validator_call = call_node

        if is_root_val:
            self.modified = True
            self.has_model_validator = True
            self.has_root_validator_removed = True

            new_decorators = []
            for dec in updated_node.decorators:
                d = dec.decorator
                func_name = ""
                if isinstance(d, cst.Name):
                    func_name = d.value
                elif isinstance(d, cst.Call):
                    func_name = d.func.value if isinstance(d.func, cst.Name) else (d.func.attr.value if isinstance(d.func, cst.Attribute) else "")

                if func_name == "root_validator":
                    new_dec = dec.with_changes(decorator=cst.parse_expression(f'model_validator(mode="{root_val_mode}")'))
                    new_decorators.append(new_dec)
                elif func_name == "classmethod" and root_val_mode == "after":
                    # Convert to instance method: drop @classmethod
                    continue
                else:
                    new_decorators.append(dec)

            if root_val_mode == "before":
                has_classmethod = any(
                    isinstance(x.decorator, cst.Name) and x.decorator.value == "classmethod"
                    for x in new_decorators
                )
                if not has_classmethod:
                    new_decorators.append(cst.Decorator(decorator=cst.Name("classmethod")))

                new_params = []
                for p in updated_node.params.params:
                    if p.name.value == "cls":
                        new_params.append(p)
                    elif p.name.value in ("values", "data"):
                        new_params.append(p.with_changes(name=cst.Name("data")))
                    else:
                        new_params.append(p)

                updated_node = updated_node.with_changes(
                    decorators=new_decorators,
                    params=updated_node.params.with_changes(params=new_params),
                )
                return updated_node.visit(RootValidatorBeforeTransformer())
            else:
                self.has_self_import = True
                ret_anno = updated_node.returns
                if ret_anno is not None:
                    ret_anno = cst.Annotation(annotation=cst.Name("Self"))
                updated_node = updated_node.with_changes(
                    decorators=new_decorators,
                    params=cst.Parameters(params=[cst.Param(name=cst.Name("self"))]),
                    returns=ret_anno,
                )
                return updated_node.visit(RootValidatorAfterTransformer())

        if is_validator:
            self.modified = True
            self.has_field_validator = True
            self.has_validator_removed = True

            has_each_item = False
            has_always = False
            target_field_names = []

            if validator_call:
                for a in validator_call.args:
                    if a.keyword and a.keyword.value == "each_item":
                        has_each_item = True
                    elif a.keyword and a.keyword.value == "always":
                        if isinstance(a.value, cst.Name) and a.value.value == "True":
                            has_always = True
                    elif a.keyword is None:
                        if isinstance(a.value, cst.SimpleString):
                            target_field_names.append(a.value.evaluated_value)

            # Never pass validate_default or each_item to field_validator: if each_item is present, leave the file residual.
            if has_each_item:
                self.can_resolve = False

            if has_always:
                if self.class_always_fields_stack and self.class_always_fields_stack[-1] is not None:
                    self.class_always_fields_stack[-1].update(target_field_names)
                else:
                    self.can_resolve = False

            new_args = []
            if validator_call:
                for a in validator_call.args:
                    if a.keyword and a.keyword.value == "pre":
                        val = '"before"' if (isinstance(a.value, cst.Name) and a.value.value == "True") else '"after"'
                        new_args.append(a.with_changes(keyword=cst.Name("mode"), value=cst.SimpleString(val)))
                    elif a.keyword and a.keyword.value in ("always", "validate_default", "each_item", "check_fields"):
                        continue
                    else:
                        new_args.append(a)
                if new_args:
                    new_args[-1] = new_args[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
                new_d = validator_call.with_changes(func=cst.Name("field_validator"), args=new_args)
            else:
                new_d = cst.Name("field_validator")

            new_decorators = []
            for dec in updated_node.decorators:
                d = dec.decorator
                func_name = ""
                if isinstance(d, cst.Name):
                    func_name = d.value
                elif isinstance(d, cst.Call):
                    func_name = d.func.value if isinstance(d.func, cst.Name) else (d.func.attr.value if isinstance(d.func, cst.Attribute) else "")
                if func_name == "validator":
                    new_decorators.append(dec.with_changes(decorator=new_d))
                else:
                    new_decorators.append(dec)

            has_classmethod = any(
                isinstance(x.decorator, cst.Name) and x.decorator.value == "classmethod"
                for x in new_decorators
            )
            if not has_classmethod:
                new_decorators.append(cst.Decorator(decorator=cst.Name("classmethod")))

            # Rewrite values/ValidationInfo signatures (values -> info: ValidationInfo and values.get(x) -> info.data.get(x))
            new_params = []
            has_values_param = False
            for p in updated_node.params.params:
                if p.name.value == "values":
                    has_values_param = True
                    self.has_validation_info = True
                    new_params.append(
                        p.with_changes(
                            name=cst.Name("info"),
                            annotation=cst.Annotation(annotation=cst.Name("ValidationInfo")),
                            default=None,
                        )
                    )
                elif p.name.value == "info":
                    has_values_param = True
                    self.has_validation_info = True
                    new_params.append(
                        p.with_changes(
                            annotation=cst.Annotation(annotation=cst.Name("ValidationInfo")),
                        )
                    )
                else:
                    new_params.append(p)

            updated_node = updated_node.with_changes(
                decorators=new_decorators,
                params=updated_node.params.with_changes(params=new_params),
            )
            if has_values_param:
                updated_node = updated_node.visit(ValuesToValidationInfoTransformer())

            return updated_node

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

                if is_settings_class:
                    if not any("populate_by_name" in a for a in dict_args):
                        dict_args.append("populate_by_name=True")
                    if not any("extra" in a for a in dict_args):
                        dict_args.append('extra="ignore"')

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

        has_config_class = any(isinstance(stmt, cst.ClassDef) and stmt.name.value == "Config" for stmt in updated_node.body.body)
        if is_settings_class and not has_config_class:
            self.has_settings_config_dict = True
            self.has_base_settings = True
            class_modified = True
            self.modified = True
            new_body.append(cst.parse_statement('model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")'))

        # 3. Apply validate_default=True to matching fields if always=True validators were present
        always_fields = self.class_always_fields_stack.pop() if self.class_always_fields_stack else None
        if always_fields:
            found_fields = set()
            new_body_with_fields = []
            for stmt in new_body:
                if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1 and isinstance(stmt.body[0], cst.AnnAssign):
                    ann = stmt.body[0]
                    if isinstance(ann.target, cst.Name) and ann.target.value in always_fields:
                        field_name = ann.target.value
                        found_fields.add(field_name)
                        self.has_field = True
                        val = ann.value
                        if val is None:
                            new_val = cst.parse_expression("Field(..., validate_default=True)")
                        elif (
                            isinstance(val, cst.Call)
                            and (
                                (isinstance(val.func, cst.Name) and val.func.value == "Field")
                                or (isinstance(val.func, cst.Attribute) and val.func.attr.value == "Field")
                            )
                        ):
                            has_val_def = any(a.keyword and a.keyword.value == "validate_default" for a in val.args)
                            if not has_val_def:
                                new_args = list(val.args) + [cst.Arg(keyword=cst.Name("validate_default"), value=cst.Name("True"))]
                                new_val = val.with_changes(args=new_args)
                            else:
                                new_val = val
                        else:
                            new_val = cst.Call(
                                func=cst.Name("Field"),
                                args=[
                                    cst.Arg(keyword=cst.Name("default"), value=val),
                                    cst.Arg(keyword=cst.Name("validate_default"), value=cst.Name("True")),
                                ],
                            )
                        new_body_with_fields.append(stmt.with_changes(body=[ann.with_changes(value=new_val)]))
                        class_modified = True
                        self.modified = True
                        continue
                new_body_with_fields.append(stmt)
            new_body = new_body_with_fields

            if always_fields - found_fields:
                self.can_resolve = False

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
    - Adds ConfigDict, field_serializer, field_validator, model_validator, TypeAdapter, BaseModel, Field, ValidationInfo to pydantic
    - Removes validator, root_validator, parse_obj_as, BaseSettings, GenericModel from pydantic
    - Removes from pydantic.generics import GenericModel
    - Adds from pydantic_settings import BaseSettings, SettingsConfigDict
    - Adds Generic, Self to typing if needed
    - Adds import json if needed
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
        need_field: bool = False,
        need_validation_info: bool = False,
        need_json: bool = False,
        need_self: bool = False,
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
        if need_field:
            self.add_pydantic.add("Field")
        if need_validation_info:
            self.add_pydantic.add("ValidationInfo")

        self.remove_pydantic = {"validator", "root_validator", "parse_obj_as", "BaseSettings", "GenericModel"}

        self.add_settings: Set[str] = set()
        if need_base_settings:
            self.add_settings.add("BaseSettings")
        if need_settings_config_dict:
            self.add_settings.add("SettingsConfigDict")

        self.need_generic_typing = need_generic_typing
        self.need_self = need_self
        self.need_json = need_json

        self.pydantic_import_found = False
        self.settings_import_found = False
        self.typing_import_found = False
        self.json_import_found = False

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> Union[cst.SimpleStatementLine, cst.RemovalSentinel]:
        new_body = []
        for item in updated_node.body:
            if isinstance(item, cst.Import):
                for alias in item.names:
                    if alias.name.value == "json":
                        self.json_import_found = True
            elif isinstance(item, cst.ImportFrom):
                mod = cst.Module([]).code_for_node(item.module).strip() if item.module else ""
                if mod == "json":
                    self.json_import_found = True
                elif mod == "pydantic.generics":
                    continue
                elif mod == "pydantic":
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
                elif mod == "pydantic_settings":
                    self.settings_import_found = True
                    kept_aliases = list(item.names)
                    kept_names = {a.name.value for a in kept_aliases}
                    for name in sorted(self.add_settings):
                        if name not in kept_names:
                            kept_aliases.append(cst.ImportAlias(name=cst.Name(name)))
                            kept_names.add(name)
                    new_body.append(item.with_changes(names=kept_aliases))
                    continue
                elif mod == "typing":
                    self.typing_import_found = True
                    kept_aliases = list(item.names)
                    kept_names = {a.name.value for a in kept_aliases}
                    if self.need_generic_typing and "Generic" not in kept_names:
                        kept_aliases.append(cst.ImportAlias(name=cst.Name("Generic")))
                        kept_names.add("Generic")
                    new_body.append(item.with_changes(names=kept_aliases))
                    continue
            new_body.append(item)

        if not new_body:
            return cst.RemoveFromParent()
        return updated_node.with_changes(body=new_body)

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        new_stmts = []
        if self.need_json and not self.json_import_found:
            new_stmts.append(cst.parse_statement("import json"))

        typing_add = []
        if self.need_generic_typing and not self.typing_import_found:
            typing_add.append("Generic")
        if typing_add:
            names = ", ".join(sorted(typing_add))
            new_stmts.append(cst.parse_statement(f"from typing import {names}"))
        if self.need_self:
            # typing.Self needs Python 3.11+; typing_extensions (a pydantic v2 dependency) works on 3.8+
            new_stmts.append(cst.parse_statement("from typing_extensions import Self"))

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
    return_can_resolve: bool = False,
) -> Union[Tuple[str, bool], Tuple[str, bool, bool]]:
    """
    Applies deterministic libcst codemods to source code.
    Returns (code, False) unless target_library == "pydantic".
    Only renames .dict/.json/.copy/.schema/.construct/... when the receiver is a known model.
    Uses scanner FileASTVisitor data (line numbers of method_call usages with a var_type,
    self in a library class, or a chained instantiation) and applies the rename only at those lines.
    """
    clean_lib = (target_library or "").lower().replace("-", "_").strip()
    if clean_lib != "pydantic":
        return (code, False, False) if return_can_resolve else (code, False)

    if not code.strip():
        return (code, False, True) if return_can_resolve else (code, False)

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
        return (code, False, False) if return_can_resolve else (code, False)

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
        or transformer.has_field
        or transformer.has_validation_info
        or transformer.has_json_import
        or transformer.has_self_import
    )

    if not transformer.modified and not needs_import_update:
        return (code, False, transformer.can_resolve) if return_can_resolve else (code, False)

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
        need_field=transformer.has_field,
        need_validation_info=transformer.has_validation_info,
        need_json=transformer.has_json_import,
        need_self=transformer.has_self_import,
    )
    final_tree = transformed_tree.visit(import_updater)
    final_code = final_tree.code

    was_modified = final_code != code
    if return_can_resolve:
        return final_code, was_modified, transformer.can_resolve
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
                if usage.deprecation_hint:
                    m = re.search(r"Class `(\w+)` inherits", usage.deprecation_hint)
                    if m:
                        known_library_classes.add(m.group(1))

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

        codemodded_code, modified, can_resolve = apply_deterministic_codemods(
            original_code,
            target_library=migration_delta.library,
            model_call_lines=model_call_lines,
            library_classes=known_library_classes,
            return_can_resolve=True,
        )

        if not modified or not can_resolve:
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

        res_visitor = FileASTVisitor(codemodded_code.splitlines(), migration_delta.library, known_library_classes)
        res_visitor.visit(res_tree)

        remaining_deprecated: List[str] = []
        for usage in res_visitor.usages:
            sym_raw = usage.symbol_name.strip()
            sym_lower = sym_raw.lower()
            base_sym = sym_lower.replace(" (inheritance)", "").replace("(inheritance)", "").rstrip("()")
            # Safe symbols and user-defined model classes are not deprecated
            if sym_lower in SAFE_V2_SYMBOLS or base_sym in SAFE_V2_SYMBOLS:
                continue
            if base_sym in {k.lower() for k in known_library_classes} and base_sym not in ("basesettings", "genericmodel"):
                continue

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
                    if dep == "basesettings" and "pydantic_settings" in codemodded_code:
                        continue
                    if dep in sym_lower:
                        remaining_deprecated.append(sym_raw)
                        break

        # Condition 2b: v1 `.construct()` keeps unknown keys; v2 `.model_construct()` drops them
        # unless extra="allow", so this is a behavior change that needs review, not a pure rename.
        if re.search(r"\.construct\s*\(", original_code):
            remaining_deprecated.append(".construct() (extra-key behavior differs in v2)")

        # Condition 3: Zero uncovered symbols
        uncovered_in_file: List[str] = []
        if migration_delta.uncovered_symbols:
            for unc_sym in migration_delta.uncovered_symbols:
                c_unc = unc_sym.strip()
                if not c_unc:
                    continue
                # Symbol-kind rules: safe constructs, user-defined models, typed parameters
                if _is_safe_uncovered_symbol(c_unc, known_library_classes, res_visitor):
                    continue

                unc_lower = c_unc.lower()
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
