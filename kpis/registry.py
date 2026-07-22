"""
KPI Master Registry — 46 KPIs from the spreadsheet's "KPI Master Database"
sheet, restricted to the verticals "Kirana Owner" and "Common (All Verticals)".

Each entry carries enough metadata for the frontend to render a card and
enough for the backend to know how to compute (or politely refuse to
compute) it. KPIs whose underlying data does not exist in lit_db are
flagged status="data_unavailable" with a clear `missing_data` field so the
UI can show a "needs setup" tile instead of a fake number.

Adding a new data source later? Implement the calculator, change `compute`
to point at it, and flip status to "ok". No UI change required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# from kpis import calculator as calc
from kpis import calculators as calc


# ─────────────────────────────────────────────────────────────────────────────
# Status values
# ─────────────────────────────────────────────────────────────────────────────
STATUS_OK              = "ok"               # implemented and computable
STATUS_DATA_UNAVAILABLE = "data_unavailable" # source not present in DB yet


@dataclass
class KPIDef:
    kpi_id: str                     # stable slug, e.g. "K_TL_1"
    spreadsheet_num: str            # the original "KPI #" cell value (1, "C1", etc.)
    name: str                       # display name
    vertical: str                   # "Kirana Owner" or "Common (All Verticals)"
    pl_category: str                # "Top Line" / "Bottom Line"
    theme: str                      # e.g. "Revenue & Growth"
    target: str
    baseline: str
    why: str
    ai_agent: str
    data_source: str
    perspective: str
    spreadsheet_status: str         # "Existing" / "NEW v5" / "New New"
    category: str = "Operations"
    # Implementation
    status: str = STATUS_DATA_UNAVAILABLE
    endpoint_slug: Optional[str] = None    # the URL slug (without /kirana/kpis/ prefix)
    compute: Optional[Callable] = None     # (engine, store_id, **kwargs) -> dict
    primary_field: Optional[str] = None    # field in the result that is "the number"
    missing_data: Optional[str] = None     # human-readable "what we still need"
    # F4 — vertical_codes this KPI applies to. Empty = all verticals (the common
    # set, e.g. the existing grocery/common KPIs). Vertical packs list their codes.
    verticals: list[str] = field(default_factory=list)

    def applies_to(self, vertical_code: str) -> bool:
        return not self.verticals or vertical_code in self.verticals


# ─────────────────────────────────────────────────────────────────────────────
# Helper to build a list cleanly
# ─────────────────────────────────────────────────────────────────────────────
def _ok(slug: str, fn: Callable, primary: str | None = None, **base) -> KPIDef:
    return KPIDef(status=STATUS_OK, endpoint_slug=slug, compute=fn,
                  primary_field=primary, **base)


def _missing(missing: str, **base) -> KPIDef:
    return KPIDef(status=STATUS_DATA_UNAVAILABLE,
                  missing_data=missing, **base)


def _soon(missing: str, **base) -> KPIDef:
    """A registered-but-not-yet-computable KPI (Guru gap). Hidden from stores by
    default; an admin can switch it on per vertical once data lands."""
    return KPIDef(status=STATUS_DATA_UNAVAILABLE, missing_data=missing, **base)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────
# _REGISTRY: list[KPIDef] = [
#     # ─── Kirana Owner — Top Line (16) ─────────────────────────────────────────
#     _ok("daily-revenue", calc.calc_daily_revenue, "total_revenue",
#         kpi_id="K_TL_1", spreadsheet_num="1", name="Daily Sales Revenue", category="Finance",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+10% to +18%", baseline="Current avg daily sales",
#         why="Owner's #1 metric — total daily takda (cash + digital).",
#         ai_agent="Retailer Asst · Smart Order", data_source="POS / manual billing",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("walkin-purchase", calc.calc_walkin_purchase, "conversion_pct",
#         kpi_id="K_TL_2", spreadsheet_num="2", name="Walk-in to Purchase %", category="Sales",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+8% to +15%", baseline="Current footfall vs bills",
#         why="How many entering actually buy — availability + service signal.",
#         ai_agent="Retailer Asst · Stock AI", data_source="POS, footfall",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("repeat-customer-frequency", calc.calc_repeat_customer, "repeat_rate_pct",
#         kpi_id="K_TL_3", spreadsheet_num="3", name="Repeat Customer Frequency", category="Sales",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+15% to +25%", baseline="Current repeat interval",
#         why="Loyal regulars = predictable revenue.",
#         ai_agent="Smart Order · Reminder AI", data_source="POS, WhatsApp",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("avg-basket-value", calc.calc_avg_basket_value, "avg_basket_value",
#         kpi_id="K_TL_4", spreadsheet_num="4", name="Avg Basket Value", category="Sales",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+5% to +12%", baseline="Current avg bill value",
#         why="More items per bill without extra footfall.",
#         ai_agent="Smart Order · Retailer Asst", data_source="POS, billing",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("high-margin-sales", calc.calc_high_margin_sales, "high_margin_pct",
#         kpi_id="K_TL_5", spreadsheet_num="5", name="High-Margin Item Sales %", category="Sales",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+5% to +10%", baseline="Current high-margin mix",
#         why="Revenue from items where owner earns most — shifts mix to fatter margins.",
#         ai_agent="Assortment AI · Pricing Agent", data_source="POS, margin data",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("scheme-capture", calc.calc_scheme_capture, "capture_pct",
#         kpi_id="K_TL_6", spreadsheet_num="6", name="Scheme Benefit Capture",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+15% to +25%", baseline="Current scheme utilization",
#         why="Owner misses 20-30% of eligible schemes — AI alerts ensure every rupee claimed.",
#         ai_agent="Scheme Alert · Retailer Asst", data_source="DMS, scheme data",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("category-mix", calc.calc_category_mix, "overall_margin_pct",
#         kpi_id="K_TL_7", spreadsheet_num="7", name="Category Mix Optimization",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+3% to +6% margin", baseline="Current category split",
#         why="Shift shelf space to high-demand, high-margin categories.",
#         ai_agent="Assortment AI · BI Copilot", data_source="POS, sales data",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("digital-payment-adoption", calc.calc_digital_payment, "digital_pct",
#         kpi_id="K_TL_8", spreadsheet_num="8", name="Digital Payment Adoption",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+15% to +25%", baseline="Current UPI/digital %",
#         why="Less cash risk, builds transaction data for AI.",
#         ai_agent="Retailer Asst · Payment AI", data_source="POS, UPI data",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("home-delivery", calc.calc_home_delivery, "delivery_pct",
#         kpi_id="K_TL_9", spreadsheet_num="9", name="Home Delivery Revenue %",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+8% to +15%", baseline="Current delivery order %",
#         why="WhatsApp/phone delivery = incremental revenue without extra rent.",
#         ai_agent="Order Agent · Retailer Asst", data_source="POS, WhatsApp orders",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("new-product-trial", calc.calc_new_product_trial, "success_rate_pct",
#         kpi_id="K_TL_10", spreadsheet_num="10", name="New Product Trial Success",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+10% to +20%", baseline="Current new SKU sell-through",
#         why="Does new SKU sell in 30 days? Avoids dead stock from failed launches.",
#         ai_agent="Inventory AI · Assortment AI", data_source="POS, DMS",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("cross-category-basket", calc.calc_cross_category_basket, "multi_category_pct",
#         kpi_id="K_TL_11", spreadsheet_num="11", name="Cross-Category Basket %",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+5% to +10%", baseline="Current multi-category bills",
#         why="Customers buying 3+ categories = stickier, harder to lose.",
#         ai_agent="Smart Order · Retailer Asst", data_source="POS, billing",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("festive-uplift", calc.calc_festive_uplift, "uplift_pct",
#         kpi_id="K_TL_12", spreadsheet_num="12", name="Festive / Seasonal Uplift",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+20% to +35%", baseline="Current seasonal capture",
#         why="Diwali, Eid, harvest drive 30-50% spikes — AI pre-stocks.",
#         ai_agent="Forecast · Seasonal AI", data_source="POS, calendar, DMS",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("private-label", calc.calc_private_label, "private_label_pct",
#         kpi_id="K_TL_13", spreadsheet_num="13", name="Private Label / Store Brand %",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+3% to +8%", baseline="Current own-brand revenue",
#         why="Own brand/repackaged staples carry 40-60% margin vs 8-15% branded.",
#         ai_agent="Assortment AI · Pricing Agent", data_source="POS, margin data",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("whatsapp-conversion", calc.calc_whatsapp_conversion, "conversion_proxy_pct",
#         kpi_id="K_TL_14", spreadsheet_num="14", name="WhatsApp Order Conversion",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+8% to +15%", baseline="Current chats -> orders",
#         why="Many kirana inquiries happen on WhatsApp — faster follow-up converts pings to revenue.",
#         ai_agent="Order Agent · Reminder AI", data_source="WhatsApp, POS",
#         perspective="Owner", spreadsheet_status="New New"),

#     _ok("household-wallet-share", calc.calc_household_wallet_share, "avg_share_pct",
#         kpi_id="K_TL_15", spreadsheet_num="15", name="Household Wallet Share",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+5% to +10%", baseline="Current family spend share",
#         why="Shows how much of a household's grocery wallet the store captures.",
#         ai_agent="BI Copilot · Retailer Asst", data_source="POS, loyalty/khata",
#         perspective="Owner", spreadsheet_status="New New"),

#     _ok("morning-stock-readiness", calc.calc_morning_stock_readiness, "readiness_score",
#         kpi_id="K_TL_16", spreadsheet_num="16", name="Morning Stock Readiness",
#         vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
#         target="+10% to +18%", baseline="Current before-10 AM fill %",
#         why="Fast-movers must be shelf-ready before first rush; missed morning demand is rarely recovered.",
#         ai_agent="Reorder AI · Stock AI", data_source="Opening stock, POS",
#         perspective="Owner", spreadsheet_status="New New"),

#     # ─── Kirana Owner — Bottom Line (16) ──────────────────────────────────────
#     _ok("udhar-recovery", calc.calc_udhar_recovery, "recovery_pct",
#         kpi_id="K_BL_1", spreadsheet_num="1", name="Udhar (Credit) Recovery",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-20% to -35%", baseline="Current overdue khata",
#         why="Biggest cash-flow killer — AI scoring + WhatsApp reminders.",
#         ai_agent="Credit AI · Collection Alert", data_source="Khata/ledger, POS",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("expiry-wastage", calc.calc_expiry_wastage, "waste_rate_pct",
#         kpi_id="K_BL_2", spreadsheet_num="2", name="Expiry & Wastage Loss",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-15% to -30%", baseline="Monthly expiry loss",
#         why="Items expiring on shelf = pure loss.",
#         ai_agent="Expiry Alert · Inventory AI", data_source="POS, stock register",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("dead-stock", calc.calc_dead_stock, "dead_stock_pct",
#         kpi_id="K_BL_3", spreadsheet_num="3", name="Dead Stock Reduction",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-12% to -20%", baseline="Current slow-mover value",
#         why="Items unsold 60+ days — blocking shelf and capital.",
#         ai_agent="Inventory AI · Reorder Copilot", data_source="POS, stock",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("procurement-cost-savings", calc.calc_procurement_cost, "savings_pct",
#         kpi_id="K_BL_4", spreadsheet_num="4", name="Procurement Cost Savings",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-5% to -12%", baseline="Avg purchase price",
#         why="AI compares 3-5 distributors, suggests best rate.",
#         ai_agent="Procurement AI · Smart Order", data_source="Purchase records, DMS",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("stockout-lost-sales", calc.calc_stockout_lost_sales, "estimated_lost_revenue",
#         kpi_id="K_BL_5", spreadsheet_num="5", name="Stockout Lost Sales",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-25% to -40%", baseline="Estimated lost sales/day",
#         why="Every OOS item sends customer to next shop.",
#         ai_agent="Stock-out Predict · Reorder AI", data_source="POS, stock levels",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("inventory-holding-cost", calc.calc_inventory_holding, "holding_cost_pct_of_revenue",
#         kpi_id="K_BL_6", spreadsheet_num="6", name="Inventory Holding Cost",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-10% to -18%", baseline="Working capital in stock",
#         why="Excess inventory = money not earning.",
#         ai_agent="Inventory AI · Forecast", data_source="POS, purchase data",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("shelf-productivity", calc.calc_shelf_productivity, "rev_per_sqft",
#         kpi_id="K_BL_7", spreadsheet_num="7", name="Shelf Space Productivity",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+8% to +15%", baseline="Revenue per sq ft",
#         why="Prime space to fast-movers, not slow movers.",
#         ai_agent="Assortment AI · Planogram", data_source="POS, store layout",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("distributor-terms", calc.calc_distributor_terms, "total_overpay_opportunity",
#         kpi_id="K_BL_8", spreadsheet_num="8", name="Distributor Terms Leverage",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-3% to -8% cost", baseline="Current payment terms",
#         why="AI flags when owner qualifies for bulk discounts.",
#         ai_agent="Procurement AI · Smart Order", data_source="Purchase history, DMS",
#         perspective="Owner", spreadsheet_status="Existing"),

#     _ok("perishable-waste", calc.calc_perishable_waste, "waste_rate_pct",
#         kpi_id="K_BL_9", spreadsheet_num="9", name="Perishable Freshness Waste",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-15% to -25%", baseline="Current dairy/bread/veg waste",
#         why="Dairy, bread, fruits expire in 1-7 days — daily AI tracking needed.",
#         ai_agent="Expiry Alert · Freshness AI", data_source="POS, daily stock count",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("overhead-ratio", calc.calc_overhead_ratio, "ratio_pct",
#         kpi_id="K_BL_10", spreadsheet_num="10", name="Electricity / Rent % of Rev",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-2% to -5% ratio", baseline="Current overhead ratio",
#         why="Fixed overhead 10-20% of revenue — AI benchmarks vs similar stores.",
#         ai_agent="BI Copilot · Ops AI", data_source="Bills, POS revenue",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("supplier-fill-rate", calc.calc_supplier_fill_rate, "fill_pct",
#         kpi_id="K_BL_11", spreadsheet_num="11", name="Supplier Fill Rate",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+8% to +15%", baseline="Current order vs delivery %",
#         why="Orders 50 SKUs, gets 38 — AI tracks defaulting distributors.",
#         ai_agent="Procurement AI · Supplier Score", data_source="Purchase orders, GRN",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("shrinkage", calc.calc_shrinkage, "shrinkage_rate_pct",
#         kpi_id="K_BL_12", spreadsheet_num="12", name="Pilferage / Shrinkage Loss",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-30% to -50%", baseline="Current stock discrepancy",
#         why="Theft 1-3% of revenue — POS vs physical reconciliation flags anomalies.",
#         ai_agent="Inventory AI · Audit Agent", data_source="POS, physical count",
#         perspective="Owner", spreadsheet_status="NEW v5"),

#     _ok("rtv-recovery", calc.calc_rtv_recovery, "recovery_pct",
#         kpi_id="K_BL_13", spreadsheet_num="13", name="Return-to-Vendor Recovery",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+15% to +25%", baseline="Current return recovery %",
#         why="Unsold or damaged items recovered from distributor beat full write-offs.",
#         ai_agent="Procurement AI · Inventory AI", data_source="DMS, return notes",
#         perspective="Owner", spreadsheet_status="New New"),

#     _ok("lead-time-accuracy", calc.calc_lead_time_accuracy, "on_time_rate_pct",
#         kpi_id="K_BL_14", spreadsheet_num="14", name="Reorder Lead-Time Accuracy",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+20% to +35%", baseline="Current promised vs actual days",
#         why="More predictable supplier lead times cut emergency purchases and reduce avoidable stockouts.",
#         ai_agent="Supplier Score · Forecast", data_source="PO / GRN history",
#         perspective="Owner", spreadsheet_status="New New"),

#     _ok("cash-leakage", calc.calc_cash_leakage, "leakage_rate_pct",
#         kpi_id="K_BL_15", spreadsheet_num="15", name="Cash Leakage / Billing Misses",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-20% to -35%", baseline="Current unbilled adjustments",
#         why="Prevents missed items, cash rounding leakage, and informal discounts that never hit the POS.",
#         ai_agent="Retailer Asst · Audit Agent", data_source="POS, cash tally",
#         perspective="Owner", spreadsheet_status="New New"),

#     _ok("markdown-recovery", calc.calc_markdown_recovery, "recovery_pct",
#         kpi_id="K_BL_16", spreadsheet_num="16", name="Near-Expiry Markdown Recovery",
#         vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+10% to +20%", baseline="Current markdown sell-through",
#         why="Selling expiring items at a discount is far better than writing them off at zero value.",
#         ai_agent="Expiry Alert · Pricing Agent", data_source="POS, batch / expiry",
#         perspective="Owner", spreadsheet_status="New New"),

#     # ─── Common (All Verticals) — 14 KPIs ─────────────────────────────────────
#     _ok("customer-ltv", calc.calc_customer_ltv, "avg_ltv",
#         kpi_id="C_1", spreadsheet_num="C1", name="Customer / Outlet LTV",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="₹18K+", baseline="Avg outlet LTV",
#         why="North star for unit economics.",
#         ai_agent="BI · Revenue AI", data_source="ERP, BI",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("nrr", calc.calc_nrr, "nrr_pct",
#         kpi_id="C_2", spreadsheet_num="C2", name="Net Revenue Retention",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="92%+ NRR", baseline="Current NRR",
#         why="Organic growth from existing.",
#         ai_agent="Churn Prediction", data_source="BI",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("arpu", calc.calc_arpu, "arpu",
#         kpi_id="C_3", spreadsheet_num="C3", name="ARPU Growth",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="₹499->699->865", baseline="ARPU by cohort",
#         why="Monetization velocity.",
#         ai_agent="Pricing AI · Upsell", data_source="Billing",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("repeat-customer-frequency", calc.calc_repeat_customer, "repeat_rate_pct",
#         kpi_id="C_4", spreadsheet_num="C4", name="Repeat Customer Growth",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="+10% to +20%", baseline="Current repeat rate",
#         why="Stickiest revenue across all verticals.",
#         ai_agent="Reminder AI · Loyalty", data_source="POS, CRM",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("cross-category-basket", calc.calc_cross_category_basket, "multi_category_pct",
#         kpi_id="C_5", spreadsheet_num="C5", name="Cross-sell / Upsell Rev %",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="+10% to +18%", baseline="Current upsell %",
#         why="Cheapest growth after retention.",
#         ai_agent="Rep Copilot · Smart Order", data_source="POS, DMS",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("brand-conversion", calc.calc_brand_conversion, "conversion_pct",
#         kpi_id="C_6", spreadsheet_num="C6", name="Brand Co-invest Conversion",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="+12% to +20%", baseline="Brand deal close %",
#         why="Channel validation.",
#         ai_agent="BD Copilot · Revenue AI", data_source="CRM",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("high-margin-sales", calc.calc_high_margin_sales, "high_margin_pct",
#         kpi_id="C_7", spreadsheet_num="C7", name="High-Margin Revenue %",
#         vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
#         target="+5% to +10%", baseline="Current mix",
#         why="Top-margin items all verticals.",
#         ai_agent="Assortment AI · Pricing", data_source="POS, margin",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("cac-payback", calc.calc_cac_payback, "payback_months",
#         kpi_id="C_8", spreadsheet_num="C8", name="CAC Payback Period",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="2-4 months", baseline="Avg payback",
#         why="Months to recover cost.",
#         ai_agent="Revenue AI · BI", data_source="Finance",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("working-capital-cycle", calc.calc_working_capital_cycle, "working_capital_days",
#         kpi_id="C_9", spreadsheet_num="C9", name="Working Capital Cycle",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-15% to -25%", baseline="Cash conversion cycle",
#         why="Kirana owner and FMCG alike.",
#         ai_agent="ERP Copilot · Finance AI", data_source="ERP",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("ops-cost-per-outlet", calc.calc_ops_cost_per_outlet, "avg_cost_per_outlet",
#         kpi_id="C_10", spreadsheet_num="C10", name="Ops Cost per Outlet",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-10% to -20%", baseline="Total cost / outlets",
#         why="Unit economics denominator.",
#         ai_agent="Ops Copilot · BI", data_source="Finance",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("ai-roi", calc.calc_ai_roi, "roi_multiplier",
#         kpi_id="C_11", spreadsheet_num="C11", name="AI ROI Multiplier",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="20-44x ROI", baseline="₹599/mo vs savings",
#         why="Most cited CXO metric.",
#         ai_agent="ROI Tracker · BI", data_source="Usage",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("customer-credit-risk", calc.calc_customer_credit_risk, "risk_pct",
#         kpi_id="C_12", spreadsheet_num="C12", name="Customer Credit Risk",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="-15% to -25%", baseline="Consolidated AR",
#         why="Udhar + hospital + aggregator AR.",
#         ai_agent="Credit AI · Collection", data_source="ERP, AR",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("data-quality-score", calc.calc_data_quality_score, "score",
#         kpi_id="C_13", spreadsheet_num="C13", name="Data Quality Score",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+15 to +25 pts", baseline="Field data fill rate",
#         why="AI quality = input quality.",
#         ai_agent="Data Quality · SFA", data_source="Telemetry",
#         perspective="Universal", spreadsheet_status="Existing"),

#     _ok("process-automation", calc.calc_process_automation, "automation_pct",
#         kpi_id="C_14", spreadsheet_num="C14", name="Process Automation Rate",
#         vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
#         target="+25% to +40%", baseline="% manual workflows",
#         why="Reduces headcount and errors.",
#         ai_agent="ERP · Workflow AI", data_source="Process logs",
#         perspective="Universal", spreadsheet_status="Existing"),
# ]


_REGISTRY: list[KPIDef] = [

    # ─── Kirana Owner — Top Line (16) ─────────────────────────────────────────

    _ok("daily-revenue", calc.calc_daily_revenue, "total_revenue",
        kpi_id="K_TL_1", spreadsheet_num="1", name="Daily Sales Revenue", category="Finance",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+10% to +18%", baseline="Current avg daily sales",
        why="Owner's #1 metric — total daily takda (cash + digital).",
        ai_agent="Retailer Asst · Smart Order", data_source="POS / manual billing",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("walkin-purchase", calc.calc_walkin_purchase, "conversion_pct",
        kpi_id="K_TL_2", spreadsheet_num="2", name="Walk-in to Purchase %", category="Customer",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+8% to +15%", baseline="Current footfall vs bills",
        why="How many entering actually buy — availability + service signal.",
        ai_agent="Retailer Asst · Stock AI", data_source="POS, footfall",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("repeat-customer-frequency", calc.calc_repeat_customer, "repeat_rate_pct",
        kpi_id="K_TL_3", spreadsheet_num="3", name="Repeat Customer Frequency", category="Customer",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+15% to +25%", baseline="Current repeat interval",
        why="Loyal regulars = predictable revenue.",
        ai_agent="Smart Order · Reminder AI", data_source="POS, WhatsApp",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("avg-basket-value", calc.calc_avg_basket_value, "avg_basket_value",
        kpi_id="K_TL_4", spreadsheet_num="4", name="Avg Basket Value", category="Finance",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+5% to +12%", baseline="Current avg bill value",
        why="More items per bill without extra footfall.",
        ai_agent="Smart Order · Retailer Asst", data_source="POS, billing",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("high-margin-sales", calc.calc_high_margin_sales, "high_margin_pct",
        kpi_id="K_TL_5", spreadsheet_num="5", name="High-Margin Item Sales %", category="Finance",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+5% to +10%", baseline="Current high-margin mix",
        why="Revenue from items where owner earns most — shifts mix to fatter margins.",
        ai_agent="Assortment AI · Pricing Agent", data_source="POS, margin data",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("scheme-capture", calc.calc_scheme_capture, "capture_pct",
        kpi_id="K_TL_6", spreadsheet_num="6", name="Scheme Benefit Capture", category="Finance",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+15% to +25%", baseline="Current scheme utilization",
        why="Owner misses 20-30% of eligible schemes — AI alerts ensure every rupee claimed.",
        ai_agent="Scheme Alert · Retailer Asst", data_source="DMS, scheme data",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("category-mix", calc.calc_category_mix, "overall_margin_pct",
        kpi_id="K_TL_7", spreadsheet_num="7", name="Category Mix Optimization", category="Sales",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+3% to +6% margin", baseline="Current category split",
        why="Shift shelf space to high-demand, high-margin categories.",
        ai_agent="Assortment AI · BI Copilot", data_source="POS, sales data",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("digital-payment-adoption", calc.calc_digital_payment, "digital_pct",
        kpi_id="K_TL_8", spreadsheet_num="8", name="Digital Payment Adoption", category="Customer",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+15% to +25%", baseline="Current UPI/digital %",
        why="Less cash risk, builds transaction data for AI.",
        ai_agent="Retailer Asst · Payment AI", data_source="POS, UPI data",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("home-delivery", calc.calc_home_delivery, "delivery_pct",
        kpi_id="K_TL_9", spreadsheet_num="9", name="Home Delivery Revenue %", category="Customer",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+8% to +15%", baseline="Current delivery order %",
        why="WhatsApp/phone delivery = incremental revenue without extra rent.",
        ai_agent="Order Agent · Retailer Asst", data_source="POS, WhatsApp orders",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("new-product-trial", calc.calc_new_product_trial, "success_rate_pct",
        kpi_id="K_TL_10", spreadsheet_num="10", name="New Product Trial Success", category="Sales",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+10% to +20%", baseline="Current new SKU sell-through",
        why="Does new SKU sell in 30 days? Avoids dead stock from failed launches.",
        ai_agent="Inventory AI · Assortment AI", data_source="POS, DMS",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("cross-category-basket", calc.calc_cross_category_basket, "multi_category_pct",
        kpi_id="K_TL_11", spreadsheet_num="11", name="Cross-Category Basket %", category="Sales",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+5% to +10%", baseline="Current multi-category bills",
        why="Customers buying 3+ categories = stickier, harder to lose.",
        ai_agent="Smart Order · Retailer Asst", data_source="POS, billing",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("festive-uplift", calc.calc_festive_uplift, "uplift_pct",
        kpi_id="K_TL_12", spreadsheet_num="12", name="Festive / Seasonal Uplift", category="Sales",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+20% to +35%", baseline="Current seasonal capture",
        why="Diwali, Eid, harvest drive 30-50% spikes — AI pre-stocks.",
        ai_agent="Forecast · Seasonal AI", data_source="POS, calendar, DMS",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("private-label", calc.calc_private_label, "private_label_pct",
        kpi_id="K_TL_13", spreadsheet_num="13", name="Private Label / Store Brand %", category="Sales",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+3% to +8%", baseline="Current own-brand revenue",
        why="Own brand/repackaged staples carry 40-60% margin vs 8-15% branded.",
        ai_agent="Assortment AI · Pricing Agent", data_source="POS, margin data",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("whatsapp-conversion", calc.calc_whatsapp_conversion, "conversion_proxy_pct",
        kpi_id="K_TL_14", spreadsheet_num="14", name="WhatsApp Order Conversion", category="Customer",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+8% to +15%", baseline="Current chats -> orders",
        why="Many kirana inquiries happen on WhatsApp — faster follow-up converts pings to revenue.",
        ai_agent="Order Agent · Reminder AI", data_source="WhatsApp, POS",
        perspective="Owner", spreadsheet_status="New New"),

    _ok("household-wallet-share", calc.calc_household_wallet_share, "avg_share_pct",
        kpi_id="K_TL_15", spreadsheet_num="15", name="Household Wallet Share", category="Customer",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+5% to +10%", baseline="Current family spend share",
        why="Shows how much of a household's grocery wallet the store captures.",
        ai_agent="BI Copilot · Retailer Asst", data_source="POS, loyalty/khata",
        perspective="Owner", spreadsheet_status="New New"),

    _ok("morning-stock-readiness", calc.calc_morning_stock_readiness, "readiness_score",
        kpi_id="K_TL_16", spreadsheet_num="16", name="Morning Stock Readiness", category="Inventory",
        vertical="Kirana Owner", pl_category="Top Line", theme="Revenue & Growth",
        target="+10% to +18%", baseline="Current before-10 AM fill %",
        why="Fast-movers must be shelf-ready before first rush; missed morning demand is rarely recovered.",
        ai_agent="Reorder AI · Stock AI", data_source="Opening stock, POS",
        perspective="Owner", spreadsheet_status="New New"),

    # ─── Kirana Owner — Bottom Line (16) ──────────────────────────────────────

    _ok("udhar-recovery", calc.calc_udhar_recovery, "recovery_pct",
        kpi_id="K_BL_1", spreadsheet_num="1", name="Udhar (Credit) Recovery",
        category="Risk",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-20% to -35%", baseline="Current overdue khata",
        why="Biggest cash-flow killer — AI scoring + WhatsApp reminders.",
        ai_agent="Credit AI · Collection Alert", data_source="Khata/ledger, POS",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("expiry-wastage", calc.calc_expiry_wastage, "waste_rate_pct",
        kpi_id="K_BL_2", spreadsheet_num="2", name="Expiry & Wastage Loss",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-15% to -30%", baseline="Monthly expiry loss",
        why="Items expiring on shelf = pure loss.",
        ai_agent="Expiry Alert · Inventory AI", data_source="POS, stock register",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("dead-stock", calc.calc_dead_stock, "dead_stock_pct",
        kpi_id="K_BL_3", spreadsheet_num="3", name="Dead Stock Reduction",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-12% to -20%", baseline="Current slow-mover value",
        why="Items unsold 60+ days — blocking shelf and capital.",
        ai_agent="Inventory AI · Reorder Copilot", data_source="POS, stock",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("procurement-cost-savings", calc.calc_procurement_cost, "savings_pct",
        kpi_id="K_BL_4", spreadsheet_num="4", name="Procurement Cost Savings",
        category="Finance",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-5% to -12%", baseline="Avg purchase price",
        why="AI compares 3-5 distributors, suggests best rate.",
        ai_agent="Procurement AI · Smart Order", data_source="Purchase records, DMS",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("stockout-lost-sales", calc.calc_stockout_lost_sales, "estimated_lost_revenue",
        kpi_id="K_BL_5", spreadsheet_num="5", name="Stockout Lost Sales",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-25% to -40%", baseline="Estimated lost sales/day",
        why="Every OOS item sends customer to next shop.",
        ai_agent="Stock-out Predict · Reorder AI", data_source="POS, stock levels",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("inventory-holding-cost", calc.calc_inventory_holding, "holding_cost_pct_of_revenue",
        kpi_id="K_BL_6", spreadsheet_num="6", name="Inventory Holding Cost",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-10% to -18%", baseline="Working capital in stock",
        why="Excess inventory = money not earning.",
        ai_agent="Inventory AI · Forecast", data_source="POS, purchase data",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("shelf-productivity", calc.calc_shelf_productivity, "rev_per_sqft",
        kpi_id="K_BL_7", spreadsheet_num="7", name="Shelf Space Productivity",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+8% to +15%", baseline="Revenue per sq ft",
        why="Prime space to fast-movers, not slow movers.",
        ai_agent="Assortment AI · Planogram", data_source="POS, store layout",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("distributor-terms", calc.calc_distributor_terms, "total_overpay_opportunity",
        kpi_id="K_BL_8", spreadsheet_num="8", name="Distributor Terms Leverage",
        category="Risk",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-3% to -8% cost", baseline="Current payment terms",
        why="AI flags when owner qualifies for bulk discounts.",
        ai_agent="Procurement AI · Smart Order", data_source="Purchase history, DMS",
        perspective="Owner", spreadsheet_status="Existing"),

    _ok("perishable-waste", calc.calc_perishable_waste, "waste_rate_pct",
        kpi_id="K_BL_9", spreadsheet_num="9", name="Perishable Freshness Waste",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-15% to -25%", baseline="Current dairy/bread/veg waste",
        why="Dairy, bread, fruits expire in 1-7 days — daily AI tracking needed.",
        ai_agent="Expiry Alert · Freshness AI", data_source="POS, daily stock count",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("overhead-ratio", calc.calc_overhead_ratio, "ratio_pct",
        kpi_id="K_BL_10", spreadsheet_num="10", name="Electricity / Rent % of Rev",
        category="Finance",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-2% to -5% ratio", baseline="Current overhead ratio",
        why="Fixed overhead 10-20% of revenue — AI benchmarks vs similar stores.",
        ai_agent="BI Copilot · Ops AI", data_source="Bills, POS revenue",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("supplier-fill-rate", calc.calc_supplier_fill_rate, "fill_pct",
        kpi_id="K_BL_11", spreadsheet_num="11", name="Supplier Fill Rate",
        category="Operations",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+8% to +15%", baseline="Current order vs delivery %",
        why="Orders 50 SKUs, gets 38 — AI tracks defaulting distributors.",
        ai_agent="Procurement AI · Supplier Score", data_source="Purchase orders, GRN",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("shrinkage", calc.calc_shrinkage, "shrinkage_rate_pct",
        kpi_id="K_BL_12", spreadsheet_num="12", name="Pilferage / Shrinkage Loss",
        category="Risk",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-30% to -50%", baseline="Current stock discrepancy",
        why="Theft 1-3% of revenue — POS vs physical reconciliation flags anomalies.",
        ai_agent="Inventory AI · Audit Agent", data_source="POS, physical count",
        perspective="Owner", spreadsheet_status="NEW v5"),

    _ok("rtv-recovery", calc.calc_rtv_recovery, "recovery_pct",
        kpi_id="K_BL_13", spreadsheet_num="13", name="Return-to-Vendor Recovery",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+15% to +25%", baseline="Current return recovery %",
        why="Unsold or damaged items recovered from distributor beat full write-offs.",
        ai_agent="Procurement AI · Inventory AI", data_source="DMS, return notes",
        perspective="Owner", spreadsheet_status="New New"),

    _ok("lead-time-accuracy", calc.calc_lead_time_accuracy, "on_time_rate_pct",
        kpi_id="K_BL_14", spreadsheet_num="14", name="Reorder Lead-Time Accuracy",
        category="Operations",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+20% to +35%", baseline="Current promised vs actual days",
        why="More predictable supplier lead times cut emergency purchases and reduce avoidable stockouts.",
        ai_agent="Supplier Score · Forecast", data_source="PO / GRN history",
        perspective="Owner", spreadsheet_status="New New"),

    _ok("cash-leakage", calc.calc_cash_leakage, "leakage_rate_pct",
        kpi_id="K_BL_15", spreadsheet_num="15", name="Cash Leakage / Billing Misses",
        category="Risk",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-20% to -35%", baseline="Current unbilled adjustments",
        why="Prevents missed items, cash rounding leakage, and informal discounts that never hit the POS.",
        ai_agent="Retailer Asst · Audit Agent", data_source="POS, cash tally",
        perspective="Owner", spreadsheet_status="New New"),

    _ok("markdown-recovery", calc.calc_markdown_recovery, "recovery_pct",
        kpi_id="K_BL_16", spreadsheet_num="16", name="Near-Expiry Markdown Recovery",
        category="Inventory",
        vertical="Kirana Owner", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+10% to +20%", baseline="Current markdown sell-through",
        why="Selling expiring items at a discount is far better than writing them off at zero value.",
        ai_agent="Expiry Alert · Pricing Agent", data_source="POS, batch / expiry",
        perspective="Owner", spreadsheet_status="New New"),

    # ─── Common (All Verticals) — 14 KPIs ─────────────────────────────────────

    _ok("customer-ltv", calc.calc_customer_ltv, "avg_ltv",
        kpi_id="C_1", spreadsheet_num="C1", name="Customer / Outlet LTV",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="₹18K+", baseline="Avg outlet LTV",
        why="North star for unit economics.",
        ai_agent="BI · Revenue AI", data_source="ERP, BI",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("nrr", calc.calc_nrr, "nrr_pct",
        kpi_id="C_2", spreadsheet_num="C2", name="Net Revenue Retention",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="92%+ NRR", baseline="Current NRR",
        why="Organic growth from existing.",
        ai_agent="Churn Prediction", data_source="BI",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("arpu", calc.calc_arpu, "arpu",
        kpi_id="C_3", spreadsheet_num="C3", name="ARPU Growth",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="₹499->699->865", baseline="ARPU by cohort",
        why="Monetization velocity.",
        ai_agent="Pricing AI · Upsell", data_source="Billing",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("repeat-customer-frequency", calc.calc_repeat_customer, "repeat_rate_pct",
        kpi_id="C_4", spreadsheet_num="C4", name="Repeat Customer Growth",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="+10% to +20%", baseline="Current repeat rate",
        why="Stickiest revenue across all verticals.",
        ai_agent="Reminder AI · Loyalty", data_source="POS, CRM",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("cross-category-basket", calc.calc_cross_category_basket, "multi_category_pct",
        kpi_id="C_5", spreadsheet_num="C5", name="Cross-sell / Upsell Rev %",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="+10% to +18%", baseline="Current upsell %",
        why="Cheapest growth after retention.",
        ai_agent="Rep Copilot · Smart Order", data_source="POS, DMS",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("brand-conversion", calc.calc_brand_conversion, "conversion_pct",
        kpi_id="C_6", spreadsheet_num="C6", name="Brand Co-invest Conversion",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="+12% to +20%", baseline="Brand deal close %",
        why="Channel validation.",
        ai_agent="BD Copilot · Revenue AI", data_source="CRM",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("high-margin-sales", calc.calc_high_margin_sales, "high_margin_pct",
        kpi_id="C_7", spreadsheet_num="C7", name="High-Margin Revenue %",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Top Line", theme="Revenue & Growth",
        target="+5% to +10%", baseline="Current mix",
        why="Top-margin items all verticals.",
        ai_agent="Assortment AI · Pricing", data_source="POS, margin",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("cac-payback", calc.calc_cac_payback, "payback_months",
        kpi_id="C_8", spreadsheet_num="C8", name="CAC Payback Period",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="2-4 months", baseline="Avg payback",
        why="Months to recover cost.",
        ai_agent="Revenue AI · BI", data_source="Finance",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("working-capital-cycle", calc.calc_working_capital_cycle, "working_capital_days",
        kpi_id="C_9", spreadsheet_num="C9", name="Working Capital Cycle",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-15% to -25%", baseline="Cash conversion cycle",
        why="Kirana owner and FMCG alike.",
        ai_agent="ERP Copilot · Finance AI", data_source="ERP",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("ops-cost-per-outlet", calc.calc_ops_cost_per_outlet, "avg_cost_per_outlet",
        kpi_id="C_10", spreadsheet_num="C10", name="Ops Cost per Outlet",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-10% to -20%", baseline="Total cost / outlets",
        why="Unit economics denominator.",
        ai_agent="Ops Copilot · BI", data_source="Finance",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("ai-roi", calc.calc_ai_roi, "roi_multiplier",
        kpi_id="C_11", spreadsheet_num="C11", name="AI ROI Multiplier",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="20-44x ROI", baseline="₹599/mo vs savings",
        why="Most cited CXO metric.",
        ai_agent="ROI Tracker · BI", data_source="Usage",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("customer-credit-risk", calc.calc_customer_credit_risk, "risk_pct",
        kpi_id="C_12", spreadsheet_num="C12", name="Customer Credit Risk",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="-15% to -25%", baseline="Consolidated AR",
        why="Udhar + hospital + aggregator AR.",
        ai_agent="Credit AI · Collection", data_source="ERP, AR",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("data-quality-score", calc.calc_data_quality_score, "score",
        kpi_id="C_13", spreadsheet_num="C13", name="Data Quality Score",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+15 to +25 pts", baseline="Field data fill rate",
        why="AI quality = input quality.",
        ai_agent="Data Quality · SFA", data_source="Telemetry",
        perspective="Universal", spreadsheet_status="Existing"),

    _ok("process-automation", calc.calc_process_automation, "automation_pct",
        kpi_id="C_14", spreadsheet_num="C14", name="Process Automation Rate",
        category="Core Insight",
        vertical="Common (All Verticals)", pl_category="Bottom Line", theme="Cost Savings & Efficiency",
        target="+25% to +40%", baseline="% manual workflows",
        why="Reduces headcount and errors.",
        ai_agent="ERP · Workflow AI", data_source="Process logs",
        perspective="Universal", spreadsheet_status="Existing"),

    # ═══════════════════════════════════════════════════════════════════════════
    # F4 — Vertical KPI packs (from Guru's Feature Gap Analysis).
    # Registered + tagged per vertical; shown in the admin panel. Hidden from
    # stores by default (status=data_unavailable) and switched on per vertical by
    # an admin once the underlying data lands. `missing_data` records exactly
    # what each one still needs (see docs/F4_KPI_Packs.md).
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Fashion (apparel / footwear / boutique / mono-brand) ──────────────────
    _ok("sell-through", calc.calc_sell_through, "sell_through_pct",
        kpi_id="V_AP_1", spreadsheet_num="V1", name="Sell-through %", category="Inventory",
        vertical="Fashion", pl_category="Top Line", theme="Inventory Efficiency",
        target="50–70% / season", baseline="Units sold ÷ units received",
        why="Core apparel health metric — how fast a buy sells before markdown.",
        ai_agent="Stock AI", data_source="order_item (variant), inventory",
        perspective="Owner", spreadsheet_status="New", verticals=["apparel", "footwear", "boutique", "sports_fitness", "cosmetics"]),

    _ok("size-curve", calc.calc_size_curve, "sizes_tracked",
        kpi_id="V_AP_2", spreadsheet_num="V2", name="Size-curve / Size-mix", category="Inventory",
        vertical="Fashion", pl_category="Operations", theme="Inventory Efficiency",
        target="Match demand curve", baseline="Sales by size",
        why="Reveals which sizes sell out first so re-orders match real demand.",
        ai_agent="Stock AI", data_source="product_variant.attributes, order_item",
        perspective="Owner", spreadsheet_status="New", verticals=["apparel", "footwear", "boutique", "sports_fitness", "cosmetics"]),

    _ok("markdown", calc.calc_markdown, "markdown_pct",
        kpi_id="V_AP_3", spreadsheet_num="V3", name="Markdown %", category="Finance",
        vertical="Fashion", pl_category="Bottom Line", theme="Margin",
        target="< 20% of revenue", baseline="Markdown value ÷ revenue",
        why="Tracks how much margin is lost to discounting.",
        ai_agent="Pricing AI", data_source="order_item, pricing/mrp",
        perspective="Owner", spreadsheet_status="New", verticals=["apparel", "footwear", "boutique", "sports_fitness", "cosmetics"]),

    _ok("gmroi", calc.calc_gmroi, "gmroi",
        kpi_id="V_AP_4", spreadsheet_num="V4", name="GMROI", category="Finance",
        vertical="Fashion", pl_category="Bottom Line", theme="Margin",
        target="> 2.5", baseline="Gross margin ÷ avg inventory cost",
        why="Return on every rupee tied up in stock — key for fashion/electronics.",
        ai_agent="Pricing AI", data_source="order_item.cost_price, inventory, pricing",
        perspective="Owner", spreadsheet_status="New", verticals=["apparel", "footwear", "boutique", "sports_fitness", "cosmetics", "electronics"]),

    _ok("outfit-uptake", calc.calc_outfit_uptake, "attach_pct",
        kpi_id="V_AP_5", spreadsheet_num="V5", name="Outfit / Bundle Uptake", category="Customer",
        vertical="Fashion", pl_category="Top Line", theme="Revenue & Growth",
        target="+10% basket", baseline="Bundle attach rate",
        why="Cross-sell driver for apparel/footwear.",
        ai_agent="Recommender AI", data_source="order_item co-occurrence",
        perspective="Owner", spreadsheet_status="New", verticals=["apparel", "footwear", "boutique", "sports_fitness", "cosmetics"]),

    # ── Electronics / mobile ──────────────────────────────────────────────────
    _ok("attach-rate", calc.calc_attach_rate, "attach_rate_pct",
        kpi_id="V_EL_1", spreadsheet_num="V6", name="Accessory Attach-rate", category="Customer",
        vertical="Electronics", pl_category="Top Line", theme="Revenue & Growth",
        target="> 30%", baseline="Orders with accessory ÷ device orders",
        why="High-margin add-on sales on every device sold.",
        ai_agent="Recommender AI", data_source="order_item, category links",
        perspective="Owner", spreadsheet_status="New", verticals=["electronics"]),

    _ok("warranty-claim-rate", calc.calc_warranty_claim_rate, "claim_rate_pct",
        kpi_id="V_EL_2", spreadsheet_num="V7", name="Warranty-claim Rate", category="Operations",
        vertical="Electronics", pl_category="Operations", theme="Service Quality",
        target="< 3%", baseline="Claims ÷ units sold",
        why="Quality + after-sales cost signal for electronics.",
        ai_agent="Service AI", data_source="product_serial + warranty_claim (M7)",
        perspective="Owner", spreadsheet_status="New", verticals=["electronics"]),

    # ── Optical ───────────────────────────────────────────────────────────────
    _ok("rx-renewal", calc.calc_rx_renewal, "due_count",
        kpi_id="V_OP_1", spreadsheet_num="V8", name="Prescription Renewal Due", category="Customer",
        vertical="Optical", pl_category="Top Line", theme="Revenue & Growth",
        target="Recall 100% due", baseline="Customers with Rx > 12 months",
        why="Recurring revenue from lens/eye-test renewals.",
        ai_agent="Reminder AI", data_source="customer.prescription_date (M8+F4)",
        perspective="Owner", spreadsheet_status="New", verticals=["optical"]),

    # ── Services (salon / fitness) ────────────────────────────────────────────
    _ok("service-revenue", calc.calc_service_revenue, "total_revenue",
        kpi_id="V_SV_1", spreadsheet_num="V9", name="Service-wise Revenue", category="Finance",
        vertical="Services", pl_category="Top Line", theme="Revenue & Growth",
        target="Track top services", baseline="Revenue by service",
        why="Salon/fitness revenue is service-driven, not product-driven.",
        ai_agent="Service AI", data_source="service + appointment (M4)",
        perspective="Owner", spreadsheet_status="New", verticals=["services"]),

    _ok("appointment-utilisation", calc.calc_appointment_utilisation, "utilisation_pct",
        kpi_id="V_SV_2", spreadsheet_num="V10", name="Appointment Utilisation", category="Operations",
        vertical="Services", pl_category="Operations", theme="Capacity",
        target="> 75%", baseline="Completed ÷ booked",
        why="Chair/trainer utilisation drives service profitability.",
        ai_agent="Service AI", data_source="appointment (M4)",
        perspective="Owner", spreadsheet_status="New", verticals=["services"]),

    # ── Cross-vertical (Guru critical gaps) ───────────────────────────────────
    _ok("zone-comparison", calc.calc_zone_comparison, "store_count",
        kpi_id="V_CM_1", spreadsheet_num="V11", name="Zone / City Comparison", category="Operations",
        vertical="Common (All Verticals)", pl_category="Operations", theme="Growth",
        target="Benchmark stores", baseline="Revenue by zone/city",
        why="Guru-flagged Critical gap for chains/supermarkets.",
        ai_agent="Analytics AI", data_source="store_group rollup (M2)",
        perspective="Owner", spreadsheet_status="New"),

    _ok("staff-performance", calc.calc_staff_performance, "staff_count",
        kpi_id="V_CM_2", spreadsheet_num="V12", name="Staff Performance", category="Operations",
        vertical="Common (All Verticals)", pl_category="Operations", theme="Productivity",
        target="Identify top staff", baseline="Sales per staff",
        why="Commission, coaching and rostering all need this.",
        ai_agent="Ops AI", data_source="staff module + order_item.user attribution (not built)",
        perspective="Owner", spreadsheet_status="New"),

]

# ── Lookup helpers ──────────────────────────────────────────────────────────

_BY_ID = {kpi.kpi_id: kpi for kpi in _REGISTRY}


def all_kpis() -> list[KPIDef]:
    return list(_REGISTRY)


def kpi_by_id(kpi_id: str) -> KPIDef | None:
    return _BY_ID.get(kpi_id)


def kpis_by_vertical(vertical: str) -> list[KPIDef]:
    return [k for k in _REGISTRY if k.vertical == vertical]


def implemented_kpis() -> list[KPIDef]:
    return [k for k in _REGISTRY if k.status == STATUS_OK]


def unavailable_kpis() -> list[KPIDef]:
    return [k for k in _REGISTRY if k.status == STATUS_DATA_UNAVAILABLE]


def kpi_to_metadata(k: KPIDef) -> dict:
    """JSON-safe metadata dict for /registry and /summary cards."""
    return {
        "kpi_id":              k.kpi_id,
        "spreadsheet_num":     k.spreadsheet_num,
        "name":                k.name,
        "vertical":            k.vertical,
        "pl_category":         k.pl_category,
        "theme":               k.theme,
        "category":            k.category,
        "target":              k.target,
        "baseline":            k.baseline,
        "why":                 k.why,
        "ai_agent":            k.ai_agent,
        "data_source":         k.data_source,
        "perspective":         k.perspective,
        "spreadsheet_status":  k.spreadsheet_status,
        "status":              k.status,
        "endpoint":            f"/kirana/kpis/{k.endpoint_slug}" if k.endpoint_slug else None,
        "primary_field":       k.primary_field,
        "missing_data":        k.missing_data,
        "verticals":           k.verticals,
    }


# ── F4: per-vertical visibility resolution ───────────────────────────────────
# The coarse vertical codes the app understands (mirror of the backend seed).
KNOWN_VERTICALS = [
    "grocery", "apparel", "footwear", "electronics", "optical", "services", "general",
    # PAI-3 — split out of grocery/apparel so they can diverge.
    "bakery", "boutique", "sports_fitness", "cosmetics",
]


def default_visible(k: KPIDef) -> bool:
    """Default (no admin override): computable KPIs show, coming-soon hide."""
    return k.status == STATUS_OK


def visible_kpis_for(
    vertical_code: str, overrides: dict[tuple[str, str], bool]
) -> list[KPIDef]:
    """KPIs a store on [vertical_code] should see: applicable to the vertical and
    visible after applying the admin override (else the registry default)."""
    out = []
    for k in _REGISTRY:
        if not k.applies_to(vertical_code):
            continue
        ov = overrides.get((k.kpi_id, vertical_code))
        if (ov if ov is not None else default_visible(k)):
            out.append(k)
    return out
