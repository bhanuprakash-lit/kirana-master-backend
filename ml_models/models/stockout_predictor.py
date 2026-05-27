"""
Stockout Predictor — Statistical / Poisson model
=================================================
Uses per-product historical demand statistics + current stock to compute the
probability that a product stocks out within each horizon.

WHY NOT XGBoost classifier here?
  With real sparse sales data (avg ~14 selling days out of 181, median stock 104
  units), the binary label  at_risk_Nd = (days_of_supply < N)  based on a rolling
  window collapses to 0 on almost every row (rolling avg is 0 on non-selling days,
  driving days_of_supply → ∞).  Training a classifier on all-zero labels produces
  the exactly the constant output the user reported.

  The correct model for "probability stock runs out in N days" when demand is sparse
  and known from history is:

      P(stockout in N days) = P(Demand_N > stock_on_hand)
                            = 1 − CDF_Poisson(floor(stock), λ = avg_demand * N)

  This gives well-calibrated, range-spanning probabilities from real stock/demand
  data.  Saved artifact stores per-product demand stats so inference just needs
  current stock.

Output columns (same interface as before):
  prob_stockout_3d / 7d / 21d / 30d   (float 0-1)
  risk_3d / 7d / 21d / 30d            (int 0/1, threshold 0.5)
  days_of_supply                       (float)
  avg_daily_demand                     (float — informational)
"""
from __future__ import annotations

import os
import joblib
import numpy as np
import pandas as pd
from scipy.stats import poisson

from config import STOCKOUT_HORIZONS, MODELS_DIR


class StockoutPredictor:
    """
    Statistical stockout-probability estimator.

    Demand model: Poisson with rate = historical avg_daily_units (including
    zero-sales days) per (store, product).

    P(stockout in N days) = 1 − Poisson.CDF(floor(stock), rate * N)
    """

    def __init__(self):
        self.product_stats: pd.DataFrame | None = None
        self.horizons = STOCKOUT_HORIZONS
        self.metrics: dict = {}

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, df: pd.DataFrame, verbose: bool = True) -> None:
        print(f"\n[StockoutPredictor] Computing demand stats on {len(df):,} rows "
              f"({df['product_id'].nunique()} products, {df['store_id'].nunique()} stores)")

        grp = ["store_id", "product_id"]

        stats = df.groupby(grp)["units_sold"].agg(
            avg_daily="mean",
            std_daily="std",
            total_units="sum",
            days_history="count",
            selling_days=lambda x: (x > 0).sum(),
        ).reset_index()
        stats["std_daily"] = stats["std_daily"].fillna(0.0)
        stats["fill_rate"] = stats["selling_days"] / stats["days_history"].clip(lower=1)

        latest_stock = (
            df.sort_values("date")
              .groupby(grp)["stock_on_hand"]
              .last()
              .reset_index()
              .rename(columns={"stock_on_hand": "latest_stock"})
        )
        stats = stats.merge(latest_stock, on=grp, how="left")
        stats["days_of_supply"] = stats["latest_stock"] / (stats["avg_daily"] + 1e-9)

        self.product_stats = stats

        if verbose:
            for n in self.horizons:
                at_risk = (stats["days_of_supply"] < n).sum()
                pct     = at_risk / len(stats) * 100
                print(f"  horizon {n:2d}d → {at_risk:3d} / {len(stats)} products "
                      f"at risk ({pct:.1f}%)  [latest snapshot stock]")

            print(f"\n  Demand stats summary:")
            print(f"  Avg daily demand (all products): {stats['avg_daily'].mean():.4f} units/day")
            print(f"  Products with >0 avg demand    : {(stats['avg_daily'] > 0).sum()}")
            print(f"  Median days of supply          : {stats['days_of_supply'].median():.0f} days")

        self.metrics = {
            "n_products":     int(len(stats)),
            "mean_avg_daily": round(float(stats["avg_daily"].mean()), 4),
            "at_risk_7d":     int((stats["days_of_supply"] < 7).sum()),
            "at_risk_30d":    int((stats["days_of_supply"] < 30).sum()),
        }

        self.save()

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        grp = ["store_id", "product_id"]

        if self.product_stats is None:
            raise RuntimeError("Model not trained. Call train() or load() first.")

        merged = df[grp + ["stock_on_hand"] + (["date"] if "date" in df.columns else [])].copy()
        merged = merged.merge(
            self.product_stats[grp + ["avg_daily", "std_daily", "days_of_supply"]],
            on=grp, how="left",
        )
        merged["avg_daily"]      = merged["avg_daily"].fillna(0.0)
        merged["std_daily"]      = merged["std_daily"].fillna(0.0)
        merged["days_of_supply"] = merged["days_of_supply"].fillna(9999.0)

        stock  = merged["stock_on_hand"].astype(float).values
        rate   = merged["avg_daily"].astype(float).values
        k_arr  = np.floor(stock).astype(int).clip(min=0)

        out = merged[grp + (["date"] if "date" in merged.columns else [])].copy()
        out["days_of_supply"]   = np.round(stock / (rate + 1e-9), 1)
        out["avg_daily_demand"] = np.round(rate, 4)

        for n in self.horizons:
            lam = rate * n
            probs = np.where(
                rate <= 0,
                0.0,
                1.0 - poisson.cdf(k_arr, lam)
            ).clip(0, 1)
            out[f"prob_stockout_{n}d"] = np.round(probs, 4)
            out[f"risk_{n}d"]          = (probs >= 0.5).astype(int)

        return out

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        path = os.path.join(MODELS_DIR, "stockout_predictor.pkl")
        joblib.dump({"product_stats": self.product_stats, "horizons": self.horizons}, path)
        print(f"  Saved → {path}")

    @classmethod
    def load(cls) -> "StockoutPredictor":
        path = os.path.join(MODELS_DIR, "stockout_predictor.pkl")
        obj  = cls.__new__(cls)
        saved = joblib.load(path)
        obj.product_stats = saved["product_stats"]
        obj.horizons      = saved.get("horizons", STOCKOUT_HORIZONS)
        obj.metrics       = {}
        return obj
