from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, validator, root_validator


class BaseFinancialModel(BaseModel):
    transaction_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {
            Decimal: lambda v: str(round(v, 2)),
            datetime: lambda v: v.isoformat(),
        }
        validate_assignment = True


class HighRiskWireTransfer(BaseFinancialModel):
    sender_country: str = Field(..., min_length=2, max_length=2)
    receiver_country: str = Field(..., min_length=2, max_length=2)
    amount_usd: Decimal = Field(..., gt=Decimal("0.00"))
    fx_rate: Optional[Decimal] = None
    requires_manual_compliance_review: bool = False
    compliance_notes: Optional[str] = None

    @root_validator(pre=True)
    def normalize_country_codes_and_amounts(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(values, dict):
            for key in ("sender_country", "receiver_country"):
                if key in values and isinstance(values[key], str):
                    values[key] = values[key].strip().upper()
            if "amount_usd" in values and isinstance(values["amount_usd"], (int, float)):
                values["amount_usd"] = Decimal(str(values["amount_usd"]))
        return values

    @validator("fx_rate", always=True)
    def validate_cross_border_fx_rate(cls, v: Optional[Decimal], values: Dict[str, Any]) -> Decimal:
        sender = values.get("sender_country")
        receiver = values.get("receiver_country")
        if sender and receiver and sender != receiver:
            if v is None or v <= Decimal("0"):
                raise ValueError("Cross-border transfers require a positive fx_rate")
            return v
        return v or Decimal("1.0000")

    @root_validator(skip_on_failure=True)
    def enforce_high_value_compliance_policy(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        amount = values.get("amount_usd", Decimal("0"))
        sender = values.get("sender_country")
        receiver = values.get("receiver_country")

        if amount >= Decimal("10000.00") or (sender != receiver and amount >= Decimal("3000.00")):
            values["requires_manual_compliance_review"] = True
            if not values.get("compliance_notes"):
                values["compliance_notes"] = "Auto-flagged by cross-border risk threshold"
        return values

    def export_compliance_snapshot(self) -> Dict[str, Any]:
        raw = self.dict(exclude_none=False)
        raw["serialized_json"] = self.json()
        return raw