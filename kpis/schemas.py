"""Pydantic response schemas for all 14 KPI endpoints."""
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel, field_validator
import math


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class Trend(BaseModel):
    direction: str              # "up" | "down" | "stable"
    pct_change: Optional[float] = None
    current_value: Optional[float] = None
    previous_value: Optional[float] = None
    interpretation: str = ""


class KPITarget(BaseModel):
    raw: str                    # e.g. "+15% to +25%"
    low_pct: float
    high_pct: float
    description: str = ""


class BaseKPIResponse(BaseModel):
    kpi_id: str
    kpi_name: str
    store_id: int
    store_name: str
    period_days: int
    period_from: date
    period_to: date
    target: KPITarget
    trend: Trend
    last_updated: datetime
    ml_insights: Optional[dict[str, Any]] = None


# ── 1. Repeat Customer Frequency ──────────────────────────────────────────────
class RepeatCustomerSegment(BaseModel):
    label: str
    customer_count: int
    avg_basket: float
    avg_visit_interval_days: float


class RepeatCustomerKPI(BaseKPIResponse):
    total_customers: int
    repeat_customer_count: int
    repeat_rate_pct: float
    avg_visit_interval_days: float
    median_visit_interval_days: float
    at_risk_count: int          # haven't visited in >2× avg interval
    churned_count: int          # >60 days since last visit
    segments: list[RepeatCustomerSegment]


# ── 2. Category Mix Optimization ──────────────────────────────────────────────
class CategoryMixRow(BaseModel):
    category_id: int
    category_name: str
    revenue: float
    revenue_share_pct: float
    margin_pct: float
    avg_units_per_day: float
    bcg_quadrant: str           # "star" | "cash_cow" | "question_mark" | "dog"


class CategoryMixKPI(BaseKPIResponse):
    total_revenue: float
    overall_margin_pct: float
    category_count: int
    mix_score: float            # 0-100: how well-diversified the revenue mix is
    categories: list[CategoryMixRow]
    top_opportunity: str


# ── 3. Digital Payment Adoption ───────────────────────────────────────────────
class PaymentMethodBreakdown(BaseModel):
    method: str
    count: int
    amount: float
    share_pct: float


class DigitalPaymentKPI(BaseKPIResponse):
    digital_pct: float
    cash_pct: float
    total_transactions: int
    total_amount: float
    by_method: list[PaymentMethodBreakdown]
    weekly_trend: list[dict[str, Any]]


# ── 4. New Product Trial Success ──────────────────────────────────────────────
class NewProductRow(BaseModel):
    product_id: int
    product_name: str
    category_name: str
    days_since_launch: int
    units_sold_30d: int
    revenue_30d: float
    success_label: str          # "hit" | "average" | "slow"
    predicted_success_prob: Optional[float] = None


class NewProductTrialKPI(BaseKPIResponse):
    trial_window_days: int
    new_products_count: int
    success_rate_pct: float
    avg_units_sold_30d: float
    products: list[NewProductRow]


# ── 5. Cross-Category Basket ──────────────────────────────────────────────────
class CategoryPairRow(BaseModel):
    category_a: str
    category_b: str
    co_occurrences: int
    lift: float


class CrossCategoryKPI(BaseKPIResponse):
    total_orders: int
    multi_category_orders: int
    multi_category_pct: float
    avg_categories_per_order: float
    orders_3plus_cat_pct: float
    top_pairs: list[CategoryPairRow]


# ── 6. WhatsApp Order Conversion ──────────────────────────────────────────────
class WhatsAppConversionKPI(BaseKPIResponse):
    total_sessions: int
    active_sessions: int
    language_breakdown: dict[str, int]
    state_breakdown: dict[str, int]
    total_messages_sent: int
    total_messages_received: int
    avg_messages_per_session: float
    conversion_proxy_pct: float  # sessions that reached sales/analytics menu


# ── 7. Morning Stock Readiness ────────────────────────────────────────────────
class StockReadinessRow(BaseModel):
    product_id: int
    product_name: str
    category_name: str
    current_stock: int
    avg_daily_demand: float
    days_of_cover: float
    readiness_status: str   # "ready" | "low" | "critical"
    stockout_risk_7d: Optional[float] = None


