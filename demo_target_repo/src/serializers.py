from pydantic import BaseModel
from .order_schemas import OrderRequest


class AuditRecord(BaseModel):
    event_name: str
    actor_id: str
    payload_snapshot: dict


def serialize_for_kafka(order: OrderRequest) -> str:
    return order.json(by_alias=True, exclude_unset=True)


def clone_order_with_new_id(order: OrderRequest, new_id: str) -> OrderRequest:
    return order.copy(update={"order_id": new_id}, deep=True)


def duplicate_dict_payload(raw_meta: dict) -> dict:
    return raw_meta.copy()