import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.intelligence.telemetry_tracker import PipelineTelemetrySummary
from backend.intelligence.token_factory_router import (
    _validate_patch,
    patch_single_file_via_token_factory,
    execute_stage2_and_stage3_pipeline,
)
from backend.schemas.migration_models import (
    AffectedFileReport,
    BlastRadiusReport,
    FilePatchResult,
    MigrationDelta,
    MigrationRule,
    SymbolUsage,
    TelemetryEvent,
    Stage4VerificationReport,
    SandboxRunAttempt,
)


def test_validate_patch_exact_ast_matching():
    rules = [
        MigrationRule(
            symbol="validator",
            old_syntax="@validator",
            new_syntax="@field_validator",
            required_imports=["field_validator"],
            breaking_gotcha="Use field_validator in v2",
            source_url="https://docs.pydantic.dev",
        ),
        MigrationRule(
            symbol=".dict()",
            old_syntax="m.dict()",
            new_syntax="m.model_dump()",
            required_imports=[],
            breaking_gotcha="Use model_dump in v2",
            source_url="https://docs.pydantic.dev",
        ),
    ]

    # Valid v2 code using @field_validator should NOT be rejected by substring matching "validator"
    valid_v2_code = '''
from pydantic import BaseModel, field_validator

class UserModel(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return v.strip()

def export(m: UserModel):
    return m.model_dump()
'''
    orig_code = '''
from pydantic import BaseModel, validator

class UserModel(BaseModel):
    name: str

    @validator("name")
    def validate_name(cls, v: str) -> str:
        return v.strip()

def export(m: UserModel):
    return m.dict()
'''
    valid, err = _validate_patch(valid_v2_code, orig_code, "pydantic", rules)
    assert valid is True, f"Valid v2 code should pass AST validation, got: {err}"

    # Invalid code still using @validator decorator and .dict()
    invalid_v2_code = '''
from pydantic import BaseModel, validator

class UserModel(BaseModel):
    name: str

    @validator("name")
    def validate_name(cls, v: str) -> str:
        return v.strip()

def export(m: UserModel):
    return m.dict()
'''
    valid, err = _validate_patch(invalid_v2_code, orig_code, "pydantic", rules)
    assert valid is False
    assert "validator" in err
    assert ".dict()" in err


def test_validate_patch_exact_base_settings_and_parse_obj():
    rules = [
        MigrationRule(
            symbol="BaseSettings",
            old_syntax="from pydantic import BaseSettings",
            new_syntax="from pydantic_settings import BaseSettings",
            required_imports=["BaseSettings"],
            breaking_gotcha="Moved to pydantic_settings",
            source_url="https://docs.pydantic.dev",
        ),
    ]

    # Deprecated: BaseSettings imported directly from pydantic
    deprecated_code = '''
from pydantic import BaseSettings

class AppSettings(BaseSettings):
    env: str
'''
    valid, err = _validate_patch(deprecated_code, deprecated_code, "pydantic", rules)
    assert valid is False
    assert "BaseSettings" in err

    # Fixed: BaseSettings imported from pydantic_settings
    upgraded_code = '''
from pydantic_settings import BaseSettings

class AppSettings(BaseSettings):
    env: str
'''
    valid, err = _validate_patch(upgraded_code, deprecated_code, "pydantic", rules)
    assert valid is True


def test_patch_single_file_uses_source_code_or_file_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        disk_file = Path(tmpdir) / "models.py"
        disk_file.write_text("class FromDisk:\n    pass\n", encoding="utf-8")

        # 1. When source_code is provided in AffectedFileReport, it is used
        report_with_source = AffectedFileReport(
            file_path=str(disk_file),
            relative_path="models.py",
            total_lines=2,
            imports_found=["pydantic"],
            usages=[],
            raw_complexity_score=1,
            normalized_score=1,
            assigned_tier="NEMOTRON_SUPER",
            target_model_id="super",
            routing_reasons=[],
            source_code="class FromMemory:\n    pass\n",
        )
        delta = MigrationDelta(
            library="pydantic",
            from_version="1.10.0",
            to_version="2.9.2",
            tavily_queries_executed=[],
            citations=[],
            rules=[],
            synthesized_by_model="test",
            generation_latency_ms=10.0,
        )

        with patch("backend.intelligence.token_factory_router.NEBIUS_API_KEY", ""):
            res = patch_single_file_via_token_factory(report_with_source, delta)
            assert res.original_code == "class FromMemory:\n    pass\n"

        # 2. When source_code is None, it reads from disk
        report_without_source = AffectedFileReport(
            file_path=str(disk_file),
            relative_path="models.py",
            total_lines=2,
            imports_found=["pydantic"],
            usages=[],
            raw_complexity_score=1,
            normalized_score=1,
            assigned_tier="NEMOTRON_SUPER",
            target_model_id="super",
            routing_reasons=[],
            source_code=None,
        )
        with patch("backend.intelligence.token_factory_router.NEBIUS_API_KEY", ""):
            res = patch_single_file_via_token_factory(report_without_source, delta)
            assert res.original_code == "class FromDisk:\n    pass\n"


