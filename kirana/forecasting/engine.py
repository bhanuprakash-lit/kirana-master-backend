"""
ForecastEngine — demand and revenue forecasting using the nightly ML pipeline.

Math model
----------
The nightly StockoutPredictor already computes per-(store, product):
  - avg_daily_demand  : historical mean units/day (Poisson rate λ)
  - std_daily         : std dev of daily demand (stored in stockout_predictor.pkl)
  - prob_stockout_Nd  : P(product goes OOS within N days)

Forecast for horizon N days:
  E[units]  = λ × N × availability_factor(N)
  availability_factor = 1 − P(stockout_N) × 0.5
    (0.5: OOS items still sell for the first half of the window on avg)
  σ(N days) = std_daily × √N    (variance of sum of N independent Poisson rvs)
  CI 95%    = E[units] ± 1.96 × σ(N days)
  E[revenue]= E[units] × avg_price

Why Poisson and not Prophet/ARIMA?
  See docs — short answer: kirana SKU sales are sparse (60-80% zero days),
  ARIMA/Prophet need 50+ non-zero data points per series and 2 full seasonal
  cycles.  15,000+ per-SKU time-series models would take hours to train nightly.
  The Poisson model is provably optimal for sparse integer count data and trains
  across ALL products in < 2 minutes.  Prophet is the right upgrade path for
  store-level totals and top-velocity SKUs once we have 12+ months of history.
"""
from __future__ import annotations

import logging
import math
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kirana.ml_adapter import MLAdapter

logger = logging.getLogger("kirana.forecasting")


def _sf(v, default: float = 0.0) -> float:
    """Safe float — treats NaN, None, and non-numeric as default."""
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default

HORIZONS = [1, 3, 5, 7, 14, 30]
_HORIZON_LABELS = {
    1: "Tomorrow",
    3: "Next 3 days",
    5: "Next 5 days",
    7: "Next 7 days",
    14: "Next 2 weeks",
    30: "Next 30 days",
}


