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
import math
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import numpy as np
from sqlalchemy.engine import Engine
from sqlalchemy import text

logger = logging.getLogger("kirana.ml_adapter")


def _json_safe(d: dict) -> dict:
    """Coerce a row dict to JSON-serialisable primitives — numpy scalars to
    native types, NaN/inf to None — so it can be stored as JSONB."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            out[k] = None
        elif isinstance(v, np.integer):
            out[k] = int(v)
        elif isinstance(v, np.bool_):
            out[k] = bool(v)
        elif isinstance(v, (float, np.floating)):
            fv = float(v)
            out[k] = None if (math.isnan(fv) or math.isinf(fv)) else fv
        elif isinstance(v, np.ndarray):
            out[k] = v.tolist()
        else:
            out[k] = v
    return out


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


def _load(path: str, label: str, usecols: set[str] | None = None) -> pd.DataFrame:
    if not os.path.exists(path):
        logger.warning("ML results file not found: %s — returning empty frame", path)
        return pd.DataFrame()
    try:
        # usecols keeps peak memory down on the large CSVs (the reorder set is
        # ~170k rows): only the columns _build_ml_state actually reads are
        # materialised. The lambda is tolerant of columns that aren't present.
        read_kwargs: dict[str, Any] = {}
        if usecols is not None:
            read_kwargs["usecols"] = lambda c, _keep=usecols: c in _keep
        df = pd.read_csv(path, **read_kwargs)
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

    # Only the columns _build_ml_state reads from the (large) reorder CSV.
    # Loading just these instead of the full ~170k-row × all-columns frame is
    # what keeps startup memory under the 1Gi container limit.
    _REORDER_COLS = {
        "store_id", "product_id", "current_stock", "avg_daily_sales",
        "reorder_point", "predicted_reorder_qty", "needs_reorder",
        "days_until_stockout", "lead_time_days", "eoq",
        "name", "sku", "category_name",
    }

    # ── CSV → recommendations computation (OFFLINE, memory-heavy) ─────────────
    #
    # This is the expensive step (loads the ~170k-row reorder CSV + joins +
    # row-wise build). It runs in `load_to_db()` — invoked by the training
    # pipeline where the CSVs live and RAM is generous — NOT in the API
    # request path. The API only ever queries the tables per store, so it can
    # never OOM on this data (which is what took the 1Gi container down).

    def _compute_from_csvs(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Build (ml_state signals, recommendation rows) from the result CSVs."""
        stockout = _load(self._path("stockout_predictions.csv"), "stockout")
        margin   = _load(self._path("margin_predictions.csv"),   "margin")
        velocity = _load(self._path("velocity_predictions.csv"), "velocity")
        reorder  = _load(self._path("reorder_recommendations.csv"), "reorder",
                         usecols=self._REORDER_COLS)
        deadstk  = _load(self._path("deadstock_predictions.csv"), "deadstock")

        ml_state = self._build_ml_state(stockout, reorder, velocity, margin, deadstk)

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

            prob_7d = sig["prob_stockout_7d"]
            if prob_7d >= 0.5 and avg_velocity >= MIN_VELOCITY_FOR_STOCKOUT:
                rows.append({**common, **sig, "recommendation_type": "stockout_risk"})

            qty   = float(r.get("predicted_reorder_qty") or 0)
            needs = int(r.get("needs_reorder") or 0)
            if needs == 1 and qty > 0:
                rows.append({**common, **sig, "recommendation_type": "reorder_now",
                             "reorder_qty": qty})

            if int(r.get("is_fast_moving") or 0) == 1:
                rows.append({**common, **sig, "recommendation_type": "fast_moving"})

            if int(r.get("is_high_margin") or 0) == 1:
                rows.append({**common, **sig, "recommendation_type": "profit_opportunity"})

            recent_velocity = sig["avg_units_sold"]
            if (int(r.get("is_dead_stock") or 0) == 1 and
                    recent_velocity <= MAX_VELOCITY_FOR_DEADSTOCK and
                    int(r.get("is_fast_moving") or 0) == 0):
                rows.append({**common, **sig, "recommendation_type": "dead_stock"})

        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if not df.empty:
            df = df.drop_duplicates(
                subset=["store_id", "sku_id", "recommendation_type"], keep="first")
            df = df.replace([float("inf"), float("-inf")], None)
            df = df.where(df.notna(), None)
        return ml_state, df

    def load_to_db(self) -> dict:
        """OFFLINE loader: compute recommendations + signals from the CSVs and
        write them into Postgres (kirana_oltp.ml_recommendations / ml_signals),
        replacing the previous snapshot. Run this from the training pipeline
        (after the CSVs are generated) — never in the API request path.
        Returns row counts. Requires an engine."""
        if self._engine is None:
            raise RuntimeError("load_to_db needs a database engine")
        import json
        ml_state, reco = self._compute_from_csvs()

        reco_records: list[dict] = []
        for row in reco.to_dict("records"):
            payload = {k: v for k, v in row.items()
                       if k not in ("store_id", "sku_id", "recommendation_type",
                                    "product_name", "category_name")}
            reco_records.append({
                "store_id": int(row["store_id"]), "sku_id": int(row["sku_id"]),
                "rtype": str(row["recommendation_type"]),
                "product_name": row.get("product_name") or "",
                "category_name": row.get("category_name") or "",
                "payload": json.dumps(_json_safe(payload)),
            })

        sig_records: list[dict] = []
        if not ml_state.empty:
            for row in ml_state.to_dict("records"):
                sig_records.append({
                    "store_id": int(row["store_id"]),
                    "product_id": int(row["product_id"]),
                    "payload": json.dumps(_json_safe(row)),
                })

        with self._engine.begin() as conn:
            self._ensure_tables(conn)
            conn.execute(text("TRUNCATE kirana_oltp.ml_recommendations"))
            conn.execute(text("TRUNCATE kirana_oltp.ml_signals"))
            if reco_records:
                conn.execute(text("""
                    INSERT INTO kirana_oltp.ml_recommendations
                        (store_id, sku_id, recommendation_type, product_name,
                         category_name, payload)
                    VALUES (:store_id, :sku_id, :rtype, :product_name,
                            :category_name, CAST(:payload AS JSONB))
                """), reco_records)
            if sig_records:
                # chunked insert keeps the loader's own memory bounded on the
                # large signals set.
                for i in range(0, len(sig_records), 5000):
                    conn.execute(text("""
                        INSERT INTO kirana_oltp.ml_signals (store_id, product_id, payload)
                        VALUES (:store_id, :product_id, CAST(:payload AS JSONB))
                    """), sig_records[i:i + 5000])
        # Invalidate the API-side cache so the next read picks up the new data.
        self._frame = None
        logger.info("MLAdapter.load_to_db: wrote %d recommendations, %d signals",
                    len(reco_records), len(sig_records))
        return {"recommendations": len(reco_records), "signals": len(sig_records)}

    @staticmethod
    def _ensure_tables(conn) -> None:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kirana_oltp.ml_recommendations (
                store_id            BIGINT NOT NULL,
                sku_id              BIGINT NOT NULL,
                recommendation_type TEXT   NOT NULL,
                product_name        TEXT,
                category_name       TEXT,
                payload             JSONB  NOT NULL DEFAULT '{}',
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (store_id, sku_id, recommendation_type)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ml_reco_store "
                          "ON kirana_oltp.ml_recommendations(store_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kirana_oltp.ml_signals (
                store_id   BIGINT NOT NULL,
                product_id BIGINT NOT NULL,
                payload    JSONB  NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (store_id, product_id)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ml_signals_store "
                          "ON kirana_oltp.ml_signals(store_id)"))

    # ── DB-backed reads (API path — light, per store, never loads CSVs) ───────

    def _read_recos(self, store_id: int | None = None) -> pd.DataFrame:
        """Recommendation rows from Postgres as the frame consumers expect
        (payload JSONB unpacked back into columns). Empty frame if the table
        doesn't exist yet (before the first loader run) — graceful, no error."""
        if self._engine is None:
            return pd.DataFrame()
        sql = ("SELECT store_id, sku_id, recommendation_type, product_name, "
               "category_name, payload FROM kirana_oltp.ml_recommendations")
        params: dict[str, Any] = {}
        if store_id is not None:
            sql += " WHERE store_id = :sid"
            params["sid"] = int(store_id)
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text(sql), params).mappings().all()
        except Exception as exc:  # table missing / DB hiccup → degrade to empty
            logger.warning("ml_recommendations read failed (%s) — empty", exc)
            return pd.DataFrame()
        if not rows:
            return pd.DataFrame()
        records = []
        for r in rows:
            rec = {
                "store_id": r["store_id"], "sku_id": r["sku_id"],
                "recommendation_type": r["recommendation_type"],
                "product_name": r["product_name"], "category_name": r["category_name"],
            }
            rec.update(r["payload"] or {})
            records.append(rec)
        return pd.DataFrame(records)

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

    def signals_freshness(self) -> dict[str, Any]:
        """DB-backed freshness of the `ml_signals` table — what the forecast and
        the app's ML cards actually read. This is deliberately separate from
        `freshness()`, which only measures the CSV files on disk: a retrain can
        leave the CSVs fresh while `load_to_db()` silently fails, leaving this
        table stale for days with every other status check still green. This is
        the value to alert on."""
        if self._engine is None:
            return {"available": False, "reason": "no_engine"}
        try:
            with self._engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT COUNT(*) AS rows, COUNT(DISTINCT store_id) AS stores, "
                    "MAX(updated_at) AS newest FROM kirana_oltp.ml_signals"
                )).mappings().first()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ml_signals freshness read failed: %s", exc)
            return {"available": False, "reason": str(exc)}

        newest = row["newest"] if row else None
        age_h = None
        if newest is not None:
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            age_h = round((datetime.now(timezone.utc) - newest).total_seconds() / 3600, 1)
        return {
            "available": True,
            "rows": int((row["rows"] if row else 0) or 0),
            "stores": int((row["stores"] if row else 0) or 0),
            "newest": newest.isoformat() if newest else None,
            "age_hours": age_h,
            "stale": age_h is None or age_h > ML_STALE_AFTER_HOURS,
        }

    def flags_for_store(self, store_id: int) -> dict[int, list[str]]:
        """{product_id: [recommendation_type, ...]} for one store — drives the
        ML flag tags (fast_moving / reorder_now / dead_stock / ...) on items."""
        df = self._read_recos(store_id)
        if df.empty or "sku_id" not in df.columns:
            return {}
        out: dict[int, list[str]] = {}
        for _, r in df.iterrows():
            out.setdefault(int(r["sku_id"]), []).append(str(r["recommendation_type"]))
        return out

    def refresh(self) -> None:
        """API-side refresh = drop the cached frame so the next read re-queries
        Postgres. It does NOT recompute from the CSVs (that's load_to_db(), run
        offline in the training pipeline) — the whole point of the DB backing
        is that the API never loads the large CSVs into memory."""
        self._frame = None

    def get_frame(self) -> pd.DataFrame:
        """All recommendation rows (the small, already-filtered set) from
        Postgres, cached for the request lifetime."""
        if self._frame is None:
            self._frame = self._read_recos()
        return self._frame

    def _read_signals(self, store_id: int) -> pd.DataFrame:
        """One store's ml_state signal rows from Postgres (never all stores —
        the full signals set is ~170k rows and would reintroduce the OOM)."""
        if self._engine is None:
            return pd.DataFrame()
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT store_id, product_id, payload FROM kirana_oltp.ml_signals "
                    "WHERE store_id = :sid"), {"sid": int(store_id)}).mappings().all()
        except Exception as exc:
            logger.warning("ml_signals read failed (%s) — empty", exc)
            return pd.DataFrame()
        if not rows:
            return pd.DataFrame()
        recs = []
        for r in rows:
            rec = {"store_id": r["store_id"], "product_id": r["product_id"]}
            rec.update(r["payload"] or {})
            recs.append(rec)
        return pd.DataFrame(recs)

    def get_ml_state(self, store_id: int | None = None) -> pd.DataFrame | None:
        """Signal frame for ONE store (pass store_id). Returns empty when no
        store is given — callers must scope to a store so the full 170k-row
        signals set never loads at once."""
        if store_id is None:
            return pd.DataFrame()
        return self._read_signals(int(store_id))

    # ── Quick summary for a single store ──────────────────────────────────────

    def store_summary(self, store_id: int) -> dict:
        df = self._read_recos(store_id)
        if df.empty:
            return {"store_id": store_id, "stockout_risk": 0, "reorder": 0,
                    "fast_moving": 0, "profit": 0, "dead_stock": 0}
        rt = df["recommendation_type"]
        return {
            "store_id":      store_id,
            "stockout_risk": int((rt == "stockout_risk").sum()),
            "reorder":       int((rt == "reorder_now").sum()),
            "fast_moving":   int((rt == "fast_moving").sum()),
            "profit":        int((rt == "profit_opportunity").sum()),
            "dead_stock":    int((rt == "dead_stock").sum()),
        }

    # ── Helper queries used by other modules ──────────────────────────────────

    def _top(self, rtype: str, store_id: int | None, top_n: int,
             sort_col: str, cols: list[str]) -> list[dict]:
        df = self._read_recos(store_id)
        if df.empty or "recommendation_type" not in df.columns:
            return []
        sub = df[df["recommendation_type"] == rtype]
        if sub.empty:
            return []
        if sort_col in sub.columns:
            sub = sub.nlargest(top_n, sort_col)
        else:
            sub = sub.head(top_n)
        keep = [c for c in cols if c in sub.columns]
        return sub[keep].to_dict("records")

    def get_stockout_products(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        return self._top("stockout_risk", store_id, top_n, "prob_stockout_7d",
                         ["sku_id", "product_name", "category_name",
                          "prob_stockout_7d", "days_to_stockout"])

    def get_fast_moving(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        return self._top("fast_moving", store_id, top_n, "forecast_demand",
                         ["sku_id", "product_name", "category_name", "forecast_demand"])

    def get_high_margin(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        return self._top("profit_opportunity", store_id, top_n, "effective_margin",
                         ["sku_id", "product_name", "category_name", "effective_margin"])

    def get_dead_stock(self, store_id: int | None = None, top_n: int = 10) -> list[dict]:
        return self._top("dead_stock", store_id, top_n, "current_stock",
                         ["sku_id", "product_name", "category_name", "current_stock"])
