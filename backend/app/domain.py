from typing import Literal
from datetime import datetime
from pydantic import BaseModel, Field, model_validator

Agent = Literal["Manager", "Market Scout", "PC Builder", "Market Analyst"]
Status = Literal["idle", "working", "completed", "error"]
CATEGORIES = ("CPU", "Motherboard", "GPU", "RAM", "Storage", "PSU", "Case")


class BuildRequest(BaseModel):
    max_cost: int | None = Field(default=None, ge=1000, le=500000)
    target_margin: float | None = Field(default=None, ge=0, le=80)
    minimum_target_margin: float | None = Field(default=None, ge=0, le=80)
    minimum_margin_pct: float | None = Field(default=None, ge=0, le=80)
    use_case: Literal[
        "gaming", "office", "workstation",
        "1080p gaming", "1440p gaming", "Office & productivity",
    ] = "gaming"
    target_resolution: Literal["1080p", "1440p", "4k"] | None = None
    preferred_cpu: str | None = Field(default=None, max_length=100)
    preferred_gpu: str | None = Field(default=None, max_length=100)
    preferred_cpu_brand: str | None = Field(default=None, max_length=50)
    preferred_gpu_vendor: str | None = Field(default=None, max_length=50)
    required_ram_gb: int | None = Field(default=None, ge=4, le=512)
    required_storage_gb: int | None = Field(default=None, ge=128, le=16384)
    target_selling_price: int | None = Field(default=None, ge=10000, le=1000000)
    target_sell_price: int | None = Field(default=None, ge=10000, le=1000000)

    @model_validator(mode="after")
    def validate_business_constraints(self):
        for field in ("preferred_cpu", "preferred_gpu", "preferred_cpu_brand", "preferred_gpu_vendor"):
            value = getattr(self, field)
            if value is not None:
                setattr(self, field, value.strip() or None)
        if self.target_selling_price is not None and self.target_sell_price is not None:
            if self.target_selling_price != self.target_sell_price:
                raise ValueError("target_sell_price and target_selling_price must match")
        return self

    @property
    def minimum_margin(self) -> float:
        for value in (self.minimum_margin_pct, self.minimum_target_margin, self.target_margin):
            if value is not None:
                return value
        return 0

    @property
    def selling_price_target(self) -> int | None:
        return self.target_sell_price or self.target_selling_price


class ProductIdentity(BaseModel):
    category: str
    manufacturer: str | None = None
    product_family: str | None = None
    model_number: str | None = None
    variant: str | None = None
    color: str | None = None
    gpu_vendor: str | None = None
    chipset: str | None = None
    vram_gb: int | None = None
    memory_type: str | None = None
    memory_token: str | None = None
    cpu_socket: str | None = None
    ram_type: str | None = None
    capacity_gb: int | None = None
    kit_count: int | None = None
    capacity_per_stick_gb: int | None = None
    speed_mhz: int | None = None
    ecc: bool | None = None
    interface: str | None = None
    wattage: int | None = None
    efficiency_rating: str | None = None
    form_factor: str | None = None
    packaging: Literal["boxed", "tray"] | None = None
    bundle: bool | None = None
    cpu_suffix: str | None = None
    canonical_name: str
    identity_key: str


class ProductMatchResult(BaseModel):
    matched: bool
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    conflicts: list[str] = Field(default_factory=list)
    possible_match: bool = False
    matching_fields: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)


class Listing(BaseModel):
    sku: str
    name: str
    category: str
    retailer: str
    price: int
    regular_price: int | None = None
    shipping: int | None = 0
    in_stock: bool | None = True
    specs: dict[str, str | int]
    source: str | None = None
    brand: str | None = None
    model: str | None = None
    title: str | None = None
    url: str | None = None
    collected_at: datetime | None = None
    product_identity: ProductIdentity | None = None
    data_mode: Literal["live"] = "live"
    canonical_product_id: str | None = None
    effective_price: int | None = None
    shipping_known: bool = True
    freshness: Literal["fresh", "stale", "expired", "unknown"] = "unknown"


class Component(BaseModel):
    listing: Listing
    delivered_price: int
    alternatives: list[dict]
    best_sources: list[str] = Field(default_factory=list)
    suspicious: bool = False
    suspicious_reasons: list[str] = Field(default_factory=list)


class CompatibilityCheck(BaseModel):
    rule: str
    status: Literal["pass", "fail", "unknown"]
    details: str
    critical: bool = False


class CompatibilityResult(BaseModel):
    compatible: bool
    checks: list[CompatibilityCheck]


class BuildCandidate(BaseModel):
    rank: int
    name: str
    badges: list[str] = Field(default_factory=list)
    components: list[Component]
    total_parts_cost: int
    shipping_cost: int
    assembly_cost: int
    packaging_cost: int
    warranty_reserve: int
    landed_cost: int
    target_sell_price: int | None = None
    required_sell_price: int
    total_cost: int
    suggested_selling_price: int
    estimated_profit: int
    actual_margin: float
    performance_score: float
    value_score: float
    ranking_score: float
    scores: dict[str, float]
    compatibility: CompatibilityResult
    compatibility_checks: list[str]
    rationale: str


class Recommendation(BaseModel):
    components: list[Component]
    total_cost: int
    suggested_selling_price: int
    estimated_profit: int
    actual_margin: float
    compatibility_checks: list[str]
    rationale: str
    candidates: list[BuildCandidate] = Field(default_factory=list)