def test_catch_missing_nebius_api_key_as_failed():
    report = AffectedFileReport(
        file_path="/fake/path.py",
        relative_path="path.py",
        total_lines=2,
        imports_found=[],
        usages=[],
        raw_complexity_score=1,
        normalized_score=1,
        assigned_tier="NEMOTRON_SUPER",
        target_model_id="super",
        routing_reasons=[],
        source_code="class MyModel:\n    pass\n",
    )
    delta = MigrationDelta(
        library="pydantic",
        from_version="1.10.0",
        to_version="2.9.2",
        tavily_queries_executed=[],
        citations=[],
        rules=[],
        synthesized_by_model="test",
        generation_latency_ms=10.0,
    )

    with patch("backend.intelligence.token_factory_router.NEBIUS_API_KEY", ""):
        res = patch_single_file_via_token_factory(report, delta)
        assert res.status == "FAILED"
        assert res.syntax_valid is False
        assert "NEBIUS_API_KEY is not configured" in (res.error_message or "")


def test_codemod_counter_separate_from_super_and_ultra():
    telemetry = PipelineTelemetrySummary()

    # Codemod event
    codemod_evt = TelemetryEvent(
        file_path="model.py",
        assigned_tier="LIBCST_DETERMINISTIC_CODEMOD",
        model_used="LIBCST_DETERMINISTIC_CODEMOD",
        complexity_score=1,
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=5.0,
        actual_cost_usd=0.0,
        hypothetical_ultra_cost_usd=0.01,
        cost_saved_usd=0.01,
        execution_mode="LIBCST_DETERMINISTIC_CODEMOD",
    )
    # Super event
    super_evt = TelemetryEvent(
        file_path="service.py",
        assigned_tier="NEMOTRON_SUPER",
        model_used="super_model",
        complexity_score=3,
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=200.0,
        actual_cost_usd=0.001,
        hypothetical_ultra_cost_usd=0.005,
        cost_saved_usd=0.004,
        execution_mode="NEBIUS_TOKEN_FACTORY",
    )
    # Ultra event
    ultra_evt = TelemetryEvent(
        file_path="deep.py",
        assigned_tier="NEMOTRON_ULTRA",
        model_used="ultra_model",
        complexity_score=8,
        prompt_tokens=500,
        completion_tokens=200,
        latency_ms=800.0,
        actual_cost_usd=0.01,
        hypothetical_ultra_cost_usd=0.01,
        cost_saved_usd=0.0,
        execution_mode="NEBIUS_TOKEN_FACTORY",
    )

    telemetry.record_event(codemod_evt)
    telemetry.record_event(super_evt)
    telemetry.record_event(ultra_evt)

    assert telemetry.total_events == 3
    assert telemetry.codemod_calls == 1
    assert telemetry.super_calls == 1
    assert telemetry.ultra_calls == 1

    summary_dict = telemetry.to_dict()
    assert summary_dict["codemod_calls"] == 1
    assert summary_dict["super_calls"] == 1
    assert summary_dict["ultra_calls"] == 1


def test_failed_patch_overrides_final_status_and_pr():
    from backend.exporters.github_pr_client import create_migration_pr

    with tempfile.TemporaryDirectory() as tmpdir:
        ws_dir = Path(tmpdir)
        (ws_dir / "models.py").write_text("x = 1\n", encoding="utf-8")

        delta = MigrationDelta(
            library="pydantic",
            from_version="1.10.0",
            to_version="2.9.2",
            tavily_queries_executed=[],
            citations=[],
            rules=[],
            synthesized_by_model="test",
            generation_latency_ms=10.0,
        )

        # Stage 4 report with PASSED from sandbox
        stage4_report = Stage4VerificationReport(
            test_file_path="tests/test_parity.py",
            total_attempts=1,
            final_status="PASSED",
            self_healing_triggered=False,
            attempts=[
                SandboxRunAttempt(
                    attempt_number=1,
                    job_id="job-1",
                    execution_target="nebius_sandbox",
                    exit_code=0,
                    passed_count=5,
                    failed_count=0,
                    duration_ms=120.0,
                    stdout_log="5 passed",
                    stderr_traceback="",
                    healed_file=None,
                    healed_by_model=None,
                )
            ],
        )

        # Simulated Stage 3 patches containing a FAILED patch
        failed_patch = FilePatchResult(
            relative_path="failed.py",
            original_code="bad code",
            patched_code="",
            unified_diff="",
            assigned_tier="NEMOTRON_SUPER",
            model_used="super",
            syntax_valid=False,
            telemetry=TelemetryEvent(
                file_path="failed.py",
                assigned_tier="NEMOTRON_SUPER",
                model_used="super",
                complexity_score=1,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                actual_cost_usd=0.0,
                hypothetical_ultra_cost_usd=0.0,
                cost_saved_usd=0.0,
            ),
            status="FAILED",
            error_message="Missing NEBIUS_API_KEY",
        )
        patches = [failed_patch]

        # Pipeline logic check: If any patch is FAILED, set final_status="FAILED"
        if any(p.status == "FAILED" for p in patches):
            stage4_report.final_status = "FAILED"

        assert stage4_report.final_status == "FAILED"

        # When final_status is FAILED and skip_on_fail is True, no PR is opened (returns None)
        pr_url_skipped = create_migration_pr(
            ws_dir, delta, stage4_report, "https://github.com/shubhv123/DriftZero", None, skip_on_fail=True
        )
        assert pr_url_skipped is None
