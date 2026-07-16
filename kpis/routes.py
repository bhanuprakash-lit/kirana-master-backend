"""
KPI Routes — 14 production-grade endpoints under /kirana/kpis/
Each endpoint returns a richly structured response matching its schema.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

# from kpis import calculator as calc
from kpis import calculators as calc
from kpis import registry as kpi_registry
from kpis.ml_inference import get_kpi_models

logger = logging.getLogger("kpis.routes")
router = APIRouter(prefix="/kirana/kpis", tags=["KPIs"])

# ── KPI Target definitions (from the KPI Master Database) ────────────────────
_TARGETS = {
    # ── 8 previously-blocked KPIs (now unblocked) ──────────────────────────────
    "daily_revenue":              {"raw": "+5% MoM",      "low_pct": 5.0,  "high_pct": 10.0,
                                    "description": "Grow monthly GMV by 5–10%"},
    "gross_profit_margin":        {"raw": "25–35%",       "low_pct": 25.0, "high_pct": 35.0,
                                    "description": "Maintain gross profit margin between 25–35%"},
    "avg_basket_value":           {"raw": "+8% to +15%",  "low_pct": 8.0,  "high_pct": 15.0,
                                    "description": "Grow average order value by 8–15%"},
    "inventory_turnover":         {"raw": "8x to 12x/yr", "low_pct": 8.0,  "high_pct": 12.0,
                                    "description": "Annualised stock turnover 8–12 times"},
    "stockout_rate":              {"raw": "<5%",          "low_pct": 0.0,  "high_pct": 5.0,
                                    "description": "Keep OOS SKUs below 5%"},
    "dead_stock":                 {"raw": "<10% inv value","low_pct": 0.0,  "high_pct": 10.0,
                                    "description": "Keep dead stock below 10% of inventory value"},
    "return_rate":                {"raw": "<2%",          "low_pct": 0.0,  "high_pct": 2.0,
                                    "description": "Keep return/cancellation rate below 2%"},
    "cashflow_runway":            {"raw": ">30 days",     "low_pct": 30.0, "high_pct": 60.0,
                                    "description": "Maintain >30 days of positive cashflow runway"},
    # ── Original 14 KPIs ───────────────────────────────────────────────────────
    "repeat_customer_frequency":  {"raw": "+15% to +25%", "low_pct": 15.0, "high_pct": 25.0,
                                    "description": "Increase % of repeat customers by 15–25%"},
    "category_mix":               {"raw": "+3% to +6% margin", "low_pct": 3.0, "high_pct": 6.0,
                                    "description": "Shift mix to higher-margin categories"},
    "digital_payment_adoption":   {"raw": "+15% to +25%", "low_pct": 15.0, "high_pct": 25.0,
                                    "description": "Grow digital (UPI/card) share by 15–25%"},
    "new_product_trial":          {"raw": "+10% to +20%", "low_pct": 10.0, "high_pct": 20.0,
                                    "description": "Improve new product 30-day success rate"},
    "cross_category_basket":      {"raw": "+5% to +10%", "low_pct": 5.0, "high_pct": 10.0,
                                    "description": "More baskets with 3+ categories"},
    "whatsapp_conversion":        {"raw": "+8% to +15%", "low_pct": 8.0, "high_pct": 15.0,
                                    "description": "WhatsApp inquiries that complete a data flow"},
    "morning_stock_readiness":    {"raw": "+10% to +18%", "low_pct": 10.0, "high_pct": 18.0,
                                    "description": "Fast-movers shelf-ready before morning rush"},
    "procurement_cost_savings":   {"raw": "-5% to -12%", "low_pct": 5.0, "high_pct": 12.0,
                                    "description": "Reduce procurement cost vs standard rates"},
    "inventory_holding_cost":     {"raw": "-10% to -18%", "low_pct": 10.0, "high_pct": 18.0,
                                    "description": "Reduce holding cost % of monthly revenue"},
    "distributor_terms":          {"raw": "-3% to -8% cost", "low_pct": 3.0, "high_pct": 8.0,
                                    "description": "Negotiate better distributor prices"},
    "perishable_waste":           {"raw": "-15% to -25%", "low_pct": 15.0, "high_pct": 25.0,
                                    "description": "Reduce perishable waste rate"},
    "shrinkage":                  {"raw": "-30% to -50%", "low_pct": 30.0, "high_pct": 50.0,
                                    "description": "Reduce stock shrinkage / pilferage"},
    "lead_time_accuracy":         {"raw": "+20% to +35%", "low_pct": 20.0, "high_pct": 35.0,
                                    "description": "Improve supplier delivery accuracy"},
    "cash_leakage":               {"raw": "-20% to -35%", "low_pct": 20.0, "high_pct": 35.0,
                                    "description": "Reduce billing misses and unpaid orders"},
    # ── New derivable KPIs (added with the 46-KPI registry) ─────────────────────
    "high_margin_sales":          {"raw": "+5% to +10%", "low_pct": 5.0, "high_pct": 10.0,
                                    "description": "Lift revenue share of high-margin SKUs"},
    "stockout_lost_sales":        {"raw": "-25% to -40%", "low_pct": 25.0, "high_pct": 40.0,
                                    "description": "Reduce revenue lost to OOS events"},
    "data_quality_score":         {"raw": "+15 to +25 pts", "low_pct": 15.0, "high_pct": 25.0,
                                    "description": "Improve fill rate of critical fields"},
}


def _base(kpi_id: str, store_id: int, store_name: str, period_days: int, trend: dict) -> dict:
    tgt = _TARGETS[kpi_id]
    today = date.today()
    return {
        "kpi_id":    kpi_id,
        "kpi_name":  kpi_id.replace("_", " ").title(),
        "store_id":  store_id,
        "store_name": store_name,
        "period_days": period_days,
        "period_from": today - timedelta(days=period_days),
        "period_to":   today,
        "target":      tgt,
        "trend":       trend,
        "last_updated": datetime.now(timezone.utc),
        "ml_insights": None,
    }


def _engine(request: Request):
    return request.app.state.engine


def _sname(engine, store_id: int) -> str:
    return calc._store_name(engine, store_id)


def _auth(request: Request):
    svc = request.app.state.kirana_service
    s   = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer   = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else ""
    if api_key == s.kirana_api_key or bearer == s.kirana_api_key:
        return {"role": "admin"}
    if bearer:
        user = svc.user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorised")


def _enforce_store_scope(request: Request, user: dict = Depends(_auth)):
    """Router-level IDOR guard for every KPI endpoint.

    KPI routes take `store_id` as a query param and feed it straight to the
    calculators, defaulting to store 1 when omitted — so without this a store
    owner could read any other store's revenue/margin/shrinkage by changing
    (or omitting) the number. Admins (X-API-Key or the admin bearer) may query
    any store; a store-scoped user must pass their OWN store_id explicitly.
    Read from the raw query string so it can't interact with each endpoint's
    own `store_id` default. Applied once at the router level.
    """
    if user.get("role") == "admin":
        return
    # /registry exposes only the KPI catalogue metadata (no store data), so it
    # needs no store_id and is safe for any authenticated user.
    if request.url.path.rstrip("/").endswith("/registry"):
        return
    owned = user.get("store_id")
    if owned is None:
        raise HTTPException(status_code=403, detail="No store assigned to this user")
    raw = request.query_params.get("store_id")
    if raw is None or int(raw) != int(owned):
        raise HTTPException(status_code=403, detail="Access denied to this store")


# Apply the guard to every route on this router (added after the routes are
# registered; FastAPI copies router.dependencies onto each route at include).
router.dependencies.append(Depends(_enforce_store_scope))


# ── 1. Repeat Customer Frequency ──────────────────────────────────────────────

@router.get("/repeat-customer-frequency",
            summary="Repeat Customer Frequency — loyalty and churn signals")
async def repeat_customer_frequency(
    request: Request,
    store_id: int = Query(1, description="Store ID (1–4)"),
    days: int     = Query(30, ge=7, le=365, description="Analysis window in days"),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_repeat_customer(engine, store_id, days)
    ml     = get_kpi_models()

    # ML: churn scoring per customer segment
    cust_sql = """
    WITH intervals AS (
        SELECT customer_id,
               order_date::date AS od,
               total_amount,
               LAG(order_date::date) OVER (PARTITION BY customer_id ORDER BY order_date) AS prev_od
        FROM kirana_oltp.orders
        WHERE store_id=:sid AND order_status='completed' AND customer_id IS NOT NULL
    )
    SELECT customer_id,
           COUNT(*) AS order_count,
           MAX(od)::text AS last_visit,
           MIN(od)::text AS first_visit,
           AVG(total_amount) AS avg_basket,
           AVG(od - prev_od) AS avg_interval_days,
           STDDEV((od - prev_od)::float) AS interval_std,
           (CURRENT_DATE - MAX(od)) AS days_since_last,
           (MAX(od) - MIN(od)) AS tenure_days
    FROM intervals GROUP BY customer_id
    """
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text(cust_sql), {"sid": store_id}).mappings().all()
    cust_feats = [dict(r) for r in rows]
    churn_preds = ml.predict_churn(cust_feats)

    high_churn  = sum(1 for p in churn_preds if p["churn_risk"] == "high")
    avg_churn_p = round(sum(p["churn_prob"] for p in churn_preds) / max(len(churn_preds), 1), 3)

    return {
        **_base("repeat_customer_frequency", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_customers":             data["total_customers"],
        "repeat_customer_count":       data["repeat_customer_count"],
        "repeat_rate_pct":             data["repeat_rate_pct"],
        "avg_visit_interval_days":     data["avg_visit_interval_days"],
        "median_visit_interval_days":  data["median_visit_interval_days"],
        "at_risk_count":               data["at_risk_count"],
        "churned_count":               data["churned_count"],
        "segments":                    data["segments"],
        "ml_insights": {
            "model":             "XGBoost Churn Predictor",
            "high_churn_risk_customers": high_churn,
            "avg_churn_probability":     avg_churn_p,
            "top_at_risk": sorted(churn_preds, key=lambda x: x["churn_prob"], reverse=True)[:10],
        },
    }


# ── 2. Category Mix Optimization ──────────────────────────────────────────────

@router.get("/category-mix",
            summary="Category Mix Optimization — BCG quadrant analysis")
async def category_mix(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_category_mix(engine, store_id, days)
    ml     = get_kpi_models()

    # Re-run BCG with ML model
    ml_enriched = ml.predict_bcg([
        {"rev_share": c["revenue_share_pct"],
         "margin_pct": c["margin_pct"],
         "velocity":   c["avg_units_per_day"]}
        for c in data.get("categories", [])
    ])
    for i, c in enumerate(data.get("categories", [])):
        if i < len(ml_enriched):
            c["bcg_quadrant"] = ml_enriched[i].get("bcg_quadrant", c["bcg_quadrant"])

    return {
        **_base("category_mix", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_revenue":      data["total_revenue"],
        "overall_margin_pct": data["overall_margin_pct"],
        "category_count":     data["category_count"],
        "mix_score":          data["mix_score"],
        "categories":         data.get("categories", []),
        "top_opportunity":    data.get("top_opportunity", ""),
        "ml_insights": {"model": "KMeans-4 BCG Classifier"},
    }


# ── 3. Digital Payment Adoption ───────────────────────────────────────────────

@router.get("/digital-payment-adoption",
            summary="Digital Payment Adoption — UPI/card vs cash split and trend")
async def digital_payment_adoption(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_digital_payment(engine, store_id, days)
    return {
        **_base("digital_payment_adoption", store_id, _sname(engine, store_id), days, data["trend"]),
        "digital_pct":        data["digital_pct"],
        "cash_pct":           data["cash_pct"],
        "total_transactions": data["total_transactions"],
        "total_amount":       data["total_amount"],
        "by_method":          data["by_method"],
        "weekly_trend":       data["weekly_trend"],
    }


# ── 4. New Product Trial Success ──────────────────────────────────────────────

@router.get("/new-product-trial",
            summary="New Product Trial Success — 30-day velocity of recently launched SKUs")
async def new_product_trial(
    request: Request,
    store_id: int    = Query(1),
    trial_days: int  = Query(30, ge=7, le=90),
    user: dict       = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_new_product_trial(engine, store_id, trial_days)
    ml     = get_kpi_models()

    # ML: predict success probability for each new product
    trial_feats = []
    for p in data.get("products", []):
        trial_feats.append({
            "category_id":  p.get("category_id", 0),
            "is_perishable": p.get("is_perishable", 0),
            "is_loose":     p.get("is_loose", 0),
            "price":        p.get("price", 0.0),
            "cost_price":   p.get("cost_price", 0.0),
            "margin_pct":   p.get("margin_pct", 0.0),
        })
    probs = ml.predict_trial_success(trial_feats)
    for i, prod in enumerate(data.get("products", [])):
        if i < len(probs):
            prod["predicted_success_prob"] = probs[i]

    return {
        **_base("new_product_trial", store_id, _sname(engine, store_id), trial_days, data["trend"]),
        "trial_window_days":   data["trial_window_days"],
        "new_products_count":  data["new_products_count"],
        "success_rate_pct":    data["success_rate_pct"],
        "avg_units_sold_30d":  data["avg_units_sold_30d"],
        "products":            data.get("products", []),
        "ml_insights": {"model": "XGBoost Trial Success Predictor"},
    }


# ── 5. Cross-Category Basket ──────────────────────────────────────────────────

@router.get("/cross-category-basket",
            summary="Cross-Category Basket % — multi-category purchase behaviour")
async def cross_category_basket(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_cross_category_basket(engine, store_id, days)
    return {
        **_base("cross_category_basket", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_orders":             data["total_orders"],
        "multi_category_orders":    data["multi_category_orders"],
        "multi_category_pct":       data["multi_category_pct"],
        "avg_categories_per_order": data["avg_categories_per_order"],
        "orders_3plus_cat_pct":     data["orders_3plus_cat_pct"],
        "top_pairs":                data["top_pairs"],
        "ml_insights": {
            "model": "Apriori Co-occurrence Matrix",
            "note":  "top_pairs lifted by support and lift metric",
        },
    }


# ── 6. WhatsApp Order Conversion ──────────────────────────────────────────────

@router.get("/whatsapp-conversion",
            summary="WhatsApp Order Conversion — chatbot engagement funnel")
async def whatsapp_conversion(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_whatsapp_conversion(engine, store_id, days)
    return {
        **_base("whatsapp_conversion", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_sessions":          data["total_sessions"],
        "active_sessions":         data["active_sessions"],
        "language_breakdown":      data["language_breakdown"],
        "state_breakdown":         data["state_breakdown"],
        "total_messages_sent":     data["total_messages_sent"],
        "total_messages_received": data["total_messages_received"],
        "avg_messages_per_session": data["avg_messages_per_session"],
        "conversion_proxy_pct":    data["conversion_proxy_pct"],
    }


# ── 7. Morning Stock Readiness ────────────────────────────────────────────────

@router.get("/morning-stock-readiness",
            summary="Morning Stock Readiness — fast-movers stocked before rush hour")
async def morning_stock_readiness(
    request: Request,
    store_id: int = Query(1),
    user: dict    = Depends(_auth),
):
    engine    = _engine(request)
    ml_adapter = request.app.state.kirana_service.ml
    data      = calc.calc_morning_stock_readiness(engine, store_id, ml_adapter)
    return {
        **_base("morning_stock_readiness", store_id, _sname(engine, store_id), 1, data["trend"]),
        "readiness_score":   data["readiness_score"],
        "ready_count":       data["ready_count"],
        "low_count":         data["low_count"],
        "critical_count":    data["critical_count"],
        "total_fast_movers": data["total_fast_movers"],
        "skus":              data["skus"],
        "ml_insights": {
            "source":  "Stockout Predictor (XGBoost GPU)",
            "note":    "stockout_risk_7d per SKU from trained ML model",
        },
    }


# ── 8. Procurement Cost Savings ───────────────────────────────────────────────

@router.get("/procurement-cost-savings",
            summary="Procurement Cost Savings — actual vs standard rate per supplier")
async def procurement_cost_savings(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(90, ge=30, le=365),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_procurement_cost(engine, store_id, days)
    return {
        **_base("procurement_cost_savings", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_purchased_value": data["total_purchased_value"],
        "total_standard_value":  data["total_standard_value"],
        "net_savings":           data["net_savings"],
        "savings_pct":           data["savings_pct"],
        "overpay_count":         data["overpay_count"],
        "underpay_count":        data["underpay_count"],
        "by_supplier":           data["by_supplier"],
    }


# ── 9. Inventory Holding Cost ─────────────────────────────────────────────────

@router.get("/inventory-holding-cost",
            summary="Inventory Holding Cost — capital tied up vs optimal levels")
async def inventory_holding_cost(
    request: Request,
    store_id: int = Query(1),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_inventory_holding(engine, store_id)
    return {
        **_base("inventory_holding_cost", store_id, _sname(engine, store_id), 30, data["trend"]),
        "total_stock_value":           data["total_stock_value"],
        "total_holding_cost":          data["total_holding_cost"],
        "holding_cost_pct_of_revenue": data["holding_cost_pct_of_revenue"],
        "excess_inventory_value":      data["excess_inventory_value"],
        "optimal_stock_value":         data["optimal_stock_value"],
        "by_category":                 data["by_category"],
    }


# ── 10. Distributor Terms Leverage ────────────────────────────────────────────

@router.get("/distributor-terms",
            summary="Distributor Terms Leverage — price variance and reliability per supplier")
async def distributor_terms(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(90, ge=30, le=365),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_distributor_terms(engine, store_id, days)
    ml     = get_kpi_models()

    # Enrich with ML supplier reliability scores
    for sup in data.get("by_supplier", []):
        expected_lead = sup.get("avg_expected_lead_days", 3.0)
        actual_lead   = sup.get("avg_actual_lead_days", expected_lead)
        lead_variance = abs(actual_lead - expected_lead)
        price_acc     = max(0.0, 1.0 - abs(sup.get("price_variance_pct", 0)) / 100.0)
        ml_score = ml.score_supplier_reliability([{
            "actual_cost":    sup["avg_actual_cost"],
            "standard_cost":  sup["avg_standard_cost"],
            "expected_lead":  expected_lead,
            "lead_variance":  lead_variance,
            "price_accuracy": price_acc,
        }])
        if ml_score:
            sup["ml_reliability_score"] = round(ml_score[0] * 100, 1)

    return {
        **_base("distributor_terms", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_suppliers":           data["total_suppliers"],
        "best_supplier_id":          data["best_supplier_id"],
        "best_supplier_name":        data["best_supplier_name"],
        "total_overpay_opportunity": data["total_overpay_opportunity"],
        "by_supplier":               data["by_supplier"],
        "ml_insights": {"model": "XGBoost Supplier Reliability Predictor"},
    }


# ── 11. Perishable Freshness Waste ────────────────────────────────────────────

@router.get("/perishable-waste",
            summary="Perishable Freshness Waste — stagnant perishable stock at risk")
async def perishable_waste(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(14, ge=3, le=30),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_perishable_waste(engine, store_id, days)
    return {
        **_base("perishable_waste", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_perishable_skus": data["total_perishable_skus"],
        "high_risk_count":       data["high_risk_count"],
        "medium_risk_count":     data["medium_risk_count"],
        "total_at_risk_value":   data["total_at_risk_value"],
        "waste_rate_pct":        data["waste_rate_pct"],
        "items":                 data["items"],
    }


# ── 12. Pilferage / Shrinkage Loss ────────────────────────────────────────────

@router.get("/shrinkage",
            summary="Pilferage / Shrinkage Loss — stock reconciliation with anomaly detection")
async def shrinkage(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=90),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    ml     = get_kpi_models()
    data   = calc.calc_shrinkage(engine, store_id, days, ml_anomaly_fn=ml.score_shrinkage)
    return {
        **_base("shrinkage", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_shrinkage_units": data["total_shrinkage_units"],
        "total_shrinkage_value": data["total_shrinkage_value"],
        "shrinkage_rate_pct":   data["shrinkage_rate_pct"],
        "flagged_skus_count":   data["flagged_skus_count"],
        "items":                data["items"],
        "ml_insights": {"model": "IsolationForest Anomaly Detector",
                        "contamination": "12%"},
    }


# ── 13. Reorder Lead-Time Accuracy ────────────────────────────────────────────

@router.get("/lead-time-accuracy",
            summary="Reorder Lead-Time Accuracy — actual vs expected supplier delivery days")
async def reorder_lead_time(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(90, ge=30, le=365),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_lead_time_accuracy(engine, store_id, days)
    return {
        **_base("lead_time_accuracy", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_purchase_orders": data["total_purchase_orders"],
        "avg_expected_days":     data["avg_expected_days"],
        "avg_actual_days":       data["avg_actual_days"],
        "on_time_rate_pct":      data["on_time_rate_pct"],
        "overall_accuracy_pct":  data["overall_accuracy_pct"],
        "by_supplier":           data["by_supplier"],
    }


# ── 14. Cash Leakage / Billing Misses ────────────────────────────────────────

@router.get("/cash-leakage",
            summary="Cash Leakage / Billing Misses — orders with missing or mismatched payments")
async def cash_leakage(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_cash_leakage(engine, store_id, days)
    return {
        **_base("cash_leakage", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_orders":        data["total_orders"],
        "clean_orders":        data["clean_orders"],
        "problematic_orders":  data["problematic_orders"],
        "total_leakage_value": data["total_leakage_value"],
        "leakage_rate_pct":    data["leakage_rate_pct"],
        "unpaid_count":        data["unpaid_count"],
        "mismatch_count":      data["mismatch_count"],
        "flagged_orders":      data["flagged_orders"],
    }


# ── 15. Daily Revenue (GMV) ───────────────────────────────────────────────────

@router.get("/daily-revenue", summary="Annual Revenue — GMV and daily trend")
async def daily_revenue(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=365),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_daily_revenue(engine, store_id, days)
    return {
        **{
            **_base("daily_revenue", store_id, _sname(engine, store_id), days, data["trend"]),
            "kpi_name": "Annual Revenue",
        },
        "total_revenue":     data["total_revenue"],
        "avg_daily_revenue": data["avg_daily_revenue"],
        "order_count":       data["order_count"],
        "daily_breakdown":   data["daily_breakdown"],
    }


# ── 16. Gross Profit Margin ────────────────────────────────────────────────────

@router.get("/gross-profit-margin", summary="Gross Profit Margin — overall and by category")
async def gross_profit_margin(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_gross_profit_margin(engine, store_id, days)
    return {
        **_base("gross_profit_margin", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_revenue":  data["total_revenue"],
        "total_cogs":     data["total_cogs"],
        "gross_profit":   data["gross_profit"],
        "gpm_pct":        data["gpm_pct"],
        "by_category":    data["by_category"],
    }


# ── 17. Average Basket Value ───────────────────────────────────────────────────

@router.get("/avg-basket-value", summary="Average Basket Value — order value distribution")
async def avg_basket_value(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_avg_basket_value(engine, store_id, days)
    return {
        **_base("avg_basket_value", store_id, _sname(engine, store_id), days, data["trend"]),
        "avg_basket_value":    data["avg_basket_value"],
        "median_basket_value": data["median_basket_value"],
        "max_basket_value":    data["max_basket_value"],
        "order_count":         data["order_count"],
        "brackets":            data["brackets"],
    }


# ── 18. Inventory Turnover ─────────────────────────────────────────────────────

@router.get("/inventory-turnover", summary="Inventory Turnover — annualised ratio and days on hand")
async def inventory_turnover(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_inventory_turnover(engine, store_id, days)
    return {
        **_base("inventory_turnover", store_id, _sname(engine, store_id), days, data["trend"]),
        "turnover_ratio":      data["turnover_ratio"],
        "days_of_inventory":   data["days_of_inventory"],
        "cogs":                data["cogs"],
        "avg_inventory_value": data["avg_inventory_value"],
        "by_category":         data["by_category"],
    }


# ── 19. Stockout Rate ─────────────────────────────────────────────────────────

@router.get("/stockout-rate", summary="Stockout Rate — % of SKUs out of stock")
async def stockout_rate(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=90),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_stockout_rate(engine, store_id, days)
    return {
        **_base("stockout_rate", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_skus":      data["total_skus"],
        "oos_sku_count":   data["oos_sku_count"],
        "low_stock_count": data["low_stock_count"],
        "oos_rate_pct":    data["oos_rate_pct"],
        "oos_items":       data["oos_items"],
    }


# ── 20. Dead Stock ────────────────────────────────────────────────────────────

@router.get("/dead-stock", summary="Dead Stock — SKUs with zero sales")
async def dead_stock(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=14, le=90),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_dead_stock(engine, store_id, days)
    return {
        **_base("dead_stock", store_id, _sname(engine, store_id), days, data["trend"]),
        "dead_sku_count":        data["dead_sku_count"],
        "dead_stock_value":      data["dead_stock_value"],
        "total_inventory_value": data["total_inventory_value"],
        "dead_stock_pct":        data["dead_stock_pct"],
        "items":                 data["items"],
    }


# ── 21. Return Rate ───────────────────────────────────────────────────────────

@router.get("/return-rate", summary="Return Rate — order cancellations and returns")
async def return_rate(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_return_rate(engine, store_id, days)
    return {
        **_base("return_rate", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_orders":    data["total_orders"],
        "returned_orders": data["returned_orders"],
        "return_rate_pct": data["return_rate_pct"],
        "returned_value":  data["returned_value"],
        "by_status":       data["by_status"],
    }


# ── 22. Cashflow Runway ───────────────────────────────────────────────────────

@router.get("/cashflow-runway", summary="Cashflow Runway — net cash position and days of runway")
async def cashflow_runway(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=90),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_cashflow_runway(engine, store_id, days)
    return {
        **_base("cashflow_runway", store_id, _sname(engine, store_id), days, data["trend"]),
        "period_revenue":  data["period_revenue"],
        "period_cost":     data["period_cost"],
        "net_cashflow":    data["net_cashflow"],
        "daily_net":       data["daily_net"],
        "runway_days":     data["runway_days"],
        "cashflow_status": data["cashflow_status"],
        "weekly_cashflow": data["weekly_cashflow"],
    }


# ── New derivable KPI endpoints (added with the 46-KPI registry) ──────────────

@router.get("/high-margin-sales",
            summary="High-Margin Item Sales % — revenue share from top-margin SKUs (K-TL5 / C7)")
async def high_margin_sales(
    request: Request,
    store_id: int  = Query(1),
    days: int      = Query(30, ge=7, le=180),
    margin_pctile: float = Query(0.75, ge=0.5, le=0.95),
    user: dict     = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_high_margin_sales(engine, store_id, days, margin_pctile)
    return {
        **_base("high_margin_sales", store_id, _sname(engine, store_id), days, data["trend"]),
        "total_skus":            data["total_skus"],
        "high_margin_skus":      data["high_margin_skus"],
        "total_revenue":         data["total_revenue"],
        "high_margin_revenue":   data["high_margin_revenue"],
        "high_margin_pct":       data["high_margin_pct"],
        "high_margin_profit":    data["high_margin_profit"],
        "margin_percentile":     margin_pctile,
    }


@router.get("/stockout-lost-sales",
            summary="Stockout Lost Sales — revenue lost to OOS days (K-BL5)")
async def stockout_lost_sales(
    request: Request,
    store_id: int = Query(1),
    days: int     = Query(30, ge=7, le=180),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_stockout_lost_sales(engine, store_id, days)
    return {
        **_base("stockout_lost_sales", store_id, _sname(engine, store_id), days, data["trend"]),
        "estimated_lost_revenue":  data["estimated_lost_revenue"],
        "direct_lost_revenue":     data["direct_lost_revenue"],
        "proxy_lost_revenue":      data["proxy_lost_revenue"],
        "lost_units":              data["lost_units"],
        "zero_stock_days":         data["zero_stock_days"],
        "skus_impacted":           data["skus_impacted"],
        "skus_observed":           data["skus_observed"],
        "method":                  data["method"],
    }


@router.get("/data-quality-score",
            summary="Data Quality Score — fill rate across critical fields (C13)")
async def data_quality_score(
    request: Request,
    store_id: int = Query(1, description="Store scope (currently global, kept for symmetry)"),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    data   = calc.calc_data_quality_score(engine, store_id)
    return {
        **_base("data_quality_score", store_id, _sname(engine, store_id), 0, data["trend"]),
        "score":       data["score"],
        "field_count": data["field_count"],
        "breakdown":   data["breakdown"],
    }


# ── Registry endpoint ────────────────────────────────────────────────────────

@router.get("/registry",
            summary="46-KPI Master Registry — vertical, theme, status and endpoint per KPI")
async def kpi_registry_endpoint(
    request: Request,
    vertical: str | None = Query(None, description="Filter by vertical exact match"),
    status: str   | None = Query(None, description="Filter by status: ok | data_unavailable"),
    user: dict    = Depends(_auth),
):
    items = kpi_registry.all_kpis()
    if vertical:
        items = [k for k in items if k.vertical == vertical]
    if status:
        items = [k for k in items if k.status == status]
    payload = [kpi_registry.kpi_to_metadata(k) for k in items]
    counts = {
        "total":              len(payload),
        "ok":                 sum(1 for k in payload if k["status"] == kpi_registry.STATUS_OK),
        "data_unavailable":   sum(1 for k in payload if k["status"] == kpi_registry.STATUS_DATA_UNAVAILABLE),
    }
    return {
        "verticals":   sorted({k["vertical"] for k in payload}),
        "counts":      counts,
        "kpis":        payload,
        "last_updated": datetime.now(timezone.utc),
    }


# ── Generic per-KPI slug endpoint ────────────────────────────────────────────

# Map endpoint_slug -> KPIDef for O(1) lookup.
_BY_SLUG = {k.endpoint_slug: k for k in kpi_registry.all_kpis() if k.endpoint_slug}

# Functions that take (engine, store_id) instead of (engine, store_id, days)
_TWO_ARG_CALCS = {
    calc.calc_inventory_holding,
    calc.calc_data_quality_score,
    calc.calc_customer_ltv,
    calc.calc_working_capital_cycle,
    calc.calc_ops_cost_per_outlet,
    calc.calc_ai_roi,
    calc.calc_customer_credit_risk,
    calc.calc_process_automation,
    calc.calc_shelf_productivity,
}

def _compute_one(request: Request, kpi: kpi_registry.KPIDef,
                  store_id: int, days: int):
    """Run the registry-bound calculator for a single KPI and return a
    response shaped consistently with the existing per-KPI endpoints —
    base envelope (kpi_id, target, trend, period) plus the calculator's
    raw fields.
    """
    engine = _engine(request)
    ma     = request.app.state.kirana_service.ml
    today  = date.today()

    if kpi.compute is calc.calc_morning_stock_readiness:
        data = kpi.compute(engine, store_id, ma)
        period_days = 1
    elif kpi.compute in _TWO_ARG_CALCS:
        data = kpi.compute(engine, store_id)
        if kpi.compute is calc.calc_inventory_holding:
            period_days = 30
        else:
            period_days = 0
    elif kpi.compute is calc.calc_nrr or kpi.compute is calc.calc_arpu:
        data = kpi.compute(engine, None, days)
        period_days = days
    else:
        data = kpi.compute(engine, store_id, days)
        period_days = days

    sname = _sname(engine, store_id)
    target = {
        "raw":         kpi.target,
        "description": kpi.why,
    }
    return {
        "kpi_id":      kpi.kpi_id,
        "kpi_key":     (kpi.endpoint_slug or kpi.kpi_id).replace("-", "_"),
        "kpi_name":    kpi.name,
        "store_id":    store_id,
        "store_name":  sname,
        "period_days": period_days,
        "period_from": today - timedelta(days=period_days),
        "period_to":   today,
        "target":      target,
        "trend":       data.get("trend") or {},
        "last_updated": datetime.now(timezone.utc),
        "primary_field": kpi.primary_field,
        "primary_value": data.get(kpi.primary_field) if kpi.primary_field else None,
        # Spread the calculator's structured fields (skipping trend, already lifted)
        **{k: v for k, v in data.items() if k != "trend"},
    }


@router.get("/by-slug/{slug}",
            summary="Compute a single KPI by its endpoint slug (e.g. walkin-purchase)")
async def kpi_by_slug(
    request: Request,
    slug: str,
    store_id: int = Query(1),
    days: int     = Query(30, ge=1, le=365),
    user: dict    = Depends(_auth),
):
    kpi = _BY_SLUG.get(slug)
    if not kpi or kpi.compute is None:
        raise HTTPException(status_code=404, detail=f"Unknown KPI slug: {slug}")
    return _compute_one(request, kpi, store_id, days)


# Inject a route handler for every registry KPI that does not already have an
# explicit endpoint defined above. This means /kirana/kpis/walkin-purchase,
# /kirana/kpis/scheme-capture, etc. all resolve cleanly without hand-written
# duplicates.

_EXPLICIT_SLUGS = {
    "repeat-customer-frequency", "category-mix", "digital-payment-adoption",
    "new-product-trial", "cross-category-basket", "whatsapp-conversion",
    "morning-stock-readiness", "procurement-cost-savings", "inventory-holding-cost",
    "distributor-terms", "perishable-waste", "shrinkage", "lead-time-accuracy",
    "cash-leakage", "daily-revenue", "gross-profit-margin", "avg-basket-value",
    "inventory-turnover", "stockout-rate", "dead-stock", "return-rate",
    "cashflow-runway", "high-margin-sales", "stockout-lost-sales",
    "data-quality-score",
}


def _make_slug_handler(_kpi: kpi_registry.KPIDef):
    async def _handler(
        request: Request,
        store_id: int = Query(1),
        days: int     = Query(30, ge=1, le=365),
        user: dict    = Depends(_auth),
    ):
        return _compute_one(request, _kpi, store_id, days)
    return _handler


for _slug, _kpi in list(_BY_SLUG.items()):
    if _slug in _EXPLICIT_SLUGS:
        continue
    router.add_api_route(
        f"/{_slug}",
        _make_slug_handler(_kpi),
        methods=["GET"],
        summary=f"{_kpi.name} ({_kpi.kpi_id})",
    )


@router.get("/by-id/{kpi_id}",
            summary="Compute a single KPI by its registry kpi_id (e.g. K_TL_1, C_7)")
async def kpi_by_id_value(
    request: Request,
    kpi_id: str,
    store_id: int = Query(1),
    days: int     = Query(30, ge=1, le=365),
    user: dict    = Depends(_auth),
):
    kpi = kpi_registry.kpi_by_id(kpi_id)
    if kpi is None:
        raise HTTPException(status_code=404, detail=f"Unknown KPI id: {kpi_id}")

    meta = kpi_registry.kpi_to_metadata(kpi)
    if kpi.status != kpi_registry.STATUS_OK or kpi.compute is None:
        return {**meta, "value": None, "data": None,
                "error": "data_unavailable",
                "missing_data": kpi.missing_data}

    engine = _engine(request)
    ma     = request.app.state.kirana_service.ml
    try:
        # Adapt the small set of KPIs whose signature is non-standard.
        if kpi.compute is calc.calc_morning_stock_readiness:
            data = kpi.compute(engine, store_id, ma)
        elif kpi.compute in _TWO_ARG_CALCS:
            data = kpi.compute(engine, store_id)
        else:
            data = kpi.compute(engine, store_id, days)
    except Exception as exc:
        logger.warning("KPI %s failed: %s", kpi_id, exc)
        return {**meta, "value": None, "data": None, "error": str(exc)}

    primary = data.get(kpi.primary_field) if kpi.primary_field else None
    return {**meta, "value": primary, "data": data,
            "trend": data.get("trend"),
            "store_id": store_id, "days": days}


# ── Summary endpoint (registry-driven, all 46 KPIs) ──────────────────────────

@router.get("/summary",
            summary="All-KPIs summary — one card per KPI in the master registry (46)")
async def kpi_summary(
    request: Request,
    store_id: int = Query(1),
    vertical: str | None = Query(None, description="Optional filter by vertical"),
    user: dict    = Depends(_auth),
):
    engine = _engine(request)
    sname  = _sname(engine, store_id)
    ma     = request.app.state.kirana_service.ml
    today  = date.today()

    cards: list[dict] = []
    errors: dict[str, str] = {}

    items = kpi_registry.all_kpis()
    if vertical:
        items = [k for k in items if k.vertical == vertical]

    for kpi in items:
        meta = kpi_registry.kpi_to_metadata(kpi)
        # kpi_key is the snake_case slug the Flutter app uses for card.find()
        kpi_key = (kpi.endpoint_slug or kpi.kpi_id).replace("-", "_")
        if kpi.status != kpi_registry.STATUS_OK or kpi.compute is None:
            cards.append({
                **meta,
                "kpi_key":          kpi_key,
                "value":            None,
                "trend_direction":  None,
                "trend_pct_change": None,
            })
            continue

        try:
            if kpi.compute is calc.calc_morning_stock_readiness:
                data = kpi.compute(engine, store_id, ma)
            elif kpi.compute in _TWO_ARG_CALCS:
                data = kpi.compute(engine, store_id)
            else:
                data = kpi.compute(engine, store_id, 30)
        except Exception as exc:
            logger.warning("KPI %s failed: %s", kpi.kpi_id, exc)
            errors[kpi.kpi_id] = str(exc)
            cards.append({**meta, "kpi_key": kpi_key, "value": None,
                          "trend_direction": None, "trend_pct_change": None,
                          "error": str(exc)})
            continue

        trend = data.get("trend") or {}
        primary = data.get(kpi.primary_field) if kpi.primary_field else None
        cards.append({
            **meta,
            "kpi_key":          kpi_key,
            "value":            primary,
            "trend_direction":  trend.get("direction"),
            "trend_pct_change": trend.get("pct_change"),
        })

    counts = {
        "total":            len(cards),
        "ok":               sum(1 for c in cards if c.get("status") == kpi_registry.STATUS_OK and c.get("error") is None),
        "data_unavailable": sum(1 for c in cards if c.get("status") == kpi_registry.STATUS_DATA_UNAVAILABLE),
        "errors":           len(errors),
    }
    return {
        "store_id":     store_id,
        "store_name":   sname,
        "as_of":        today,
        "vertical":     vertical,
        "counts":       counts,
        "kpis":         cards,
        "errors":       errors,
        "last_updated": datetime.now(timezone.utc),
    }
