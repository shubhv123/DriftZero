# Scores affected files (1-10) to decide Nano/Super vs Ultra routing
from typing import List, Tuple
from backend.schemas.migration_models import SymbolUsage
from backend.config import (
    NEMOTRON_SUPER_MODEL,
    NEMOTRON_ULTRA_MODEL,
    PRICE_PER_1M_SUPER_INPUT,
    PRICE_PER_1M_SUPER_OUTPUT,
    PRICE_PER_1M_ULTRA_INPUT,
    PRICE_PER_1M_ULTRA_OUTPUT,
)


def _is_validator_usage(u: SymbolUsage) -> bool:
    s = u.symbol_name.lower()
    snip = u.code_snippet.lower()
    return "validator" in s or (u.usage_type == "decorator" and "validator" in s) or "validator" in snip


def _is_root_usage(u: SymbolUsage) -> bool:
    return "__root__" in u.symbol_name or "__root__" in u.code_snippet


def _is_json_encoders_usage(u: SymbolUsage) -> bool:
    s = u.symbol_name.lower()
    snip = u.code_snippet.lower()
    return "json_encoders" in s or "json_encoders" in snip


def _is_basesettings_usage(u: SymbolUsage) -> bool:
    s = u.symbol_name.lower()
    snip = u.code_snippet.lower()
    return "basesettings" in s or "basesettings" in snip


def _is_genericmodel_usage(u: SymbolUsage) -> bool:
    s = u.symbol_name.lower()
    snip = u.code_snippet.lower()
    return "genericmodel" in s or "genericmodel" in snip


def _usage_risk_weight(u: SymbolUsage) -> Tuple[int, str]:
    if _is_validator_usage(u):
        return 6, "validator"
    if _is_root_usage(u):
        return 6, "__root__"
    if _is_json_encoders_usage(u):
        return 6, "json_encoders"
    if _is_basesettings_usage(u):
        return 6, "BaseSettings"
    if _is_genericmodel_usage(u):
        return 6, "GenericModel"
    # Mechanical usages:
    if u.usage_type == "base_class":
        return 2, "base_class"
    if u.usage_type in ("method_call", "import", "typed_parameter", "instance_creation"):
        return 1, u.usage_type
    if u.usage_type in ("config_attr", "special_field"):
        return 2, u.usage_type
    if u.usage_type == "decorator":
        return 3, "decorator"
    return max(1, u.complexity_weight if u.complexity_weight is not None else 1), "other"


