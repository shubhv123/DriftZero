import ast
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from backend.schemas.migration_models import (
    AffectedFileReport,
    BlastRadiusReport,
    SymbolUsage,
)
from backend.core.complexity_scorer import (
    calculate_cost_savings,
    score_and_route_file,
)
from backend.core.repo_manager import detect_installed_library_version


def _extract_type_names(annotation_node: Optional[ast.AST]) -> Set[str]:
    """Extracts identifier names from an AST type annotation node, including forward references."""
    if annotation_node is None:
        return set()
    names = set()
    for n in ast.walk(annotation_node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            words = re.findall(r"\b[A-Za-z_]\w*\b", n.value)
            names.update(words)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def build_cross_file_inheritance_index(
    repo_path: Path,
    target_library: str,
) -> Set[str]:
    """
    Pass 1: Builds a cross-file index of all class names that inherit
    directly or transitively from target library base classes.
    """
    clean_target_lib = target_library.lower().replace("-", "_")

    # Map of class_name -> list of base class names
    class_hierarchy: Dict[str, List[str]] = {}
    # Classes that directly inherit from a target library base
    direct_library_classes: Set[str] = set()

    # Pre-seed well-known base classes
    if clean_target_lib == "pydantic":
        direct_library_classes.update({"BaseModel", "BaseSettings", "GenericModel"})
    elif clean_target_lib == "sqlalchemy":
        direct_library_classes.update({"Base", "DeclarativeBase"})

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in ("venv", ".venv", "env", "__pycache__", "node_modules")
        ]
        for file in files:
            if not file.endswith(".py"):
                continue
            full_path = Path(root) / file
            try:
                source = full_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except Exception:
                continue

            # Identify target library imports in this file
            local_imported_bases: Set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        clean = alias.name.lower().replace("-", "_")
                        if clean == clean_target_lib or clean.startswith(f"{clean_target_lib}."):
                            sym = alias.asname or alias.name
                            local_imported_bases.add(sym)
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").lower().replace("-", "_")
                    if mod == clean_target_lib or mod.startswith(f"{clean_target_lib}."):
                        for alias in node.names:
                            sym = alias.asname or alias.name
                            local_imported_bases.add(sym)

            # Record class definitions and bases
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    base_names = []
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            base_names.append(base.id)
                            if base.id in local_imported_bases:
                                direct_library_classes.add(node.name)
                        elif isinstance(base, ast.Attribute):
                            base_attr = base.attr
                            base_repr = ast.unparse(base) if hasattr(ast, "unparse") else ""
                            base_names.append(base_attr)
                            clean_base = base_repr.lower().replace("-", "_")
                            if clean_base.startswith(f"{clean_target_lib}.") or any(s in base_repr for s in local_imported_bases):
                                direct_library_classes.add(node.name)
                        else:
                            base_repr = ast.unparse(base) if hasattr(ast, "unparse") else ""
                            if base_repr:
                                base_names.append(base_repr)
                                if clean_target_lib in base_repr.lower():
                                    direct_library_classes.add(node.name)

                    class_hierarchy[node.name] = base_names

    # Transitive closure: propagate inheritance across files
    library_classes = set(direct_library_classes)
    changed = True
    while changed:
        changed = False
        for cls_name, bases in class_hierarchy.items():
            if cls_name not in library_classes:
                if any(b in library_classes for b in bases):
                    library_classes.add(cls_name)
                    changed = True

    return library_classes


