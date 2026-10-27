# Calculates real-time token usage, latency, and % cost saved
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from backend.config import (
    PRICE_PER_1M_SUPER_INPUT,
    PRICE_PER_1M_SUPER_OUTPUT,
    PRICE_PER_1M_ULTRA_INPUT,
    PRICE_PER_1M_ULTRA_OUTPUT,
)
from backend.schemas.migration_models import TelemetryEvent


@dataclass
class PipelineTelemetrySummary:
    total_events: int = 0
    super_calls: int = 0
    ultra_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    total_actual_cost_usd: float = 0.0
    total_hypothetical_ultra_cost_usd: float = 0.0
    total_cost_saved_usd: float = 0.0
    savings_percentage: float = 0.0
    events: List[TelemetryEvent] = field(default_factory=list)

    def record_event(self, event: TelemetryEvent) -> None:
        self.events.append(event)
        self.total_events += 1
        if "SUPER" in event.assigned_tier.upper():
            self.super_calls += 1
        else:
            self.ultra_calls += 1
        self.total_prompt_tokens += event.prompt_tokens
        self.total_completion_tokens += event.completion_tokens
        self.total_tokens += event.prompt_tokens + event.completion_tokens
        self.total_latency_ms += event.latency_ms
        self.total_actual_cost_usd += event.actual_cost_usd
        self.total_hypothetical_ultra_cost_usd += event.hypothetical_ultra_cost_usd
        self.total_cost_saved_usd += event.cost_saved_usd
        if self.total_hypothetical_ultra_cost_usd > 0:
            self.savings_percentage = round(
                ((self.total_hypothetical_ultra_cost_usd - self.total_actual_cost_usd)
                 / self.total_hypothetical_ultra_cost_usd) * 100.0,
                1,
            )

    def to_dict(self) -> Dict:
        return {
            "total_events": self.total_events,
            "super_calls": self.super_calls,
            "ultra_calls": self.ultra_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "total_actual_cost_usd": round(self.total_actual_cost_usd, 6),
            "total_hypothetical_ultra_cost_usd": round(self.total_hypothetical_ultra_cost_usd, 6),
            "total_cost_saved_usd": round(self.total_cost_saved_usd, 6),
            "savings_percentage": self.savings_percentage,
        }
