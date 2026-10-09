from typing import Literal
from pydantic import BaseModel, Field

Agent = Literal["Manager", "Market Scout", "PC Builder", "Market Analyst"]
Status = Literal["idle", "working", "completed", "error"]
CATEGORIES = ("CPU", "Motherboard", "GPU", "RAM", "Storage", "PSU", "Case")


class BuildRequest(BaseModel):
    max_cost: int = Field(ge=10000, le=500000)
    target_margin: float = Field(ge=0, le=80)
    use_case: Literal["1080p gaming", "1440p gaming", "Office & productivity"]


class Listing(BaseModel):
    sku: str
    name: str
    category: str
    retailer: str
    price: int
    shipping: int = 0
    in_stock: bool = True
    specs: dict[str, str | int]


class Component(BaseModel):
    listing: Listing
    delivered_price: int
    alternatives: list[dict]


class Recommendation(BaseModel):
    components: list[Component]
    total_cost: int
    suggested_selling_price: int
    estimated_profit: int
    actual_margin: float
    compatibility_checks: list[str]
    rationale: str