class MorningStockKPI(BaseKPIResponse):
    readiness_score: float          # 0-100
    ready_count: int
    low_count: int
    critical_count: int
    total_fast_movers: int
    skus: list[StockReadinessRow]


# ── 8. Procurement Cost Savings ───────────────────────────────────────────────
class ProcurementSupplierRow(BaseModel):
    supplier_id: int
    supplier_name: str
    total_purchased_value: float
    standard_value: float
    actual_savings: float
    savings_pct: float


class ProcurementCostKPI(BaseKPIResponse):
    total_purchased_value: float
    total_standard_value: float
    net_savings: float
    savings_pct: float
    overpay_count: int
    underpay_count: int
    by_supplier: list[ProcurementSupplierRow]


# ── 9. Inventory Holding Cost ─────────────────────────────────────────────────
class HoldingCostRow(BaseModel):
    category_name: str
    avg_stock_value: float
    holding_cost: float
    holding_cost_pct: float
    excess_units: int
    excess_value: float


class InventoryHoldingKPI(BaseKPIResponse):
    total_stock_value: float
    total_holding_cost: float
    holding_cost_pct_of_revenue: float
    excess_inventory_value: float
    optimal_stock_value: float
    by_category: list[HoldingCostRow]


# ── 10. Distributor Terms Leverage ────────────────────────────────────────────
class DistributorRow(BaseModel):
    supplier_id: int
    supplier_name: str
    total_orders: int
    avg_actual_cost: float
    avg_standard_cost: float
    price_variance_pct: float
    reliability_score: float        # 0-100
    lead_time_accuracy_pct: float
    recommendation: str


class DistributorTermsKPI(BaseKPIResponse):
    total_suppliers: int
    best_supplier_id: int
    best_supplier_name: str
    total_overpay_opportunity: float
    by_supplier: list[DistributorRow]


# ── 11. Perishable Freshness Waste ────────────────────────────────────────────
class PerishableRow(BaseModel):
    product_id: int
    product_name: str
    category_name: str
    current_stock: int
    days_stock_unchanged: int
    daily_avg_sales: float
    days_of_cover: float
    waste_risk: str             # "high" | "medium" | "low"
    estimated_waste_value: float


class PerishableWasteKPI(BaseKPIResponse):
    total_perishable_skus: int
    high_risk_count: int
    medium_risk_count: int
    total_at_risk_value: float
    waste_rate_pct: float
    items: list[PerishableRow]


# ── 12. Pilferage / Shrinkage Loss ────────────────────────────────────────────
class ShrinkageRow(BaseModel):
    product_id: int
    product_name: str
    category_name: str
    opening_stock: int
    purchases: int
    sales: int
    expected_closing: int
    actual_closing: int
    shrinkage_units: int
    shrinkage_value: float
    anomaly_score: Optional[float] = None
    flagged: bool = False


class ShrinkageKPI(BaseKPIResponse):
    total_shrinkage_units: int
    total_shrinkage_value: float
    shrinkage_rate_pct: float
    flagged_skus_count: int
    items: list[ShrinkageRow]


# ── 13. Reorder Lead-Time Accuracy ────────────────────────────────────────────
class LeadTimeSupplierRow(BaseModel):
    supplier_id: int
    supplier_name: str
    order_count: int
    avg_expected_days: float
    avg_actual_days: float
    on_time_pct: float
    mape: float                 # Mean Absolute Percentage Error
    reliability_score: float    # 0-100


class ReorderLeadTimeKPI(BaseKPIResponse):
    total_purchase_orders: int
    avg_expected_days: float
    avg_actual_days: float
    on_time_rate_pct: float
    overall_accuracy_pct: float
    by_supplier: list[LeadTimeSupplierRow]


# ── 14. Cash Leakage / Billing Misses ────────────────────────────────────────
class CashLeakageRow(BaseModel):
    order_id: int
    order_date: datetime
    order_total: float
    payment_amount: Optional[float]
    gap: float
    issue_type: str             # "unpaid" | "underpaid" | "overpaid"


class CashLeakageKPI(BaseKPIResponse):
    total_orders: int
    clean_orders: int
    problematic_orders: int
    total_leakage_value: float
    leakage_rate_pct: float
    unpaid_count: int
    mismatch_count: int
    flagged_orders: list[CashLeakageRow]
