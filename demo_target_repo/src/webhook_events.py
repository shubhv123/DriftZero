from typing import Dict, Any
from pydantic import BaseModel, Field


class StripeWebhookEvent(BaseModel):
    event_id: str = Field(..., alias="id")
    event_type: str = Field(..., alias="type")
    data_object: Dict[str, Any] = Field(default_factory=dict, alias="data")

    class Config:
        allow_population_by_field_name = True
        extra = "ignore"


def fast_unvalidated_webhook(raw_data: Dict[str, Any]) -> StripeWebhookEvent:
    return StripeWebhookEvent.construct(**raw_data)