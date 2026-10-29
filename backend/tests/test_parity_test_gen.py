import json
import tempfile
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from backend.schemas.migration_models import MigrationDelta
from backend.verification.parity_test_gen import (
    _build_deterministic_differential_suite,
    _build_model_sample_inputs,
    _discover_migrated_symbols,
    _generate_sample_input_for_field,
    _serialize_value,
    dry_run_candidate_in_record_mode,
    generate_parity_test_suite,
)


class BaseEntity(BaseModel):
    entity_id: str
    created_at: datetime
    balance: Decimal


class AccountEntity(BaseEntity):
    account_number: str
    is_active: bool = True
    tags: list = Field(default_factory=list)


class StatusEnum(Enum):
    PENDING = "pending"
    SETTLED = "settled"


class Transaction(BaseModel):
    status: StatusEnum
    amount: float


def test_build_model_sample_inputs_includes_inherited_fields():
    """Verify inputs are built from model's own fields (model_fields or __fields__, including inherited ones)."""
    # AccountEntity inherits entity_id, created_at, balance from BaseEntity
    sample_inputs = _build_model_sample_inputs(AccountEntity)

    # Must contain both subclass fields and inherited base fields
    assert "entity_id" in sample_inputs
    assert "created_at" in sample_inputs
    assert "balance" in sample_inputs
    assert "account_number" in sample_inputs
    assert "is_active" in sample_inputs

    # Instantiating the model with these inputs must succeed
    instance = AccountEntity(**sample_inputs)
    assert instance.entity_id == "test_entity_id_123"
    assert instance.account_number == "sample_account_number"


def test_emit_sample_kwargs_uses_repr_not_json_dumps():
    """Verify that sample_kwargs is emitted with repr() (e.g. True/False/None) instead of json.dumps (true/false/null)."""
    delta = MigrationDelta(
        library="pydantic",
        from_version="1.10.12",
        to_version="2.9.2",
        tavily_queries_executed=[],
        citations=[],
        rules=[],
        synthesized_by_model="test",
        generation_latency_ms=1.0,
    )

    modules = [
        {
            "rel_path": "models.py",
            "module": "models",
            "classes": [
                {
                    "name": "UserConfig",
                    "fields": {
                        "is_active": "bool",
                        "extra_info": "None",
                    },
                }
            ],
            "functions": [
                {
                    "name": "compute",
                    "args": {
                        "enabled": "bool",
                    },
                }
            ],
        }
    ]

    suite_code = _build_deterministic_differential_suite(modules, delta)

    # repr() emits True, False, None in Python syntax, NOT json true, false, null
    assert "True" in suite_code or "False" in suite_code
    assert "sample_kwargs = " in suite_code
    assert "json.dumps" not in suite_code.split("def test_differential_parity_0_models():")[1]
    assert ": true" not in suite_code
    assert ": false" not in suite_code
    assert ": null" not in suite_code


def test_serialize_value_mode_json_normalization():
    """Verify both sides are normalized through _serialize_value(mode='json')."""
    t = Transaction(status=StatusEnum.SETTLED, amount=123.45)
    normalized = _serialize_value(t, mode="json")

    assert normalized["status"] == "settled"
    assert normalized["amount"] == 123.45

    # Test Decimals, dates, and nested types
    data = {
        "price": Decimal("49.99"),
        "timestamp": datetime(2025, 1, 1, 12, 0, 0),
        "tags": {"tag_a", "tag_b"},
    }
    norm_dict = _serialize_value(data, mode="json")
    assert norm_dict["price"] == "49.99"
    assert norm_dict["timestamp"] == "2025-01-01T12:00:00"
    assert isinstance(norm_dict["tags"], list)
    assert sorted(norm_dict["tags"]) == ["tag_a", "tag_b"]


def test_error_type_only_comparison_in_test_suite():
    """Verify generated test suite compares error type only, not message."""
    delta = MigrationDelta(
        library="pydantic",
        from_version="1.10.12",
        to_version="2.9.2",
        tavily_queries_executed=[],
        citations=[],
        rules=[],
        synthesized_by_model="test",
        generation_latency_ms=1.0,
    )
    modules = [
        {
            "rel_path": "api.py",
            "module": "api",
            "classes": [{"name": "MyModel", "fields": {"id": "str"}}],
            "functions": [{"name": "my_func", "args": {"x": "int"}}],
        }
    ]
    suite_code = _build_deterministic_differential_suite(modules, delta)

    # Must compare error type only
    assert "actual_type == expected_type" in suite_code
    assert 'expected_type = expected["invalid_error_type"]' in suite_code
    # Must NOT assert error message equality
    assert 'assert str(exc_info.value) == expected["error_message"]' not in suite_code
    assert 'assert actual_msg == expected["invalid_error_message"]' not in suite_code


def test_global_golden_store_used():
    """Verify global _golden_store is declared and used in the generated suite."""
    delta = MigrationDelta(
        library="pydantic",
        from_version="1.10.12",
        to_version="2.9.2",
        tavily_queries_executed=[],
        citations=[],
        rules=[],
        synthesized_by_model="test",
        generation_latency_ms=1.0,
    )
    suite_code = _build_deterministic_differential_suite([], delta)
    assert "_golden_store: dict = {}" in suite_code
    assert "global _golden_store" in suite_code


def test_dry_run_candidate_in_record_mode():
    """Verify dry_run_candidate_in_record_mode rejects invalid test code and cleans up."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir)
        (repo_dir / "src").mkdir()

        # 1. Invalid syntax candidate
        bad_syntax = "def test_something( invalid syntax here!"
        res_bad = dry_run_candidate_in_record_mode(bad_syntax, repo_dir)
        assert res_bad is False
        assert not (repo_dir / "_dry_run_parity_test.py").exists()

        # 2. Failing test candidate (raises AssertionError)
        failing_code = """
import os
def test_failing():
    assert False, "Intentional failure in dry run"
"""
        res_fail = dry_run_candidate_in_record_mode(failing_code, repo_dir)
        assert res_fail is False
        assert not (repo_dir / "_dry_run_parity_test.py").exists()

        # 3. Passing test in record mode
        passing_code = """
import os
def test_passing():
    assert os.getenv("DRIFTZERO_RECORD_GOLDEN") == "1"
"""
        res_pass = dry_run_candidate_in_record_mode(passing_code, repo_dir)
        assert res_pass is True
        assert not (repo_dir / "_dry_run_parity_test.py").exists()


def test_generate_parity_test_suite_deterministic_fallback():
    """Verify generate_parity_test_suite outputs valid deterministic suite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir)
        (repo_dir / "models.py").write_text("class MyOrder:\n    pass\n", encoding="utf-8")

        delta = MigrationDelta(
            library="pydantic",
            from_version="1.10.12",
            to_version="2.9.2",
            tavily_queries_executed=[],
            citations=[],
            rules=[],
            synthesized_by_model="test",
            generation_latency_ms=1.0,
        )

        test_file = generate_parity_test_suite(repo_dir, delta)
        assert test_file.exists()
        content = test_file.read_text(encoding="utf-8")
        assert "_golden_store" in content
        assert "_serialize_value" in content
        assert "_build_model_sample_inputs" in content
