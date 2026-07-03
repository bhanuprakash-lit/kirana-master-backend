"""
ML Adapter — bridges the ml_models/results CSVs to RecommendationItem objects.

Design notes:
  - All five prediction CSVs are first joined into a single per-(store, product)
    "ml_state" frame. Every emitted recommendation row pulls its display
    metrics from that joined state, so a stockout card shows real demand and
    a reorder card shows real stockout probability — no zeroed-out fields.
  - A stockout signal is suppressed for products with negligible velocity
    (< MIN_VELOCITY units/day), because a "100% risk" on a SKU that never
    sells is noise, not insight.
  - Each recommendation type carries the same shape so the UI can render
    consistent pills regardless of which tab it lives in.
"""
from __future__ import annotations

import os
import time
import logging
from typing import Any

import pandas as pd
import numpy as np
from sqlalchemy.engine import Engine
from sqlalchemy import text

logger = logging.getLogger("kirana.ml_adapter")


# Stockout cards below this avg_units_sold are suppressed — a SKU that
# sells <0.3 units/day going "out of stock" is not a useful action.
MIN_VELOCITY_FOR_STOCKOUT = 0.3

# Dead-stock is mutually exclusive with active selling. We require BOTH
# the model to flag dead stock AND the recent-window data to confirm:
# avg sales below this threshold is what "no sales" means in practice.
MAX_VELOCITY_FOR_DEADSTOCK = 0.3

# Predictions older than this are considered stale (kirana demand shifts fast).
ML_STALE_AFTER_HOURS = 36

# The result CSVs the adapter depends on.
ML_RESULT_FILES = [
    "stockout_predictions.csv",
    "margin_predictions.csv",
    "velocity_predictions.csv",
    "reorder_recommendations.csv",
    "deadstock_predictions.csv",
]


def _load(path: str, label: str) -> pd.DataFrame:
    if not os.path.exists(path):
        logger.warning("ML results file not found: %s — returning empty frame", path)
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        logger.info("Loaded %s: %d rows", label, len(df))
        return df
    except Exception as exc:
        logger.error("Failed to load %s: %s", label, exc)
        return pd.DataFrame()


