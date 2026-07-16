"""
Forecast Routes — demand and revenue forecasting endpoints.

All endpoints are read-only (GET), zero DB queries.
Inference is fully in-memory using the MLAdapter state refreshed every 6h.

Endpoints:
  GET /kirana/forecast/summary       — all horizons in one call (dashboard card)
  GET /kirana/forecast/items         — per-SKU demand + revenue for a horizon
  GET /kirana/forecast/revenue       — store revenue across all 6 horizons
  GET /kirana/forecast/risks         — items at OOS risk with lost-revenue estimate
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from kirana.forecasting.engine import ForecastEngine, HORIZONS

logger = logging.getLogger("kirana.forecasting.routes")
router = APIRouter(prefix="/kirana/forecast", tags=["Forecasting"])


# ── Shared helpers ────────────────────────────────────────────────────────────

def _auth(request: Request) -> dict:
    svc = request.app.state.kirana_service
    s   = request.app.state.settings
    api_key  = request.headers.get("X-API-Key", "")
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
    """Router-level IDOR guard: a store-scoped user may only forecast their
    own store (must pass their own store_id); admins may query any. Read from
    the raw query string so it can't interact with each route's store_id
    default. Applied once to every forecast route."""
    if user.get("role") == "admin":
        return
    owned = user.get("store_id")
    if owned is None:
        raise HTTPException(status_code=403, detail="No store assigned to this user")
    raw = request.query_params.get("store_id")
    if raw is None or int(raw) != int(owned):
        raise HTTPException(status_code=403, detail="Access denied to this store")


router.dependencies.append(Depends(_enforce_store_scope))


def _forecast_engine(request: Request) -> ForecastEngine:
    svc = request.app.state.kirana_service
    return ForecastEngine(ml_adapter=svc.ml)


def _valid_horizon(days: int) -> int:
    if days not in (1, 3, 5, 7, 14, 30):
        raise HTTPException(
            status_code=400,
            detail=f"horizon_days must be one of {HORIZONS}. Got {days}."
        )
    return days


# ── 1. Summary (all horizons in one call) ─────────────────────────────────────

@router.get(
    "/summary",
    summary="Demand + Revenue forecast — all horizons (1/3/5/7/14/30 days)",
)
async def forecast_summary(
    request: Request,
    store_id: int = Query(..., description="Store ID"),
    user: dict    = Depends(_auth),
):
    """
    Returns demand + revenue forecast for every horizon in a single response.
    Use this for the main dashboard forecast card — one round-trip shows all windows.

    Revenue CI: ±1.96σ√N (Poisson, 95%). Adjust for OOS probability per window.
    """
    engine = _forecast_engine(request)
    result = engine.forecast_summary(store_id)
    if not result.get("horizons"):
        raise HTTPException(
            status_code=503,
            detail="ML predictions not yet available. Run: python ml_models/train_all.py"
        )
    return result


# ── 2. Per-SKU items forecast ─────────────────────────────────────────────────

@router.get(
    "/items",
    summary="Per-SKU demand + revenue forecast for a single horizon",
)
async def forecast_items(
    request: Request,
    store_id:     int = Query(...,  description="Store ID"),
    horizon_days: int = Query(7,   description="Forecast horizon: 1/3/5/7/14/30"),
    top_n:        int = Query(100, ge=1, le=500, description="Max SKUs to return (ranked by revenue)"),
    user: dict        = Depends(_auth),
):
    """
    Returns per-SKU predicted units and revenue for the chosen horizon.
    Items are sorted by predicted revenue descending — top revenue drivers first.

    Includes:
    - `predicted_units` ± CI (95%)
    - `predicted_revenue` ± CI
    - `will_oos_in_window` flag
    - `stockout_risk_pct`
    - `days_of_supply`

    Use this to build the "What will we sell?" inventory planning table.
    """
    _valid_horizon(horizon_days)
    engine = _forecast_engine(request)
    result = engine.forecast_items(store_id, horizon_days, top_n=top_n)
    if result["total_items"] == 0:
        raise HTTPException(
            status_code=503,
            detail="ML predictions not yet available or store has no active SKUs."
        )
    return result


# ── 3. Store revenue forecast (multi-horizon) ─────────────────────────────────

@router.get(
    "/revenue",
    summary="Store revenue forecast across all horizons (1/3/5/7/14/30 days)",
)
async def forecast_revenue(
    request: Request,
    store_id: int = Query(..., description="Store ID"),
    user: dict    = Depends(_auth),
):
    """
    Returns total predicted revenue for the store across all 6 forecast horizons.
    Each horizon includes a `low`/`high` confidence band (95% Poisson CI).

    Use this for the revenue forecast trend chart.

    Example response item:
    ```json
    {"horizon_days": 7, "horizon_label": "Next 7 days",
     "predicted": 87500, "low": 72000, "high": 103000, "predicted_units": 1750}
    ```
    """
    engine = _forecast_engine(request)
    results = engine.forecast_revenue(store_id)
    if not results or all(r["predicted"] == 0 for r in results):
        raise HTTPException(
            status_code=503,
            detail="ML predictions not yet available for this store."
        )
    return {
        "store_id":       store_id,
        "revenue_by_horizon": results,
        "model":          "Poisson demand × horizon (95% CI)",
        "horizons_available": [r["horizon_days"] for r in results],
    }


# ── 4. OOS risk items ─────────────────────────────────────────────────────────

@router.get(
    "/risks",
    summary="Items at OOS risk during the forecast window — with lost revenue estimate",
)
async def forecast_risks(
    request: Request,
    store_id:     int = Query(..., description="Store ID"),
    horizon_days: int = Query(7,   description="Forecast horizon: 1/3/5/7/14/30"),
    user: dict        = Depends(_auth),
):
    """
    Returns items that are likely to go out-of-stock within the forecast window,
    ranked by estimated lost revenue impact.

    Urgency levels:
    - CRITICAL: < 1 day of supply remaining
    - HIGH:     1–3 days
    - MEDIUM:   3–7 days
    - LOW:      > 7 days (still risky given the horizon)

    `predicted_lost_revenue` = avg_daily × OOS_duration × stockout_prob × avg_price.
    Use this to drive the "Act now" reorder alert panel.
    """
    _valid_horizon(horizon_days)
    engine = _forecast_engine(request)
    result = engine.forecast_risks(store_id, horizon_days)
    return result
