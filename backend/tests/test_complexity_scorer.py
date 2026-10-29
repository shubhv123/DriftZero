import pytest
from backend.core.complexity_scorer import (
    calculate_cost_savings,
    score_and_route_file,
)
from backend.schemas.migration_models import SymbolUsage


def test_empty_usages():
    raw_score, norm_score, tier, model_id, reasons = score_and_route_file([])
    assert raw_score == 0
    assert norm_score == 1
    assert tier == "NEMOTRON_SUPER"
    assert "super" in model_id.lower()


def test_mechanical_file_with_base_model_inheritance_routes_to_super():
    """
    Subclassing BaseModel is mechanical and must NOT mark the file as complex.
    It should route to NEMOTRON_SUPER.
    """
    usages = [
        SymbolUsage("pydantic.BaseModel", "import", 1, "from pydantic import BaseModel", 1, ""),
        SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class User(BaseModel):", 4, ""),
        SymbolUsage(".dict()", "method_call", 7, "u.dict()", 2, ""),
        SymbolUsage(".json()", "method_call", 8, "u.json()", 2, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_SUPER"
    assert norm_score <= 5
    assert any("Deterministic syntactic deprecation pattern" in r for r in reasons)


def test_mechanical_file_multiple_models_and_method_calls_routes_to_super():
    """
    Multiple BaseModel subclasses and multiple .dict()/.json() calls are mechanical
    and should route to NEMOTRON_SUPER without getting inflated into Ultra.
    """
    usages = [
        SymbolUsage("pydantic.BaseModel", "import", 1, "from pydantic import BaseModel", 1, ""),
        SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class User(BaseModel):", 4, ""),
        SymbolUsage("BaseModel (Inheritance)", "base_class", 6, "class Item(BaseModel):", 4, ""),
        SymbolUsage("BaseModel (Inheritance)", "base_class", 9, "class Order(BaseModel):", 4, ""),
        SymbolUsage(".dict()", "method_call", 12, "u.dict()", 2, ""),
        SymbolUsage(".dict()", "method_call", 13, "i.dict()", 2, ""),
        SymbolUsage(".json()", "method_call", 14, "o.json()", 2, ""),
        SymbolUsage(".copy()", "method_call", 15, "u.copy()", 2, ""),
        SymbolUsage(".schema()", "method_call", 16, "Item.schema()", 2, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_SUPER"
    assert norm_score <= 5


def test_validator_weighed_as_high_risk_routes_to_ultra():
    """
    Files with @validator or @root_validator have high migration risk and must route to NEMOTRON_ULTRA.
    """
    usages = [
        SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class User(BaseModel):", 4, ""),
        SymbolUsage("@validator('name')", "decorator", 5, "@validator('name')", 3, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_ULTRA"
    assert norm_score >= 6
    assert any("validator" in r.lower() for r in reasons)


def test_root_validator_weighed_as_high_risk_routes_to_ultra():
    usages = [
        SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class User(BaseModel):", 4, ""),
        SymbolUsage("@root_validator", "decorator", 5, "@root_validator\ndef check(cls, v):", 3, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_ULTRA"
    assert norm_score >= 6
    assert any("validator" in r.lower() for r in reasons)


def test_root_special_field_weighed_as_high_risk_routes_to_ultra():
    """
    Files with __root__ have high migration risk and must route to NEMOTRON_ULTRA.
    """
    usages = [
        SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class ItemList(BaseModel):", 4, ""),
        SymbolUsage("__root__", "special_field", 4, "__root__: List[str]", 3, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_ULTRA"
    assert norm_score >= 6
    assert any("__root__" in r for r in reasons)


def test_json_encoders_weighed_as_high_risk_routes_to_ultra():
    """
    Files with json_encoders have high migration risk and must route to NEMOTRON_ULTRA.
    """
    usages = [
        SymbolUsage("BaseModel (Inheritance)", "base_class", 3, "class Event(BaseModel):", 4, ""),
        SymbolUsage("json_encoders", "config_attr", 6, "json_encoders = {datetime: str}", 2, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_ULTRA"
    assert norm_score >= 6
    assert any("json_encoders" in r for r in reasons)


def test_basesettings_weighed_as_high_risk_routes_to_ultra():
    """
    Files with BaseSettings have high migration risk and must route to NEMOTRON_ULTRA.
    """
    usages = [
        SymbolUsage("pydantic.BaseSettings", "import", 1, "from pydantic import BaseSettings", 1, ""),
        SymbolUsage("BaseSettings (Inheritance)", "base_class", 3, "class Settings(BaseSettings):", 4, ""),
        SymbolUsage(".dict()", "method_call", 7, "s.dict()", 2, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_ULTRA"
    assert norm_score >= 6
    assert any("BaseSettings" in r for r in reasons)


def test_genericmodel_weighed_as_high_risk_routes_to_ultra():
    """
    Files with GenericModel have high migration risk and must route to NEMOTRON_ULTRA.
    """
    usages = [
        SymbolUsage("GenericModel (Inheritance)", "base_class", 3, "class Data(GenericModel, Generic[T]):", 4, ""),
        SymbolUsage(".dict()", "method_call", 7, "d.dict()", 2, ""),
    ]

    raw_score, norm_score, tier, model_id, reasons = score_and_route_file(usages)

    assert tier == "NEMOTRON_ULTRA"
    assert norm_score >= 6
    assert any("GenericModel" in r for r in reasons)


def test_calculate_cost_savings():
    # 0 files
    assert calculate_cost_savings(0, 0) == 0.0

    # 100% Super vs 100% Ultra
    savings = calculate_cost_savings(super_count=10, ultra_count=0)
    assert savings > 70.0  # Super is much cheaper than Ultra

    # Mixed
    savings_mixed = calculate_cost_savings(super_count=5, ultra_count=5)
    assert 0.0 < savings_mixed < savings
