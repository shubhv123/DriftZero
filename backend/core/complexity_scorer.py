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


def score_and_route_file(usages: List[SymbolUsage]) -> Tuple[int, int, str, str, List[str]]:
    """
    Computes universal AST complexity, normalizes to a 1-10 score, and assigns the Nebius Token Factory tier.
    Rule:
      - Normalized Score <= 5 -> NEMOTRON_SUPER (Fast syntactic replacements, simple method & import renames)
      - Normalized Score > 5  -> NEMOTRON_ULTRA (Deep structural, architectural, or multi-field semantic refactoring)
    """
    if not usages:
        return 0, 1, "NEMOTRON_SUPER", NEMOTRON_SUPER_MODEL, ["No symbol usages detected"]

    raw_score = sum(u.complexity_weight for u in usages)
    reasons: List[str] = []

    # Universal structural metrics
    decorator_count = sum(1 for u in usages if u.usage_type == "decorator")
    base_class_count = sum(1 for u in usages if u.usage_type == "base_class")
    special_field_count = sum(1 for u in usages if u.usage_type == "special_field")
    config_attr_count = sum(1 for u in usages if u.usage_type == "config_attr")
    method_call_count = sum(1 for u in usages if u.usage_type == "method_call")

    # High complexity indicators
    has_cross_boundary = any("cross" in u.symbol_name.lower() or "root" in u.symbol_name.lower() for u in usages)
    has_inheritance = base_class_count > 0
    has_heavy_decorators = decorator_count >= 2
    has_metaclass_or_config = config_attr_count > 0 or special_field_count > 0

    if has_inheritance:
        raw_score += 4 * base_class_count
        reasons.append(f"Subclasses library base types ({base_class_count} inheritance sites; requires signature/MRO alignment)")

    if has_heavy_decorators:
        raw_score += 3 + (decorator_count * 2)
        reasons.append(f"Multiple decorators ({decorator_count}) modifying function/class behavior in scope")

    if has_cross_boundary:
        raw_score += 5
        reasons.append("Cross-field or root-level validation/lifecycle hooks present")

    if has_metaclass_or_config:
        raw_score += 3
        reasons.append("Inner configuration classes or custom special attributes detected")

    # Decide normalized score (1 to 10)
    is_complex = has_inheritance or has_heavy_decorators or has_cross_boundary or raw_score >= 12

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