class FileASTVisitor(ast.NodeVisitor):
    """
    Universal AST visitor that maps imports and symbol usages for ANY target library.
    Builds on cross-file class hierarchy to track instances and typed parameters.
    """

    def __init__(
        self,
        source_lines: List[str],
        target_library: str,
        library_derived_classes: Optional[Set[str]] = None,
    ):
        self.source_lines = source_lines
        self.target_library = target_library.lower().replace("-", "_")
        self.library_classes: Set[str] = set(library_derived_classes or set())
        self.imports_found: List[str] = []
        self.imported_symbols: Set[str] = set()
        self.usages: List[SymbolUsage] = []
        self.touches_target_lib = False
        self.model_call_lines: Set[int] = set()

        # Scoped variable type tracking: stack of variable name -> type name mappings
        self.scope_stack: List[Dict[str, str]] = [{}]
        self.current_class: Optional[str] = None

    def _enter_scope(self) -> None:
        self.scope_stack.append({})

    def _exit_scope(self) -> None:
        if len(self.scope_stack) > 1:
            self.scope_stack.pop()

    def _set_var_type(self, var_name: str, type_name: str) -> None:
        self.scope_stack[-1][var_name] = type_name

    def _get_var_type(self, var_name: str) -> Optional[str]:
        for scope in reversed(self.scope_stack):
            if var_name in scope:
                return scope[var_name]
        return None

    def _get_call_func_name(self, func_node: ast.AST) -> Optional[str]:
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            return func_node.attr
        return None

    def _get_line_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def _is_target_module(self, mod_name: Optional[str]) -> bool:
        """Match target modules exactly or by lib. prefix."""
        if not mod_name:
            return False
        clean = mod_name.lower().replace("-", "_")
        return clean == self.target_library or clean.startswith(f"{self.target_library}.")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._is_target_module(alias.name):
                self.touches_target_lib = True
                self.imports_found.append(alias.name)
                symbol_alias = alias.asname or alias.name
                self.imported_symbols.add(symbol_alias)
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f"import {alias.name}",
                        usage_type="import",
                        line_number=node.lineno,
                        code_snippet=self._get_line_snippet(node.lineno),
                        complexity_weight=1,
                        deprecation_hint=f"Module-level import of target library `{alias.name}`",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._is_target_module(node.module):
            self.touches_target_lib = True
            for alias in node.names:
                full_import = f"{node.module}.{alias.name}" if node.module else alias.name
                self.imports_found.append(full_import)
                symbol_alias = alias.asname or alias.name
                self.imported_symbols.add(symbol_alias)

                self.usages.append(
                    SymbolUsage(
                        symbol_name=full_import,
                        usage_type="import",
                        line_number=node.lineno,
                        code_snippet=self._get_line_snippet(node.lineno),
                        complexity_weight=1,
                        deprecation_hint=f"Import `{alias.name}` from `{node.module}`",
                    )
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        prev_class = self.current_class
        is_library_derived = node.name in self.library_classes
        if is_library_derived:
            self.current_class = node.name

        # 1. Base class inheritance
        for base in node.bases:
            base_repr = ast.unparse(base) if hasattr(ast, "unparse") else ""
            matches_import = any(sym in base_repr for sym in self.imported_symbols)
            matches_lib_cls = any(c in base_repr for c in self.library_classes)
            if matches_import or matches_lib_cls or self.target_library in base_repr.lower():
                self.touches_target_lib = True
                self.library_classes.add(node.name)
                self.current_class = node.name
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f"{base_repr} (Inheritance)",
                        usage_type="base_class",
                        line_number=node.lineno,
                        code_snippet=self._get_line_snippet(node.lineno),
                        complexity_weight=4,
                        deprecation_hint=f"Class `{node.name}` inherits from library base `{base_repr}`",
                    )
                )

        # 2. Decorators on class
        for dec in node.decorator_list:
            dec_repr = ast.unparse(dec) if hasattr(ast, "unparse") else ""
            if any(sym in dec_repr for sym in self.imported_symbols) or self.target_library in dec_repr.lower():
                self.touches_target_lib = True
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f"@{dec_repr}",
                        usage_type="decorator",
                        line_number=dec.lineno,
                        code_snippet=self._get_line_snippet(dec.lineno),
                        complexity_weight=3,
                        deprecation_hint=f"Class decorator `{dec_repr}` applied to `{node.name}`",
                    )
                )

        # 3. Inner config classes or custom attributes
        for item in node.body:
            if isinstance(item, ast.ClassDef) and (item.name in ("Config", "Meta") or self.touches_target_lib):
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f"class {item.name}",
                        usage_type="config_attr",
                        line_number=item.lineno,
                        code_snippet=self._get_line_snippet(item.lineno),
                        complexity_weight=2,
                        deprecation_hint=f"Inner configuration class `{item.name}` in `{node.name}`",
                    )
                )
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                attr_name = item.target.id
                if self.touches_target_lib and attr_name.startswith("__"):
                    self.usages.append(
                        SymbolUsage(
                            symbol_name=attr_name,
                            usage_type="special_field",
                            line_number=item.lineno,
                            code_snippet=self._get_line_snippet(item.lineno),
                            complexity_weight=3,
                            deprecation_hint=f"Special model attribute `{attr_name}` in `{node.name}`",
                        )
                    )

        self.generic_visit(node)
        self.current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_scope()
        self._check_function_or_method(node)
        self._inspect_typed_parameters(node)
        self.generic_visit(node)
        self._exit_scope()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_scope()
        self._check_function_or_method(node)
        self._inspect_typed_parameters(node)
        self.generic_visit(node)
        self._exit_scope()

    def _inspect_typed_parameters(self, node: ast.AST) -> None:
        args = getattr(node, "args", None)
        if not args:
            return
        all_args = list(args.args) + list(args.kwonlyargs) + getattr(args, "posonlyargs", [])
        for arg in all_args:
            if arg.annotation:
                types = _extract_type_names(arg.annotation)
                matching = types.intersection(self.library_classes)
                if matching:
                    cls_name = sorted(matching)[0]
                    self._set_var_type(arg.arg, cls_name)
                    self.touches_target_lib = True
                    self.usages.append(
                        SymbolUsage(
                            symbol_name=f"{arg.arg}: {cls_name}",
                            usage_type="typed_parameter",
                            line_number=arg.lineno,
                            code_snippet=self._get_line_snippet(arg.lineno),
                            complexity_weight=2,
                            deprecation_hint=f"Typed parameter `{arg.arg}` using library-derived class `{cls_name}`",
                        )
                    )

    def _check_function_or_method(self, node: ast.AST) -> None:
        decorator_list = getattr(node, "decorator_list", [])
        for dec in decorator_list:
            dec_repr = ast.unparse(dec) if hasattr(ast, "unparse") else ""
            is_relevant = (
                any(sym in dec_repr for sym in self.imported_symbols)
                or self.target_library in dec_repr.lower()
                or (self.touches_target_lib and any(k in dec_repr for k in ("validator", "task", "tool", "hook")))
            )
            if is_relevant:
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f"@{dec_repr}",
                        usage_type="decorator",
                        line_number=dec.lineno,
                        code_snippet=self._get_line_snippet(dec.lineno),
                        complexity_weight=3,
                        deprecation_hint=f"Decorator `{dec_repr}` applied to function `{getattr(node, 'name', '')}`",
                    )
                )

    def visit_Assign(self, node: ast.Assign) -> None:
        # Check instance creation: user = User(...)
        if isinstance(node.value, ast.Call):
            func_name = self._get_call_func_name(node.value.func)
            if func_name and func_name in self.library_classes:
                self.touches_target_lib = True
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._set_var_type(target.id, func_name)
                        self.usages.append(
                            SymbolUsage(
                                symbol_name=f"{target.id} = {func_name}()",
                                usage_type="instance_creation",
                                line_number=node.lineno,
                                code_snippet=self._get_line_snippet(node.lineno),
                                complexity_weight=2,
                                deprecation_hint=f"Instance creation of library-derived class `{func_name}`",
                            )
                        )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Check annotated variable: user: User = ... or user: User
        if node.annotation:
            types = _extract_type_names(node.annotation)
            matching = types.intersection(self.library_classes)
            if matching and isinstance(node.target, ast.Name):
                cls_name = sorted(matching)[0]
                self._set_var_type(node.target.id, cls_name)
                self.touches_target_lib = True
        if node.value and isinstance(node.value, ast.Call):
            func_name = self._get_call_func_name(node.value.func)
            if func_name and func_name in self.library_classes:
                self.touches_target_lib = True
                if isinstance(node.target, ast.Name):
                    self._set_var_type(node.target.id, func_name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # 1. Attribute call: obj.method()
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            caller_repr = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
            caller_var = node.func.value.id if isinstance(node.func.value, ast.Name) else ""

            # Check direct library import symbol
            is_caller_imported = any(sym in caller_repr for sym in self.imported_symbols)

            # Check method call on an instance of a library class (typed parameter or assigned instance)
            var_type = self._get_var_type(caller_var) if caller_var else None

            # Check method call on self inside a library-derived class
            is_self_in_lib_class = (caller_var == "self" and self.current_class is not None)

            # Check classmethod call directly on library class, e.g. User.parse_obj(...)
            is_class_call = caller_var in self.library_classes

            # Check chained call on direct instantiation, e.g. User(...).dict()
            is_chained_instantiation = False
            if isinstance(node.func.value, ast.Call):
                inst_func = self._get_call_func_name(node.func.value.func)
                if inst_func in self.library_classes:
                    is_chained_instantiation = True
                    var_type = inst_func

            is_model_receiver = bool(var_type or is_self_in_lib_class or is_class_call or is_chained_instantiation)
            if is_model_receiver:
                self.model_call_lines.add(node.lineno)

            if (
                is_caller_imported
                or var_type is not None
                or is_self_in_lib_class
                or is_class_call
                or is_chained_instantiation
                or (self.touches_target_lib and any(s.lower() in caller_repr.lower() for s in self.imported_symbols))
            ):
                self.touches_target_lib = True
                source_desc = var_type or (self.current_class if is_self_in_lib_class else caller_repr)
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f".{method_name}()",
                        usage_type="method_call",
                        line_number=node.lineno,
                        code_snippet=self._get_line_snippet(node.lineno),
                        complexity_weight=2 if is_model_receiver else 1,
                        deprecation_hint=f"Method call `.{method_name}()` on target library instance/symbol `{source_desc}`",
                    )
                )
        # 2. Direct function call: func()
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.library_classes:
                self.model_call_lines.add(node.lineno)
            if func_name in self.imported_symbols or func_name in self.library_classes:
                self.touches_target_lib = True
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f"{func_name}()",
                        usage_type="method_call",
                        line_number=node.lineno,
                        code_snippet=self._get_line_snippet(node.lineno),
                        complexity_weight=2,
                        deprecation_hint=f"Constructor or function call `{func_name}()` from target library",
                    )
                )

        self.generic_visit(node)

    def get_model_method_call_lines(self) -> Set[int]:
        return set(self.model_call_lines)


