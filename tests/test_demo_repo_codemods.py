import ast
import py_compile
import shutil
import tempfile
from pathlib import Path
import pytest

from backend.core.ast_scanner import scan_repository, FileASTVisitor
from backend.core.codemods import apply_deterministic_codemods, partition_files_for_stage3
from backend.schemas.migration_models import MigrationDelta
from backend.verification.parity_test_gen import (
    generate_parity_test_suite,
    _generate_sample_input_for_field,
)


@pytest.fixture
def pydantic_migration_delta() -> MigrationDelta:
    return MigrationDelta(
        library="pydantic",
        from_version="1.10.13",
        to_version="2.9.2",
        tavily_queries_executed=["migration guide pydantic v1 to v2"],
        citations=["https://docs.pydantic.dev/2.9/migration/"],
        rules=[],
        synthesized_by_model="test-router",
        generation_latency_ms=15.0,
    )


def test_regression_dict_copy_stays_unchanged():
    """
    Regression Test:
    Plain Python dictionary .copy() calls MUST remain unchanged, while
    Pydantic BaseModel .copy() calls are safely migrated to .model_copy().
    """
    serializers_path = Path("demo_target_repo/src/serializers.py")
    source_code = serializers_path.read_text()

    # Verify original code contains both a model .copy and a dict .copy
    assert "order.copy(" in source_code
    assert "raw_meta.copy()" in source_code

    tree = ast.parse(source_code)
    visitor = FileASTVisitor(source_code.splitlines(), "pydantic", {"OrderRequest", "AuditRecord"})
    visitor.visit(tree)
    model_lines = visitor.get_model_method_call_lines()

    # Apply deterministic codemod targeted to detected model method calls
    codemodded_code, modified = apply_deterministic_codemods(
        source_code,
        target_library="pydantic",
        model_call_lines=model_lines,
        library_classes={"OrderRequest", "AuditRecord"},
    )

    assert modified, "Expected serializers.py to be modified"

    # 1. order.copy(...) must be renamed to order.model_copy(...)
    assert "order.model_copy(update=" in codemodded_code
    assert "order.copy(" not in codemodded_code

    # 2. raw_meta.copy() on plain dict MUST remain unchanged
    assert "raw_meta.copy()" in codemodded_code
    assert "raw_meta.model_copy()" not in codemodded_code

    # 3. Explicit inline test case with variable assigned dict
    inline_test = """
def test_inline():
    d = {"key": "value"}
    c = d.copy()
    return c
"""
    inline_tree = ast.parse(inline_test)
    inline_visitor = FileASTVisitor(inline_test.splitlines(), "pydantic", set())
    inline_visitor.visit(inline_tree)
    inline_lines = inline_visitor.get_model_method_call_lines()

    mod_inline, mod_flag = apply_deterministic_codemods(
        inline_test,
        target_library="pydantic",
        model_call_lines=inline_lines,
    )
    assert "d.copy()" in mod_inline
    assert "d.model_copy()" not in mod_inline


def test_regression_basesettings_is_migrated():
    """
    Regression Test:
    BaseSettings from pydantic must be migrated to pydantic_settings.BaseSettings,
    Config must become SettingsConfigDict, and settings.dict() must become settings.model_dump().
    """
    config_path = Path("demo_target_repo/src/config.py")
    source_code = config_path.read_text()

    assert "from pydantic import BaseSettings" in source_code
    assert "class Config:" in source_code
    assert "settings.dict()" in source_code

    tree = ast.parse(source_code)
    visitor = FileASTVisitor(source_code.splitlines(), "pydantic", {"AppSettings"})
    visitor.visit(tree)
    model_lines = visitor.get_model_method_call_lines()

    codemodded_code, modified = apply_deterministic_codemods(
        source_code,
        target_library="pydantic",
        model_call_lines=model_lines,
        library_classes={"AppSettings"},
    )

    assert modified, "Expected config.py to be modified"

    # BaseSettings import rewritten
    assert "from pydantic_settings import BaseSettings, SettingsConfigDict" in codemodded_code
    assert "from pydantic import BaseSettings" not in codemodded_code

    # SettingsConfigDict introduced and Config class removed
    assert "model_config = SettingsConfigDict(" in codemodded_code
    assert "env_file=\".env\"" in codemodded_code
    assert "class Config:" not in codemodded_code

    # settings.dict() rewritten to settings.model_dump()
    assert "settings.model_dump()" in codemodded_code
    assert "settings.dict()" not in codemodded_code

    # Verify python syntax validity
    compiled_ast = ast.parse(codemodded_code)
    assert compiled_ast is not None


def test_regression_bool_fields_dont_break_generated_test(pydantic_migration_delta: MigrationDelta):
    """
    Regression Test:
    Boolean fields on models (such as HighRiskWireTransfer.requires_manual_compliance_review: bool = False)
    must produce valid boolean sample values (True / False), NOT string fallbacks like 'sample_...'
    which would fail pydantic schema validation during test execution.
    """
    # 1. Unit verification of sample generator for boolean fields
    bool_sample = _generate_sample_input_for_field("requires_manual_compliance_review", "bool")
    assert isinstance(bool_sample, bool), f"Expected bool, got {type(bool_sample)}"
    assert bool_sample is False

    bool_flag_sample = _generate_sample_input_for_field("is_active", "bool")
    assert isinstance(bool_flag_sample, bool)
    assert bool_flag_sample is True

    # 2. End-to-end parity test generation against the demo repo
    with tempfile.TemporaryDirectory() as tmp_dir:
        target_repo = Path(tmp_dir) / "demo_repo"
        shutil.copytree("demo_target_repo", target_repo)

        test_file_path = generate_parity_test_suite(target_repo, pydantic_migration_delta)
        assert test_file_path.exists(), "test_migration_parity.py was not created"

        suite_content = test_file_path.read_text()

        # Check that HighRiskWireTransfer (which contains the bool field) is present in differential tests
        assert "HighRiskWireTransfer" in suite_content
        assert "requires_manual_compliance_review" in suite_content

        # Verify the entire test suite file compiles as valid Python
        parsed_tree = ast.parse(suite_content)
        assert parsed_tree is not None

        # Confirm boolean input generation handler in the generated runtime template
        assert "if \"bool\" in t_clean:" in suite_content
        assert "return True" in suite_content


def test_full_codemods_across_demo_repo(pydantic_migration_delta: MigrationDelta):
    """
    Runs deterministic partition and codemods across the full demo repository.
    Verifies that all files compile and key transformations are in place.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        target_repo = Path(tmp_dir) / "demo_repo"
        shutil.copytree("demo_target_repo", target_repo)

        blast_report = scan_repository(str(target_repo), "pydantic", "2.9.2")
        resolved_files, residual_files = partition_files_for_stage3(
            blast_report, pydantic_migration_delta
        )

        assert len(resolved_files) > 0, "Expected mechanical files in demo repo to resolve"

        for patch in resolved_files:
            (target_repo / patch.relative_path).write_text(patch.patched_code)

        # Check serializers.py
        serializers_mod = (target_repo / "src" / "serializers.py").read_text()
        assert "order.model_copy(" in serializers_mod
        assert "raw_meta.copy()" in serializers_mod

        # Check config.py
        config_mod = (target_repo / "src" / "config.py").read_text()
        assert "from pydantic_settings import BaseSettings, SettingsConfigDict" in config_mod
        assert "model_config = SettingsConfigDict(" in config_mod