def score_and_route_file(usages: List[SymbolUsage]) -> Tuple[int, int, str, str, List[str]]:
    """
    Computes universal AST complexity, normalizes to a 1-10 score, and assigns the Nebius Token Factory tier.
    Rule:
      - Normalized Score <= 5 -> NEMOTRON_SUPER (Fast syntactic replacements, simple method & import renames)
      - Normalized Score > 5  -> NEMOTRON_ULTRA (Deep structural, architectural, or multi-field semantic refactoring)
    """
    if not usages:
        return 0, 1, "NEMOTRON_SUPER", NEMOTRON_SUPER_MODEL, ["No symbol usages detected"]

    reasons: List[str] = []

    # High-risk migration symbols
    validator_count = sum(1 for u in usages if _is_validator_usage(u))
    root_count = sum(1 for u in usages if _is_root_usage(u))
    json_encoders_count = sum(1 for u in usages if _is_json_encoders_usage(u))
    basesettings_count = sum(1 for u in usages if _is_basesettings_usage(u))
    genericmodel_count = sum(1 for u in usages if _is_genericmodel_usage(u))

    has_high_risk = (
        validator_count > 0
        or root_count > 0
        or json_encoders_count > 0
        or basesettings_count > 0
        or genericmodel_count > 0
    )

    if validator_count > 0:
        reasons.append(f"Custom validator(s) detected ({validator_count} site(s); requires field_validator/model_validator migration)")

    if root_count > 0:
        reasons.append(f"Custom root type `__root__` detected ({root_count} site(s); requires RootModel[T] migration)")

    if json_encoders_count > 0:
        reasons.append(f"Custom `json_encoders` detected ({json_encoders_count} site(s); requires field_serializer/PlainSerializer migration)")

    if basesettings_count > 0:
        reasons.append(f"BaseSettings detected ({basesettings_count} site(s); requires pydantic-settings migration)")

    if genericmodel_count > 0:
        reasons.append(f"GenericModel detected ({genericmodel_count} site(s); requires BaseModel + Generic[T] migration)")

    # Structural metrics
    raw_score = sum(_usage_risk_weight(u)[0] for u in usages)

    decorator_count = sum(1 for u in usages if u.usage_type == "decorator" and not _is_validator_usage(u))
    base_class_count = sum(1 for u in usages if u.usage_type == "base_class" and not (_is_basesettings_usage(u) or _is_genericmodel_usage(u)))
    special_field_count = sum(1 for u in usages if u.usage_type == "special_field" and not _is_root_usage(u))
    config_attr_count = sum(1 for u in usages if u.usage_type == "config_attr" and not _is_json_encoders_usage(u))

    has_cross_boundary = any("cross" in u.symbol_name.lower() for u in usages)
    has_heavy_decorators = decorator_count >= 2
    has_metaclass_or_config = config_attr_count > 0 or special_field_count > 0

    if base_class_count > 0:
        reasons.append(f"Standard model inheritance ({base_class_count} class definition(s))")

    if has_heavy_decorators:
        raw_score += 3 + (decorator_count * 2)
        reasons.append(f"Multiple decorators ({decorator_count}) modifying function/class behavior in scope")

    if has_cross_boundary:
        raw_score += 5
        reasons.append("Cross-field or root-level validation/lifecycle hooks present")

    if has_metaclass_or_config:
        raw_score += 2
        reasons.append("Inner configuration classes or custom special attributes detected")

    # Decide normalized score (1 to 10)
    # has_inheritance removed from is_complex so standard BaseModel subclasses route to Super
    is_complex = has_high_risk or has_heavy_decorators or has_cross_boundary

    if not is_complex:
        normalized_score = min(5, max(1, (raw_score + 1) // 2))
        reasons.append("Deterministic syntactic deprecation pattern (safe for fast Nemotron Super execution)")
        assigned_tier = "NEMOTRON_SUPER"
        target_model = NEMOTRON_SUPER_MODEL
    else:
        normalized_score = min(10, max(6, 5 + (raw_score // 5)))
        assigned_tier = "NEMOTRON_ULTRA"
        target_model = NEMOTRON_ULTRA_MODEL

    return raw_score, normalized_score, assigned_tier, target_model, reasons


def calculate_cost_savings(
    super_count: int,
    ultra_count: int,
    est_input_tokens_per_file: int = 800,
    est_output_tokens_per_file: int = 450,
) -> float:
    """
    Calculates percentage token cost saved by routing simple files to Nemotron Super
    ($0.13 in / $0.40 out per 1M) vs running 100% of files through Nemotron 3 Ultra ($0.60 in / $1.80 out per 1M).
    """
    total = super_count + ultra_count
    if total == 0:
        return 0.0

    super_call_cost = (
        (est_input_tokens_per_file / 1_000_000) * PRICE_PER_1M_SUPER_INPUT
        + (est_output_tokens_per_file / 1_000_000) * PRICE_PER_1M_SUPER_OUTPUT
    )
    ultra_call_cost = (
        (est_input_tokens_per_file / 1_000_000) * PRICE_PER_1M_ULTRA_INPUT
        + (est_output_tokens_per_file / 1_000_000) * PRICE_PER_1M_ULTRA_OUTPUT
    )

    all_ultra_cost = total * ultra_call_cost
    smart_routed_cost = (super_count * super_call_cost) + (ultra_count * ultra_call_cost)
    savings_pct = ((all_ultra_cost - smart_routed_cost) / all_ultra_cost) * 100.0
    return round(savings_pct, 1)