def scan_repository(
    repo_path_str: str,
    target_library: str,
    target_version: str,
) -> BlastRadiusReport:
    """
    Universal AST Blast-Radius Scanner.
    Inspects Python source files in the target repository to map imports and symbol usages
    for ANY target library. Zero tokens used.
    Only skips __pycache__, scanning __init__.py.
    Builds cross-file inheritance index and flags method calls on instances and typed parameters.
    """
    start_time = time.perf_counter()
    repo_path = Path(repo_path_str).resolve()
    old_version = detect_installed_library_version(repo_path, target_library)

    # 1. Build cross-file inheritance index of classes inheriting from library bases
    library_classes = build_cross_file_inheritance_index(repo_path, target_library)

    affected_files: List[AffectedFileReport] = []
    unique_symbols: Set[str] = set()
    total_scanned = 0
    super_count = 0
    ultra_count = 0

    for root, dirs, files in os.walk(repo_path):
        # Ignore hidden folders, caches, virtual environments (only skip __pycache__)
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in ("venv", ".venv", "env", "__pycache__", "node_modules")
        ]
        for file in sorted(files):
            # Only skip non-.py files (scan __init__.py)
            if not file.endswith(".py"):
                continue
            total_scanned += 1
            full_path = Path(root) / file
            rel_path = str(full_path.relative_to(repo_path))

            try:
                source_code = full_path.read_text(encoding="utf-8")
            except Exception:
                continue

            source_lines = source_code.splitlines()

            try:
                tree = ast.parse(source_code, filename=rel_path)
            except SyntaxError:
                continue

            visitor = FileASTVisitor(source_lines, target_library, library_classes)
            visitor.visit(tree)

            if visitor.touches_target_lib and visitor.usages:
                raw_score, norm_score, tier, model_id, reasons = score_and_route_file(visitor.usages)
                if tier == "NEMOTRON_SUPER":
                    super_count += 1
                elif tier == "NEMOTRON_ULTRA":
                    ultra_count += 1

                for u in visitor.usages:
                    unique_symbols.add(u.symbol_name)

                affected_files.append(
                    AffectedFileReport(
                        file_path=str(full_path),
                        relative_path=rel_path,
                        total_lines=len(source_lines),
                        imports_found=visitor.imports_found,
                        usages=visitor.usages,
                        raw_complexity_score=raw_score,
                        normalized_score=norm_score,
                        assigned_tier=tier,
                        target_model_id=model_id,
                        routing_reasons=reasons,
                        source_code=source_code,
                    )
                )

    # Sort files by complexity descending (highest complexity files first)
    affected_files.sort(key=lambda f: f.normalized_score, reverse=True)

    # Generate targeted Tavily search queries dynamically for this library & version
    sorted_symbols = sorted(unique_symbols)
    top_symbol_sample = ", ".join([s.split("(")[0].strip() for s in sorted_symbols[:4]])

    tavily_queries = [
        f"{target_library} {old_version or ''} to {target_version} official migration guide breaking changes".strip(),
        f"{target_library} upgrade {target_version} changelog release notes",
    ]
    if top_symbol_sample:
        tavily_queries.append(f"{target_library} {target_version} migration {top_symbol_sample}")

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
    savings_pct = calculate_cost_savings(super_count, ultra_count)

    return BlastRadiusReport(
        repo_path=str(repo_path),
        target_library=target_library,
        old_version=old_version,
        target_version=target_version,
        scan_duration_ms=elapsed_ms,
        total_files_scanned=total_scanned,
        affected_files_count=len(affected_files),
        super_routed_count=super_count,
        ultra_routed_count=ultra_count,
        estimated_cost_savings_pct=savings_pct,
        unique_deprecated_symbols=sorted_symbols,
        tavily_search_queries=tavily_queries,
        affected_files=affected_files,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("❌ Error: Missing required command-line arguments.")
        print("Usage: python -m backend.core.ast_scanner <repo_path> <target_library> <target_version>")
        print("Example: python -m backend.core.ast_scanner ./demo_target_repo pydantic 2.9.2")
        sys.exit(1)

    target_dir = sys.argv[1]
    lib = sys.argv[2]
    ver = sys.argv[3]

    report = scan_repository(target_dir, target_library=lib, target_version=ver)
    print("=" * 78)
    print("⚡ DRIFTZERO STAGE 1: UNIVERSAL AST BLAST-RADIUS SCANNER")
    print("=" * 78)
    print(f"Target Repo       : {report.repo_path}")
    print(f"Upgrade Path      : {report.target_library} {report.old_version or 'detected'} -> {report.target_version}")
    print(f"Scan Duration     : {report.scan_duration_ms} ms (Zero LLM Tokens Used)")
    print(f"Files Scanned     : {report.total_files_scanned} | Affected: {report.affected_files_count}")
    print(f"Smart Routing     : {report.super_routed_count} -> Nemotron Super | {report.ultra_routed_count} -> Nemotron 3 Ultra")
    print(f"Est. Cost Savings : {report.estimated_cost_savings_pct}% saved via Nebius Token Factory Tiered Routing")
    print("-" * 78)
    for af in report.affected_files:
        badge = "🔴 [ULTRA]" if af.assigned_tier == "NEMOTRON_ULTRA" else "🟢 [SUPER]"
        print(f"{badge} {af.relative_path:<32} | Score: {af.normalized_score:>2}/10 | Symbols: {len(af.usages)}")
    print("-" * 78)