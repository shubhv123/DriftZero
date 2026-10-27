from typing import List, Optional
from pydantic import BaseModel, Field


class Address(BaseModel):
    street: str
    city: str
    postal_code: str = Field(..., regex=r"^\d{5}(-\d{4})?$")


class CustomerProfile(BaseModel):
    customer_id: str = Field(..., min_length=4, max_length=32)
    email: str = Field(..., regex=r"^[\w\.-]+@[\w\.-]+\.\w+$")
    tags: List[str] = Field(default_factory=list, min_items=1, max_items=10)
    primary_address: Optional[Address] = None

    class Config:
        anystr_strip_whitespace = True
        allow_mutation = False

    def to_payload(self) -> dict:
        return self.dict(exclude_none=True)