def _store_carriers_for(engine: Engine | None, product_ids: list[int]) -> dict[int, list[int]]:
    """Return {product_id: [store_ids that physically carry it]}.

    Only used as a defensive fallback for legacy CSVs that lack store_id.
    With per-store models this should never trigger.
    """
    if engine is None or not product_ids:
        return {}
    sql = text(
        """
        SELECT product_id, store_id
        FROM kirana_oltp.inventory
        WHERE product_id = ANY(:pids)
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"pids": list(set(int(p) for p in product_ids))}).all()
    out: dict[int, list[int]] = {}
    for pid, sid in rows:
        out.setdefault(int(pid), []).append(int(sid))
    return out


class MLAdapter:
    """Loads all ML result CSVs and exposes a unified per-(store,product,type) frame."""

    def __init__(self, results_dir: str, engine: Engine | None = None):
        self.results_dir = results_dir
        self._engine = engine
        self._frame: pd.DataFrame | None = None
        # ml_state is the joined per-(store, product) signal frame; kept so
        # the explainer/agent endpoints can reach the raw numbers.
        self._ml_state: pd.DataFrame | None = None

    def _path(self, name: str) -> str:
        return os.path.join(self.results_dir, name)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _resolve_store_ids(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure df has a store_id column; fan out via inventory if missing."""
        if df.empty:
            return df
        if "store_id" in df.columns and df["store_id"].notna().all():
            df["store_id"] = df["store_id"].astype(int)
            return df
        carriers = _store_carriers_for(self._engine, df["product_id"].tolist())
        if not carriers:
            logger.warning(
                "No store_id in %d ML rows and no inventory mapping available; "
                "dropping rows.", len(df)
            )
            return df.iloc[0:0]
        rows = []
        for _, r in df.iterrows():
            for sid in carriers.get(int(r["product_id"]), []):
                row = r.to_dict()
                row["store_id"] = sid
                rows.append(row)
        return pd.DataFrame(rows)

    def _build_ml_state(
        self,
        stockout: pd.DataFrame,
        reorder: pd.DataFrame,
        velocity: pd.DataFrame,
        margin: pd.DataFrame,
        deadstk: pd.DataFrame,
    ) -> pd.DataFrame:
        """Join all five prediction CSVs into one row per (store, product).

        The result is the source of truth for every emitted card — any field
        a card needs (current_stock, forecast_demand, stockout_prob, margin,
        days-to-stockout, ...) comes from here so we never zero things out.
        """
        velocity = self._resolve_store_ids(velocity)
        margin   = self._resolve_store_ids(margin)
        deadstk  = self._resolve_store_ids(deadstk)

        if reorder.empty:
            logger.warning("Reorder predictions empty — base ml_state will be sparse")
            base = pd.DataFrame(columns=["store_id", "product_id"])
        else:
            base = reorder[[
                "store_id", "product_id",
                "current_stock", "avg_daily_sales", "reorder_point",
                "predicted_reorder_qty", "needs_reorder",
                "days_until_stockout", "lead_time_days", "eoq",
            ]].copy()

        # Pull product display fields from any frame that has them.
        for source in (reorder, stockout, velocity, margin, deadstk):
            if source.empty:
                continue
            cols_present = [c for c in ("name", "sku", "category_name") if c in source.columns]
            if cols_present and "product_id" in source.columns:
                product_meta = source[["product_id", *cols_present]].drop_duplicates("product_id")
                base = base.merge(product_meta, on="product_id", how="left")
                break

        if not stockout.empty:
            so = stockout[["store_id", "product_id",
                           "prob_stockout_3d", "prob_stockout_7d",
                           "prob_stockout_30d"]].copy()
            base = base.merge(so, on=["store_id", "product_id"], how="left")

        if not velocity.empty:
            ve_cols = ["store_id", "product_id"]
            for c in ("prob_fast_moving", "is_fast_moving",
                      "is_slow_moving", "velocity_score",
                      "avg_units_sold", "avg_stock", "avg_margin"):
                if c in velocity.columns:
                    ve_cols.append(c)
            base = base.merge(velocity[ve_cols], on=["store_id", "product_id"], how="left")

        if not margin.empty:
            mg_cols = ["store_id", "product_id"]
            for c in ("prob_high_margin", "is_high_margin",
                      "effective_margin", "avg_price"):
                if c in margin.columns:
                    mg_cols.append(c)
            base = base.merge(margin[mg_cols], on=["store_id", "product_id"], how="left")

        if not deadstk.empty:
            ds_cols = ["store_id", "product_id"]
            for c in ("prob_dead_stock", "is_dead_stock", "anomaly_score",
                      "recent_avg_sales", "recent_avg_stock"):
                if c in deadstk.columns:
                    ds_cols.append(c)
            base = base.merge(deadstk[ds_cols], on=["store_id", "product_id"], how="left")

        # Derived fields used across all card types
        base["effective_margin_pct"] = base.get(
            "effective_margin", pd.Series(0.0, index=base.index)
        ).fillna(0.0)
        base["expected_profit"] = (
            base.get("avg_daily_sales", pd.Series(0.0, index=base.index)).fillna(0)
            * (base["effective_margin_pct"] / 100.0)
            * 30.0  # rough 30-day projection
        ).round(2)

        return base

    def _row_signal(self, r: pd.Series) -> dict:
        """Common metric fields shared by every recommendation card."""
        def _f(col, default=0.0):
            v = r.get(col)
            try:
                return float(v) if pd.notna(v) else default
            except (TypeError, ValueError):
                return default

        return {
            "current_stock":       _f("current_stock"),
            "forecast_demand":     _f("avg_daily_sales") or _f("avg_units_sold"),
            "avg_units_sold":      _f("avg_units_sold"),
            "avg_daily_sales":     _f("avg_daily_sales"),
            "stockout_prob":       _f("prob_stockout_7d"),
            "prob_stockout_3d":    _f("prob_stockout_3d"),
            "prob_stockout_7d":    _f("prob_stockout_7d"),
            "prob_stockout_30d":   _f("prob_stockout_30d"),
            "days_to_stockout":    _f("days_until_stockout"),
            "reorder_qty":         _f("predicted_reorder_qty"),
            "reorder_point":       _f("reorder_point"),
            "lead_time_days":      _f("lead_time_days"),
            "effective_margin":    _f("effective_margin_pct") or _f("effective_margin"),
            "current_price":       _f("avg_price"),
            "expected_profit":     _f("expected_profit"),
        }

    # ── Refresh ──────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        stockout = _load(self._path("stockout_predictions.csv"), "stockout")
        margin   = _load(self._path("margin_predictions.csv"),   "margin")
        velocity = _load(self._path("velocity_predictions.csv"), "velocity")
        reorder  = _load(self._path("reorder_recommendations.csv"), "reorder")
        deadstk  = _load(self._path("deadstock_predictions.csv"), "deadstock")

        ml_state = self._build_ml_state(stockout, reorder, velocity, margin, deadstk)
        self._ml_state = ml_state

        rows: list[dict[str, Any]] = []

        for _, r in ml_state.iterrows():
            common = {
                "store_id":      int(r["store_id"]),
                "sku_id":        int(r["product_id"]),
                "product_name":  r.get("name") or "",
                "category_name": r.get("category_name") or "",
            }
            sig = self._row_signal(r)
            avg_velocity = sig["avg_units_sold"] or sig["avg_daily_sales"]

            # ── Stockout: only when 7d-prob is high AND velocity is non-trivial
            prob_7d = sig["prob_stockout_7d"]
            if prob_7d >= 0.5 and avg_velocity >= MIN_VELOCITY_FOR_STOCKOUT:
                rows.append({**common, **sig,
                             "recommendation_type": "stockout_risk"})

            # ── Reorder: needs_reorder=1 AND qty>0 (rule from optimizer)
            qty   = float(r.get("predicted_reorder_qty") or 0)
            needs = int(r.get("needs_reorder") or 0)
            if needs == 1 and qty > 0:
                rows.append({**common, **sig,
                             "recommendation_type": "reorder_now",
                             "reorder_qty": qty})

            # ── Fast moving
            if int(r.get("is_fast_moving") or 0) == 1:
                rows.append({**common, **sig,
                             "recommendation_type": "fast_moving"})

            # ── High margin / profit opportunity
            if int(r.get("is_high_margin") or 0) == 1:
                rows.append({**common, **sig,
                             "recommendation_type": "profit_opportunity"})

            # ── Dead stock — model flag PLUS velocity sanity check.
            # IsolationForest fallback can flag fast-movers as anomalous;
            # require the velocity to actually be near zero before showing it.
            recent_velocity = sig["avg_units_sold"]
            if (int(r.get("is_dead_stock") or 0) == 1 and
                    recent_velocity <= MAX_VELOCITY_FOR_DEADSTOCK and
                    int(r.get("is_fast_moving") or 0) == 0):
                rows.append({**common, **sig,
                             "recommendation_type": "dead_stock"})

        df = pd.DataFrame(rows) if rows else pd.DataFrame()

        if not df.empty:
            before = len(df)
            df = df.drop_duplicates(
                subset=["store_id", "sku_id", "recommendation_type"],
                keep="first",
            )
            if before != len(df):
                logger.info("MLAdapter de-duped %d duplicate rows", before - len(df))
            df = df.replace([float("inf"), float("-inf")], None)
            df = df.where(df.notna(), None)

        self._frame = df
        logger.info(
            "MLAdapter refresh complete: %d recommendation rows across %d stores",
            len(df), df["store_id"].nunique() if not df.empty else 0,
        )

        # Freshness guard: warn loudly if any model file is missing or stale, so
        # degraded ML output isn't silent.
        fresh = self.freshness()
        if fresh["any_missing"]:
            missing = [f["file"] for f in fresh["files"] if not f["present"]]
            logger.warning("ML predictions MISSING: %s — recommendations degraded. "
                           "Run: python ml_models/train_all.py", missing)
        elif fresh["stale"]:
            logger.warning("ML predictions STALE: oldest is %.1fh old (> %dh). "
                           "Consider retraining: python ml_models/train_all.py",
                           fresh["oldest_age_hours"], ML_STALE_AFTER_HOURS)

    def freshness(self) -> dict[str, Any]:
        """Per-file age + an overall stale flag for the prediction CSVs."""
        now = time.time()
        files: list[dict[str, Any]] = []
        any_missing = False
        oldest_hours = 0.0
        for name in ML_RESULT_FILES:
            path = self._path(name)
            if os.path.exists(path):
                age_h = round((now - os.path.getmtime(path)) / 3600, 1)
                files.append({"file": name, "present": True, "age_hours": age_h})
                oldest_hours = max(oldest_hours, age_h)
            else:
                any_missing = True
                files.append({"file": name, "present": False, "age_hours": None})
        return {
            "stale": any_missing or oldest_hours > ML_STALE_AFTER_HOURS,
            "any_missing": any_missing,
            "oldest_age_hours": oldest_hours,
            "stale_after_hours": ML_STALE_AFTER_HOURS,
            "results_dir": self.results_dir,
            "files": files,
        }

    def flags_for_store(self, store_id: int) -> dict[int, list[str]]:
        """{product_id: [recommendation_type, ...]} for one store — drives the
        ML flag tags (fast_moving / reorder_now / dead_stock / ...) on items."""
        df = self.get_frame()
        if df is None or df.empty or "store_id" not in df.columns:
            return {}
        sub = df[df["store_id"] == store_id]
        out: dict[int, list[str]] = {}
        for _, r in sub.iterrows():
            pid = int(r["sku_id"])
            out.setdefault(pid, []).append(str(r["recommendation_type"]))
        return out

    def get_frame(self) -> pd.DataFrame:
        if self._frame is None:
            self.refresh()
        return self._frame

    def get_ml_state(self) -> pd.DataFrame | None:
        if self._ml_state is None:
            self.refresh()
        return self._ml_state

    # ── Quick summary for a single store ──────────────────────────────────────

    def store_summary(self, store_id: int) -> dict:
        df = self.get_frame()
        if df.empty:
            return {"store_id": store_id, "stockout_risk": 0, "reorder": 0,
                    "fast_moving": 0, "profit": 0, "dead_stock": 0}
        sdf = df[df["store_id"] == store_id]
        return {
            "store_id":      store_id,
            "stockout_risk": int((sdf["recommendation_type"] == "stockout_risk").sum()),
            "reorder":       int((sdf["recommendation_type"] == "reorder_now").sum()),
            "fast_moving":   int((sdf["recommendation_type"] == "fast_moving").sum()),
            "profit":        int((sdf["recommendation_type"] == "profit_opportunity").sum()),
            "dead_stock":    int((sdf["recommendation_type"] == "dead_stock").sum()),
        }

    # ── Helper queries used by other modules ──────────────────────────────────

    def get_stockout_products(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        df = self.get_frame()
        if df.empty:
            return []
        mask = df["recommendation_type"] == "stockout_risk"
        if store_id:
            mask &= df["store_id"] == store_id
        sub = df[mask].nlargest(top_n, "prob_stockout_7d")
        return sub[["sku_id", "product_name", "category_name",
                    "prob_stockout_7d", "days_to_stockout"]].to_dict("records")

    def get_fast_moving(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        df = self.get_frame()
        if df.empty:
            return []
        mask = df["recommendation_type"] == "fast_moving"
        if store_id:
            mask &= df["store_id"] == store_id
        sub = df[mask].nlargest(top_n, "forecast_demand")
        return sub[["sku_id", "product_name", "category_name", "forecast_demand"]].to_dict("records")

    def get_high_margin(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        df = self.get_frame()
        if df.empty:
            return []
        mask = df["recommendation_type"] == "profit_opportunity"
        if store_id:
            mask &= df["store_id"] == store_id
        sub = df[mask].nlargest(top_n, "effective_margin")
        return sub[["sku_id", "product_name", "category_name", "effective_margin"]].to_dict("records")

    def get_dead_stock(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        df = self.get_frame()
        if df.empty:
            return []
        mask = df["recommendation_type"] == "dead_stock"
        if store_id:
            mask &= df["store_id"] == store_id
        sub = df[mask].nlargest(top_n, "current_stock")
        return sub[["sku_id", "product_name", "category_name", "current_stock"]].to_dict("records")
