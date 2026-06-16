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
