import ast
from pathlib import Path
import pytest

from backend.core.codemods import (
    apply_deterministic_codemods,
    partition_files_for_stage3,
    _check_py_compile,
)
from backend.core.ast_scanner import FileASTVisitor
from backend.schemas.migration_models import (
    AffectedFileReport,
    BlastRadiusReport,
    MigrationDelta,
    MigrationRule,
    SymbolUsage,
)


def test_target_library_gate():
    code = "d = {'a': 1}\nx = d.dict()\n"
    # When target library is not pydantic, must return (code, False)
    transformed, modified = apply_deterministic_codemods(code, target_library="requests")
    assert transformed == code
    assert modified is False

    transformed, modified = apply_deterministic_codemods(code, target_library="numpy")
    assert transformed == code
    assert modified is False

    transformed, modified = apply_deterministic_codemods(code, target_library="")
    assert transformed == code
    assert modified is False


def test_known_model_method_call_rename():
    code = """from pydantic import BaseModel

class User(BaseModel):
    name: str

def foo(u: User):
    return u.dict()

def bar():
    d = {"a": 1}
    return d.dict()

def chained():
    return User(name="Alice").json()

def other_copy():
    lst = [1, 2]
    return lst.copy()
"""
    transformed, modified = apply_deterministic_codemods(code, target_library="pydantic")
    assert modified is True
    assert "u.model_dump()" in transformed
    assert "d.dict()" in transformed
    assert "User(name=\"Alice\").model_dump_json()" in transformed
    assert "lst.copy()" in transformed
    assert _check_py_compile(transformed)


def test_basesettings_to_pydantic_settings():
    code = """from pydantic import BaseSettings, Field

class AppSettings(BaseSettings):
    app_name: str = Field("LegacyPaymentService", env="APP_NAME")

    class Config:
        env_file = ".env"
        case_sensitive = False

def get_settings():
    s = AppSettings()
    return s.dict()
"""
    transformed, modified = apply_deterministic_codemods(code, target_library="pydantic")
    assert modified is True
    assert "from pydantic_settings import BaseSettings, SettingsConfigDict" in transformed
    assert "from pydantic import Field" in transformed
    assert "model_config = SettingsConfigDict(env_file=\".env\", case_sensitive=False)" in transformed
    assert "s.model_dump()" in transformed
    assert _check_py_compile(transformed)


def test_validator_to_field_validator():
    code = """from pydantic import BaseModel, validator

class User(BaseModel):
    name: str
    age: int

    @validator("name", pre=True)
    def val_name(cls, v):
        return v.strip()

    @validator("age", always=True)
    def val_age(cls, v):
        return v or 18
"""
    transformed, modified = apply_deterministic_codemods(code, target_library="pydantic")
    assert modified is True
    assert "from pydantic import BaseModel, field_validator" in transformed
    assert "validator" not in transformed.split("field_validator")[0] # validator removed from import
    assert '@field_validator("name", mode="before")' in transformed
    assert '@field_validator("age", validate_default=True)' in transformed
    assert "@classmethod" in transformed
    assert _check_py_compile(transformed)


def test_root_validator_to_model_validator():
    code = """from pydantic import BaseModel, root_validator

class User(BaseModel):
    a: int
    b: int

    @root_validator(pre=True)
    def val_before(cls, values):
        return values

    @root_validator
    def val_after(cls, values):
        return values

    @root_validator(skip_on_failure=True)
    def val_after_skip(cls, values):
        return values
"""
    transformed, modified = apply_deterministic_codemods(code, target_library="pydantic")
    assert modified is True
    assert "from pydantic import BaseModel, model_validator" in transformed
    assert '@model_validator(mode="before")' in transformed
    assert '@model_validator(mode="after")' in transformed
    assert "skip_on_failure" not in transformed
    assert _check_py_compile(transformed)


def test_parse_obj_as_to_type_adapter():
    code = """from typing import List, Dict, Any
from pydantic import BaseModel, parse_obj_as

class Item(BaseModel):
    id: str

def parse_items(data: List[Dict[str, Any]]):
    return parse_obj_as(List[Item], data)
"""
    transformed, modified = apply_deterministic_codemods(code, target_library="pydantic")
    assert modified is True
    assert "from pydantic import BaseModel, TypeAdapter" in transformed
    assert "TypeAdapter(List[Item]).validate_python(data)" in transformed
    assert "parse_obj_as" not in transformed
    assert _check_py_compile(transformed)


def test_genericmodel_to_basemodel_and_generic():
    code = """from typing import Generic, TypeVar
from pydantic.generics import GenericModel

T = TypeVar("T")

class Response(GenericModel, Generic[T]):
    data: T
"""
    transformed, modified = apply_deterministic_codemods(code, target_library="pydantic")
    assert modified is True
    assert "pydantic.generics" not in transformed
    assert "class Response(BaseModel, Generic[T]):" in transformed
    assert "from pydantic import BaseModel" in transformed
    assert _check_py_compile(transformed)