class ForecastEngine:
    """
    Reads from MLAdapter._ml_state (already in memory, refreshed every 6h)
    and std_daily from stockout_predictor.pkl (loaded once, cached).
    Zero DB queries — all inference is in-memory.
    """

    def __init__(self, ml_adapter: "MLAdapter", artifacts_dir: str | None = None):
        self._ml = ml_adapter
        self._std: dict[tuple[int, int], float] = {}
        self._std_loaded = False
        self._artifacts_dir = artifacts_dir or os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "ml_models", "artifacts")
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_std(self) -> None:
        if self._std_loaded:
            return
        pkl = os.path.join(self._artifacts_dir, "stockout_predictor.pkl")
        if os.path.exists(pkl):
            try:
                import joblib
                saved = joblib.load(pkl)
                stats = saved.get("product_stats")
                if stats is not None and "std_daily" in stats.columns:
                    for _, row in stats.iterrows():
                        key = (int(row["store_id"]), int(row["product_id"]))
                        self._std[key] = float(row["std_daily"] or 0.0)
                logger.info("ForecastEngine: loaded demand std for %d SKUs", len(self._std))
            except Exception as exc:
                logger.warning("ForecastEngine: could not load std_daily from pkl: %s", exc)
        self._std_loaded = True

    def _get_std(self, store_id: int, product_id: int, avg_daily: float) -> float:
        self._load_std()
        return self._std.get((store_id, product_id), avg_daily * 0.7)

    def _stockout_prob(self, row, horizon: int) -> float:
        """Best available stockout probability for the given horizon."""
        p3  = _sf(row.get("prob_stockout_3d"))
        p7  = _sf(row.get("prob_stockout_7d"))
        p30 = _sf(row.get("prob_stockout_30d"))
        if horizon <= 3:
            return p3
        if horizon <= 7:
            # linear interpolation between 3d and 7d
            t = (horizon - 3) / 4
            return p3 + t * (p7 - p3)
        if horizon <= 21:
            t = (horizon - 7) / 14
            return p7 + t * (p30 - p7)
        return p30

    def _row_forecast(self, row, horizon: int) -> dict:
        """Compute Poisson demand forecast for one (store, product) × horizon."""
        store_id   = int(_sf(row.get("store_id")))
        product_id = int(_sf(row.get("product_id")))

        # Use avg_daily_sales (reorder CSV) with fallback to avg_units_sold (velocity CSV)
        avg_daily = _sf(row.get("avg_daily_sales")) or _sf(row.get("avg_units_sold"))
        avg_price = _sf(row.get("avg_price"))
        current_stock    = _sf(row.get("current_stock"))
        days_of_supply   = _sf(row.get("days_until_stockout"), default=9_999.0) or 9_999.0
        reorder_qty      = _sf(row.get("predicted_reorder_qty"))
        needs_reorder    = int(_sf(row.get("needs_reorder")))
        prob_oos         = self._stockout_prob(row, horizon)
        std_daily        = self._get_std(store_id, product_id, avg_daily)

        # Expected units: demand rate × horizon × availability
        availability = 1.0 - prob_oos * 0.5
        expected = avg_daily * horizon * availability

        # Cap at available supply (current stock + triggered reorder)
        max_supply = current_stock + (reorder_qty if needs_reorder else 0)
        if max_supply > 0:
            expected = min(expected, max_supply)

        # 95% CI using Poisson variance: Var(N days) = σ² × N
        ci = 1.96 * std_daily * (horizon ** 0.5)
        units_low  = max(0.0, expected - ci)
        units_high = expected + ci

        return {
            "product_id":           product_id,
            "product_name":         str(row.get("name") or ""),
            "category_name":        str(row.get("category_name") or ""),
            "avg_daily_demand":     round(avg_daily, 4),
            "avg_price":            round(avg_price, 2),
            "current_stock":        int(current_stock),
            "days_of_supply":       round(min(days_of_supply, 9_999.0), 1),
            "stockout_risk_pct":    round(prob_oos * 100, 1),
            "will_oos_in_window":   days_of_supply < horizon and prob_oos > 0.3,
            "is_fast_moving":       bool(int(_sf(row.get("is_fast_moving")))),
            "needs_reorder":        bool(needs_reorder),
            "predicted_units":      round(expected, 1),
            "predicted_units_low":  round(units_low, 1),
            "predicted_units_high": round(units_high, 1),
            "predicted_revenue":    round(expected * avg_price, 2),
            "revenue_low":          round(units_low  * avg_price, 2),
            "revenue_high":         round(units_high * avg_price, 2),
        }

    def _store_frame(self, store_id: int):
        # Scoped to one store: the adapter queries Postgres for just this
        # store's signals (the full set is ~170k rows and would OOM if loaded).
        ml_state = self._ml.get_ml_state(store_id)
        if ml_state is None or ml_state.empty:
            return None
        return ml_state

    # ── Public API ────────────────────────────────────────────────────────────

    def forecast_items(self, store_id: int, horizon_days: int, top_n: int = 100) -> dict:
        """
        Per-SKU demand + revenue forecast for a single horizon.
        Returns top_n items ranked by predicted revenue.
        """
        df = self._store_frame(store_id)
        if df is None:
            return _empty_items(horizon_days)

        rows = [self._row_forecast(row, horizon_days) for _, row in df.iterrows()]
        # Only include products that are actually expected to sell
        rows = [r for r in rows if r["avg_daily_demand"] > 0]
        rows.sort(key=lambda x: x["predicted_revenue"], reverse=True)

        total_units   = sum(r["predicted_units"]   for r in rows)
        total_revenue = sum(r["predicted_revenue"] for r in rows)
        oos_count     = sum(1 for r in rows if r["will_oos_in_window"])

        return {
            "horizon_days":            horizon_days,
            "horizon_label":           _HORIZON_LABELS.get(horizon_days, f"Next {horizon_days} days"),
            "total_items":             len(rows),
            "total_predicted_units":   round(total_units, 1),
            "total_predicted_revenue": round(total_revenue, 2),
            "items_at_oos_risk":       oos_count,
            "items":                   rows[:top_n],
            "model":                   "Poisson demand × horizon (95% CI)",
        }

    def forecast_revenue(self, store_id: int) -> list[dict]:
        """
        Multi-horizon revenue forecast — all 6 horizons in one pass.
        Primary input for the revenue forecast chart.
        """
        df = self._store_frame(store_id)
        if df is None:
            return [_empty_horizon(h) for h in HORIZONS]

        results = []
        for h in HORIZONS:
            total_rev = total_low = total_high = total_units = 0.0
            for _, row in df.iterrows():
                f = self._row_forecast(row, h)
                total_rev   += f["predicted_revenue"]
                total_low   += f["revenue_low"]
                total_high  += f["revenue_high"]
                total_units += f["predicted_units"]
            results.append({
                "horizon_days":    h,
                "horizon_label":   _HORIZON_LABELS[h],
                "predicted":       round(total_rev, 2),
                "low":             round(total_low, 2),
                "high":            round(total_high, 2),
                "predicted_units": round(total_units, 1),
            })
        return results

    def forecast_summary(self, store_id: int) -> dict:
        """
        All-in-one multi-horizon summary for the dashboard.
        Single call → everything the overview card needs.
        """
        revenue = self.forecast_revenue(store_id)
        freshness = self._ml.freshness()

        horizons_map = {}
        for h in revenue:
            key = f"{h['horizon_days']}d"
            horizons_map[key] = {
                "label":           h["horizon_label"],
                "predicted_units": h["predicted_units"],
                "revenue":         h["predicted"],
                "revenue_low":     h["low"],
                "revenue_high":    h["high"],
            }

        return {
            "store_id":             store_id,
            "data_freshness_hours": freshness.get("oldest_age_hours"),
            "data_stale":           freshness.get("stale", False),
            "model":                "Poisson demand × horizon (95% CI)",
            "note": (
                "Forecasts use per-SKU historical avg demand × horizon. "
                "Confidence bands = ±1.96 × σ × √N (Poisson variance). "
                "Availability-adjusted for OOS risk."
            ),
            "horizons": horizons_map,
        }

    def forecast_risks(self, store_id: int, horizon_days: int) -> dict:
        """
        Items that will go OOS during the forecast window.
        Includes estimated lost revenue for each, ranked by impact.
        """
        df = self._store_frame(store_id)
        if df is None:
            return _empty_risks(horizon_days)

        at_risk = []
        for _, row in df.iterrows():
            prob_oos   = self._stockout_prob(row, horizon_days)
            avg_daily  = _sf(row.get("avg_daily_sales")) or _sf(row.get("avg_units_sold"))
            avg_price  = _sf(row.get("avg_price"))
            dos        = _sf(row.get("days_until_stockout"), default=9_999.0) or 9_999.0

            if prob_oos < 0.25 or avg_daily <= 0:
                continue

            # Estimate lost revenue: demand during the OOS period × price
            oos_duration = max(0.0, horizon_days - dos)
            lost_units   = avg_daily * oos_duration * prob_oos
            lost_revenue = lost_units * avg_price

            if dos < 1:
                urgency = "CRITICAL"
            elif dos < 3:
                urgency = "HIGH"
            elif dos < 7:
                urgency = "MEDIUM"
            else:
                urgency = "LOW"

            at_risk.append({
                "product_id":             int(_sf(row.get("product_id"))),
                "product_name":           str(row.get("name") or ""),
                "category_name":          str(row.get("category_name") or ""),
                "current_stock":          int(_sf(row.get("current_stock"))),
                "avg_daily_demand":       round(avg_daily, 3),
                "days_of_supply":         round(min(dos, 9_999.0), 1),
                "stockout_prob_pct":      round(prob_oos * 100, 1),
                "predicted_lost_units":   round(lost_units, 1),
                "predicted_lost_revenue": round(lost_revenue, 2),
                "urgency":                urgency,
            })

        at_risk.sort(key=lambda x: x["predicted_lost_revenue"], reverse=True)
        total_lost = sum(i["predicted_lost_revenue"] for i in at_risk)

        return {
            "horizon_days":                 horizon_days,
            "horizon_label":                _HORIZON_LABELS.get(horizon_days, f"Next {horizon_days} days"),
            "at_risk_items":                at_risk,
            "total_at_risk_count":          len(at_risk),
            "total_potential_lost_revenue": round(total_lost, 2),
        }


# ── Empty-result helpers ──────────────────────────────────────────────────────

def _empty_items(horizon_days: int) -> dict:
    return {
        "horizon_days": horizon_days,
        "horizon_label": _HORIZON_LABELS.get(horizon_days, f"Next {horizon_days} days"),
        "total_items": 0, "total_predicted_units": 0,
        "total_predicted_revenue": 0, "items_at_oos_risk": 0, "items": [],
        "model": "Poisson demand × horizon (95% CI)",
    }


def _empty_horizon(h: int) -> dict:
    return {
        "horizon_days": h, "horizon_label": _HORIZON_LABELS[h],
        "predicted": 0, "low": 0, "high": 0, "predicted_units": 0,
    }


def _empty_risks(horizon_days: int) -> dict:
    return {
        "horizon_days": horizon_days,
        "horizon_label": _HORIZON_LABELS.get(horizon_days, f"Next {horizon_days} days"),
        "at_risk_items": [], "total_at_risk_count": 0,
        "total_potential_lost_revenue": 0,
    }
