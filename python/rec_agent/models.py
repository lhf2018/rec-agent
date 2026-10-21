from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TriggerType(str, Enum):
    impression = "impression"
    refresh = "refresh"
    location_change = "location_change"
    time_slot = "time_slot"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    degraded = "degraded"
    suppressed = "suppressed"
    skipped = "skipped"
    empty = "empty"
    failed = "failed"


class RouteMode(str, Enum):
    skip = "SKIP"
    fallback = "FALLBACK"
    rule_engine = "RULE_ENGINE"
    direct = "DIRECT"
    react = "REACT"


class OpportunityResult(str, Enum):
    recommend_now = "recommend_now"
    suppress = "suppress"
    skip = "skip"


class Location(BaseModel):
    lng: float
    lat: float
    district: str = ""


class Scene(BaseModel):
    scene_id: str
    trigger: TriggerType
    channel: str = "nearby"
    occurred_at: str
    location: Location
    radius_m: int = 3000
    meal_period: str = "lunch"


class Product(BaseModel):
    product_id: str
    merchant_id: str
    name: str
    price: float
    in_stock: bool = True
    dietary_tags: list[str] = Field(default_factory=list)
    category: str = ""
    description: str = ""


class Merchant(BaseModel):
    merchant_id: str
    name: str
    category: str
    tags: list[str] = Field(default_factory=list)
    lng: float
    lat: float
    is_open: bool = True
    delivery_radius_m: int = 5000
    avg_price: float = 30.0
    status: str = "online"
    score: float = 4.5
    products: list[Product] = Field(default_factory=list)


class ShortTermIntent(BaseModel):
    topic: str
    confidence: float
    updated_at: str
    expires_at: str
    source: str = "browse_burst"


class HardConstraints(BaseModel):
    dietary_forbid: list[str] = Field(default_factory=list)
    blocked_merchants: list[str] = Field(default_factory=list)
    not_interested_merchants: list[str] = Field(default_factory=list)
    budget_min: float | None = None
    budget_max: float | None = None


class RuntimeSignals(BaseModel):
    """Store-backed dynamic signals injected at snapshot build time."""

    delivery_count_in_window: int = 0
    last_success_at: str = ""
    recent_shown_merchants: list[str] = Field(default_factory=list)
    recent_shown_categories: list[str] = Field(default_factory=list)
    scene_seen_recently: bool = False
    exposure_merchant_ids: list[str] = Field(default_factory=list)
    dynamic_not_interested: list[str] = Field(default_factory=list)


class UserSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    tenant_id: str = "t_demo"
    snapshot_version: str = "v1"
    built_at: str = ""
    city: str = ""
    member_level: str = "normal"
    lifecycle: str = "active"
    home_district: str = ""
    long_term_categories: list[str] = Field(default_factory=list)
    avg_order_min: float = 0
    avg_order_max: float = 999
    frequent_merchants: list[str] = Field(default_factory=list)
    short_term: list[ShortTermIntent] = Field(default_factory=list)
    hard: HardConstraints = Field(default_factory=HardConstraints)
    preferred_tags: list[str] = Field(default_factory=list)
    allow_personalized: bool = True
    category_opt_out: list[str] = Field(default_factory=list)
    # Seed-only conversion markers (static). Runtime delivery/scene live in runtime.
    recent_conversions: list[str] = Field(default_factory=list)
    runtime: RuntimeSignals = Field(default_factory=RuntimeSignals)


class CandidateProduct(BaseModel):
    product_id: str
    name: str
    price: float
    in_stock: bool
    dietary_tags: list[str] = Field(default_factory=list)
    score: float = 0.0


class CandidateMerchant(BaseModel):
    merchant_id: str
    name: str
    category: str
    avg_price: float
    distance_m: float
    is_open: bool
    score: float
    tags: list[str] = Field(default_factory=list)
    products: list[CandidateProduct] = Field(default_factory=list)
    recall_sources: list[str] = Field(default_factory=list)
    evidence_id: str = ""
    provenance: str = "primary"
    observed_at: str = ""
    expires_at: str = ""


class DroppedItem(BaseModel):
    merchant_id: str
    reason: str


class EngineOutput(BaseModel):
    candidates: list[CandidateMerchant]
    baseline: list[CandidateMerchant]
    dropped: list[DroppedItem]
    candidate_set_version: str
    facts_complete: bool = True
    intent_conflict: bool = False
    need_freshness: bool = False
    need_compare: bool = False
    soft_rerank: bool = False
    supplement_needed: bool = False
    filter_stats: dict[str, int] = Field(default_factory=dict)


class RecommendedProduct(BaseModel):
    product_id: str
    name: str = ""
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class RecommendedMerchant(BaseModel):
    merchant_id: str
    name: str = ""
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    products: list[RecommendedProduct] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    task_id: str
    status: TaskStatus
    route_mode: RouteMode | None = None
    opportunity_result: OpportunityResult | None = None
    decision_reason_code: str | None = None
    fallback_reason: str | None = None
    source: str | None = None
    merchants: list[RecommendedMerchant] = Field(default_factory=list)
    self_score: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class CreateTaskRequest(BaseModel):
    tenant_id: str = "t_demo"
    user_id: str
    scene: Scene


class FeedbackRequest(BaseModel):
    event_type: str
    merchant_id: str | None = None
    event_time: str | None = None
