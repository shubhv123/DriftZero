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

class FileASTVisitor(ast.NodeVisitor):
    """
    Universal AST visitor that maps imports and symbol usages for ANY target library.
    Works on any codebase without hardcoding library-specific rules.
    """

    def __init__(self, source_lines: List[str], target_library: str):
        self.source_lines = source_lines
        self.target_library = target_library.lower().replace("-", "_")
        self.imports_found: List[str] = []
        self.imported_symbols: Set[str] = set()
        self.usages: List[SymbolUsage] = []
        self.touches_target_lib = False

    def _get_line_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def _is_target_module(self, mod_name: Optional[str]) -> bool:
        if not mod_name:
            return False
        clean = mod_name.lower().replace("-", "_")
        return clean == self.target_library or clean.startswith(f"{self.target_library}.") or clean.startswith(f"{self.target_library}_")

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
        # 1. Base class inheritance
        for base in node.bases:
            base_repr = ast.unparse(base) if hasattr(ast, "unparse") else ""
            matches_import = any(sym in base_repr for sym in self.imported_symbols)
            if matches_import or self.target_library in base_repr.lower():
                self.touches_target_lib = True
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

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_or_method(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_or_method(node)
        self.generic_visit(node)

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

    def visit_Call(self, node: ast.Call) -> None:
        # 1. Attribute call: obj.method()
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            caller_repr = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
            is_caller_imported = any(sym in caller_repr for sym in self.imported_symbols)
            
            if is_caller_imported or (self.touches_target_lib and any(s.lower() in caller_repr.lower() for s in self.imported_symbols)):
                self.usages.append(
                    SymbolUsage(
                        symbol_name=f".{method_name}()",
                        usage_type="method_call",
                        line_number=node.lineno,
                        code_snippet=self._get_line_snippet(node.lineno),
                        complexity_weight=1,
                        deprecation_hint=f"Method call `.{method_name}()` on target library symbol `{caller_repr}`",
                    )
                )
        # 2. Direct function call: func()
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.imported_symbols:
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


def scan_repository(
    repo_path_str: str,
    target_library: str,
    target_version: str,
) -> BlastRadiusReport:
    """
    Universal AST Blast-Radius Scanner.
    Inspects every Python source file in the target repository to map imports and symbol usages
    for ANY target library. Zero tokens used.
    """
    start_time = time.perf_counter()
    repo_path = Path(repo_path_str).resolve()
    old_version = detect_installed_library_version(repo_path, target_library)

    affected_files: List[AffectedFileReport] = []
    unique_symbols: Set[str] = set()
    total_scanned = 0
    super_count = 0
    ultra_count = 0

    for root, dirs, files in os.walk(repo_path):
        # Ignore hidden folders, caches, virtual environments
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", ".venv", "env", "__pycache__", "node_modules")]
        for file in sorted(files):
            if not file.endswith(".py") or file.startswith("__"):
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

            visitor = FileASTVisitor(source_lines, target_library)
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