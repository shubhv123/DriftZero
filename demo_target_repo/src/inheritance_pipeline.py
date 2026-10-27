from typing import Generic, TypeVar, List, Dict, Any
from pydantic import BaseModel, validator, root_validator, parse_obj_as
from pydantic.generics import GenericModel

PayloadT = TypeVar("PayloadT")


class BatchMetricVector(BaseModel):
    __root__: List[float]

    @validator("__root__", each_item=True)
    def check_probability_bounds(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"Metric score {v} must be between 0.0 and 1.0")
        return round(v, 4)

    def mean_score(self) -> float:
        items = self.__root__
        return sum(items) / len(items) if items else 0.0


class EnvelopeContext(GenericModel, Generic[PayloadT]):
    trace_id: str
    attempt_count: int = 1
    payload: PayloadT
    scores: BatchMetricVector

    class Config:
        arbitrary_types_allowed = True
        copy_on_model_validation = "deep"

    @validator("attempt_count")
    def check_attempt_limit(cls, v: int, field) -> int:
        if v > 5:
            raise ValueError(f"Field {field.name} exceeded max retry limit of 5")
        return v

    @root_validator
    def verify_payload_integrity(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        trace = values.get("trace_id", "")
        if trace.startswith("ERR_") and values.get("attempt_count", 1) == 1:
            values["attempt_count"] = 2
        return values


def parse_envelope_batch(raw_list: List[Dict[str, Any]]) -> List[EnvelopeContext[Dict[str, Any]]]:
    parsed = parse_obj_as(List[EnvelopeContext[Dict[str, Any]]], raw_list)
    return parsed