def test_partition_files_for_stage3():
    # 1. Resolved file: completely mechanical
    f_resolved = AffectedFileReport(
        file_path="f_res.py",
        relative_path="f_res.py",
        total_lines=10,
        imports_found=["pydantic.BaseSettings"],
        usages=[
            SymbolUsage("BaseSettings (Inheritance)", "base_class", 3, "class Settings(BaseSettings):", 4, ""),
            SymbolUsage(".dict()", "method_call", 10, "s.dict()", 2, ""),
        ],
        raw_complexity_score=6,
        normalized_score=10,
        assigned_tier="NEMOTRON_SUPER",
        target_model_id="nemotron-super",
        routing_reasons=[],
        source_code="""from pydantic import BaseSettings

class Settings(BaseSettings):
    x: str = "a"
    class Config:
        env_prefix = "P_"

s = Settings()
res = s.dict()
""",
    )

    # 2. Residual file with remaining deprecated symbol (__root__)
    f_deprecated_residual = AffectedFileReport(
        file_path="f_dep.py",
        relative_path="f_dep.py",
        total_lines=10,
        imports_found=["pydantic.BaseModel"],
        usages=[
            SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class M(BaseModel):", 4, ""),
            SymbolUsage("__root__", "special_field", 4, "__root__: int", 3, ""),
        ],
        raw_complexity_score=7,
        normalized_score=20,
        assigned_tier="NEMOTRON_ULTRA",
        target_model_id="nemotron-ultra",
        routing_reasons=[],
        source_code="""from pydantic import BaseModel

class M(BaseModel):
    __root__: int

m = M(__root__=1)
""",
    )

    # 3. Residual file with uncovered symbol
    f_uncovered_residual = AffectedFileReport(
        file_path="f_unc.py",
        relative_path="f_unc.py",
        total_lines=10,
        imports_found=["pydantic.BaseModel"],
        usages=[
            SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class M(BaseModel):", 4, ""),
            SymbolUsage(".as_openai_messages()", "method_call", 5, "m.as_openai_messages()", 2, ""),
        ],
        raw_complexity_score=6,
        normalized_score=15,
        assigned_tier="NEMOTRON_SUPER",
        target_model_id="nemotron-super",
        routing_reasons=[],
        source_code="""from pydantic import BaseModel

class M(BaseModel):
    x: str = "hello"

m = M()
res = m.as_openai_messages()
""",
    )

    # 4. Residual file that fails py_compile
    f_compile_fail = AffectedFileReport(
        file_path="f_fail.py",
        relative_path="f_fail.py",
        total_lines=5,
        imports_found=["pydantic.BaseModel"],
        usages=[
            SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class Bad(BaseModel):", 4, ""),
            SymbolUsage(".dict()", "method_call", 5, "b.dict()", 2, ""),
        ],
        raw_complexity_score=6,
        normalized_score=10,
        assigned_tier="NEMOTRON_SUPER",
        target_model_id="nemotron-super",
        routing_reasons=[],
        source_code="""from pydantic import BaseModel

class Bad(BaseModel):
    x: str

b = Bad(x=)
res = b.dict()
""",
    )

    blast_report = BlastRadiusReport(
        repo_path="/mock",
        target_library="pydantic",
        old_version="1.10.13",
        target_version="2.9.2",
        scan_duration_ms=1.0,
        total_files_scanned=4,
        affected_files_count=4,
        super_routed_count=3,
        ultra_routed_count=1,
        estimated_cost_savings_pct=50.0,
        unique_deprecated_symbols=["BaseSettings", ".dict()", "__root__", ".as_openai_messages()"],
        tavily_search_queries=[],
        affected_files=[f_resolved, f_deprecated_residual, f_uncovered_residual, f_compile_fail],
    )

    migration_delta = MigrationDelta(
        library="pydantic",
        from_version="1.10.13",
        to_version="2.9.2",
        tavily_queries_executed=[],
        citations=[],
        rules=[
            MigrationRule("BaseSettings", "BaseSettings", "pydantic_settings.BaseSettings", [], "", ""),
            MigrationRule(".dict()", ".dict()", ".model_dump()", [], "", ""),
            MigrationRule("__root__", "__root__", "RootModel", [], "", ""),
        ],
        synthesized_by_model="nemotron",
        generation_latency_ms=1.0,
        uncovered_symbols=[".as_openai_messages()"],
    )

    resolved, residual = partition_files_for_stage3(blast_report, migration_delta)

    # Only f_res.py meets: 0 deprecated remaining, 0 uncovered remaining, py_compile passes
    assert [p.relative_path for p in resolved] == ["f_res.py"]
    assert [f.relative_path for f in residual] == ["f_dep.py", "f_unc.py", "f_fail.py"]
    assert resolved[0].status == "SUCCESS"
    assert resolved[0].telemetry.prompt_tokens == 0
    assert resolved[0].telemetry.model_used == "LIBCST_DETERMINISTIC_CODEMOD"
