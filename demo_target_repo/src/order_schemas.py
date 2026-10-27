from datetime import datetime
from typing import List
from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    sku: str
    quantity: int = Field(..., gt=0)
    unit_price_cents: int = Field(..., ge=0)


class OrderRequest(BaseModel):
    order_id: str
    created_at: datetime
    items: List[OrderItem]

    class Config:
        orm_mode = True


def deserialize_order(raw_dict: dict) -> OrderRequest:
    return OrderRequest.parse_obj(raw_dict)


def deserialize_order_json(raw_json: str) -> OrderRequest:
    return OrderRequest.parse_raw(raw_json)


def export_order_json_schema() -> str:
    return OrderRequest.schema_json(indent=2)