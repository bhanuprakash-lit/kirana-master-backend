"""Pydantic response/request models for the vision API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SessionAccepted(BaseModel):
    session_id: int
    store_id: int
    session_type: str
    status: str  # 'pending' — analysis runs in the background


class SessionSummary(BaseModel):
    session_id: int
    session_type: str
    session_date: str
    status: str
    total_skus: int
    total_units: int
    unknown_count: int
    created_at: Optional[str] = None


class VisionItemOut(BaseModel):
    item_id: int
    sku_id: Optional[str] = None
    product_id: Optional[int] = None
    display_name: Optional[str] = None
    gemini_name: str
    visible_text: Optional[str] = None
    count: int
    match_score: float
    is_unknown: bool
    bbox_json: Optional[str] = None
    image_index: int = 0
    corrected_product_id: Optional[int] = None


class SalesDeltaItem(BaseModel):
    product_id: int
    display_name: str
    morning_count: int
    evening_count: int
    sold: int


class SalesResponse(BaseModel):
    store_id: int
    session_date: str
    items: list[SalesDeltaItem]
    total_sold: int


class CorrectionInput(BaseModel):
    corrected_product_id: Optional[int] = None  # null clears the correction


# ── Sale-area counter (on-device) ────────────────────────────────────────────

class CounterItemIn(BaseModel):
    class_name: str                        # on-device model label
    qty: int = 1
    avg_confidence: Optional[float] = None  # mean detection confidence on device


class CounterSyncInput(BaseModel):
    client_uid: str                         # on-device UUID → idempotent upsert
    session_date: Optional[str] = None      # YYYY-MM-DD, default server today
    device_label: Optional[str] = None
    started_at: Optional[str] = None        # ISO8601
    ended_at: Optional[str] = None
    items: list[CounterItemIn] = []


class CounterSyncResponse(BaseModel):
    session_id: int
    session_date: str
    total_units: int
    total_skus: int
    unknown_count: int


class CounterSummaryItem(BaseModel):
    product_id: Optional[int] = None
    class_name: str
    display_name: str
    qty: int
    is_unknown: bool


class CounterSummaryResponse(BaseModel):
    store_id: int
    session_date: str
    items: list[CounterSummaryItem]
    total_units: int
    total_skus: int


# ── Bulk stock-in / onboarding ────────────────────────────────────────────────

class OnboardingCommitItem(BaseModel):
    product_id: int          # matched, owner-corrected, or owner-picked
    quantity: int            # opening stock the owner confirmed


class OnboardingCommitInput(BaseModel):
    items: list[OnboardingCommitItem] = []
    # False (onboarding an empty store): SET stock to the reviewed count.
    # True (existing store restocking by camera): ADD the reviewed count to current stock.
    add_to_existing: bool = False


class OnboardingCommitResponse(BaseModel):
    session_id: int
    products_added: int
    total_quantity: int
    skipped: int
