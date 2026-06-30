"""Build feature matrices for all ML models from raw loaded DataFrames."""
import numpy as np
import pandas as pd
from config import (
    STOCKOUT_HORIZONS, FAST_PERCENTILE, SLOW_PERCENTILE,
    HIGH_MARGIN_PERCENTILE, DEAD_STOCK_DAYS, DEAD_STOCK_UNITS_THRESHOLD,
    SAFETY_STOCK_Z,
)


# ─── Stockout Features ────────────────────────────────────────────────────────

def build_stockout_features(daily: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    """
    Creates one row per (store, product, date) with:
    - Rolling sales features
    - Stock dynamics
    - Binary stockout-risk labels for 3/7/21/30 day horizons
    """
    df = daily.copy().sort_values(["store_id", "product_id", "date"])
    grp = ["store_id", "product_id"]

    # Rolling sales windows
    df["rolling_3d_sales"]  = df.groupby(grp)["units_sold"].transform(lambda x: x.rolling(3,  min_periods=1).mean())
    df["rolling_7d_sales"]  = df.groupby(grp)["units_sold"].transform(lambda x: x.rolling(7,  min_periods=1).mean())
    df["rolling_14d_sales"] = df.groupby(grp)["units_sold"].transform(lambda x: x.rolling(14, min_periods=1).mean())
    df["rolling_30d_sales"] = df.groupby(grp)["units_sold"].transform(lambda x: x.rolling(30, min_periods=1).mean())

    # Sales std (demand variability)
    df["sales_std_7d"]  = df.groupby(grp)["units_sold"].transform(lambda x: x.rolling(7,  min_periods=2).std().fillna(0))
    df["sales_std_30d"] = df.groupby(grp)["units_sold"].transform(lambda x: x.rolling(30, min_periods=2).std().fillna(0))

    # Days of supply at current avg consumption rate
    eps = 1e-6
    df["days_of_supply"]    = df["stock_on_hand"] / (df["rolling_7d_sales"] + eps)
    df["days_of_supply_3d"] = df["stock_on_hand"] / (df["rolling_3d_sales"] + eps)

    # Lag features
    df["units_lag1"] = df.groupby(grp)["units_sold"].shift(1).fillna(0)
    df["units_lag3"] = df.groupby(grp)["units_sold"].shift(3).fillna(0)
    df["units_lag7"] = df.groupby(grp)["units_sold"].shift(7).fillna(0)

    # Stock trend
    df["stock_lag1"]  = df.groupby(grp)["stock_on_hand"].shift(1).fillna(df["stock_on_hand"])
    df["stock_delta"] = df["stock_on_hand"] - df["stock_lag1"]

    # Safety stock reorder point
    df["safety_stock"]   = SAFETY_STOCK_Z * df["sales_std_7d"] * np.sqrt(df["lead_time_days"].fillna(3))
    df["reorder_point"]  = df["rolling_7d_sales"] * df["lead_time_days"].fillna(3) + df["safety_stock"]
    df["below_reorder"]  = (df["stock_on_hand"] < df["reorder_point"]).astype(int)

    # Temporal features
    df["day_of_week"] = df["date"].dt.dayofweek
    df["month"]       = df["date"].dt.month
    df["week"]        = df["date"].dt.isocalendar().week.astype(int)

    # Fill missing
    df["weather_temp"] = df["weather_temp"].fillna(df["weather_temp"].median())
    df["rain_flag"]    = df["rain_flag"].fillna(0)
    df["discount"]     = df["discount"].fillna(0)
    df["margin"]       = df["margin"].fillna(df["margin"].median())

    # ── Labels: at_risk_Nd = 1 if days_of_supply < N ─────────────────────────
    for n in STOCKOUT_HORIZONS:
        df[f"at_risk_{n}d"] = (df["days_of_supply"] < n).astype(int)

    return df.dropna(subset=["stock_on_hand", "units_sold", "rolling_7d_sales"])


STOCKOUT_FEATURE_COLS = [
    "stock_on_hand", "rolling_3d_sales", "rolling_7d_sales",
    "rolling_14d_sales", "rolling_30d_sales", "sales_std_7d", "sales_std_30d",
    "units_lag1", "units_lag3", "units_lag7",
    "stock_delta", "days_of_supply_3d", "below_reorder",
    "lead_time_days", "safety_stock", "reorder_point",
    "price", "discount", "margin", "promo_flag", "rain_flag", "weather_temp",
    "is_perishable", "is_loose", "category_id",
    "day_of_week", "month", "week",
]


# ─── Margin Features ──────────────────────────────────────────────────────────

def build_margin_features(daily: pd.DataFrame, order_items: pd.DataFrame) -> pd.DataFrame:
    """Aggregate margin metrics per (store, product) for high-margin classification.

    Margin is store-dependent because pricing varies by store; aggregating
    globally hid local high-margin opportunities and made the adapter assign
    every product to a single default store.
    """
    grp = ["store_id", "product_id"]

    olap = daily.groupby(grp).agg(
        avg_margin        = ("margin", "mean"),
        max_margin        = ("margin", "max"),
        min_margin        = ("margin", "min"),
        margin_std        = ("margin", "std"),
        avg_price         = ("price", "mean"),
        avg_units_sold    = ("units_sold", "mean"),
        total_revenue     = ("revenue", "sum"),
        total_profit      = ("profit", "sum"),
        promo_pct         = ("promo_flag", "mean"),
        avg_stock         = ("stock_on_hand", "mean"),
        category_id       = ("category_id", "first"),
        is_perishable     = ("is_perishable", "first"),
    ).reset_index()

    # From real order transactions — same (store, product) grain.
    txn = order_items.groupby(grp).agg(
        txn_avg_margin    = ("margin_pct", "mean"),
        txn_avg_unit_price= ("unit_price", "mean"),
        txn_avg_cost      = ("cost_price", "mean"),
        txn_total_qty     = ("quantity", "sum"),
        txn_total_profit  = ("gross_profit", "sum"),
    ).reset_index()

    df = olap.merge(txn, on=grp, how="left")
    df["margin_std"]        = df["margin_std"].fillna(0)
    df["profit_per_unit"]   = df["total_profit"] / (df["avg_units_sold"] + 1e-6)
    df["revenue_per_stock"] = df["total_revenue"] / (df["avg_stock"] + 1e-6)

    # Use transaction margin when available, else OLAP margin
    df["effective_margin"] = df["txn_avg_margin"].fillna(df["avg_margin"])

    # Label: per-store top HIGH_MARGIN_PERCENTILE% by effective margin.
    # Store-relative threshold avoids one store dominating the global label.
    df["is_high_margin"] = (
        df.groupby("store_id")["effective_margin"]
          .transform(lambda s: (s >= s.quantile(HIGH_MARGIN_PERCENTILE / 100)).astype(int))
    )

    return df


MARGIN_FEATURE_COLS = [
    "avg_margin", "max_margin", "min_margin", "margin_std",
    "avg_price", "avg_units_sold", "total_revenue", "total_profit",
    "promo_pct", "avg_stock", "category_id", "is_perishable",
    "txn_avg_margin", "txn_avg_unit_price", "txn_avg_cost",
    "txn_total_qty", "txn_total_profit",
    "profit_per_unit", "revenue_per_stock", "effective_margin",
]


# ─── SKU Velocity Features ────────────────────────────────────────────────────

def build_velocity_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate velocity metrics per (store, product) for fast/slow classification.

    Velocity is store-relative — what is fast in a small kirana may be slow
    in a high-footfall one. Per-(store,product) grain also lets the adapter
    emit accurate per-store recommendations without a hardcoded default.
    """
    grp = ["store_id", "product_id"]

    df = daily.groupby(grp).agg(
        avg_units_sold    = ("units_sold", "mean"),
        total_units       = ("units_sold", "sum"),
        max_units_day     = ("units_sold", "max"),
        sales_std         = ("units_sold", "std"),
        avg_stock         = ("stock_on_hand", "mean"),
        avg_revenue       = ("revenue", "mean"),
        avg_margin        = ("margin", "mean"),
        promo_pct         = ("promo_flag", "mean"),
        days_with_sales   = ("units_sold", lambda x: (x > 0).sum()),
        total_days        = ("units_sold", "count"),
        category_id       = ("category_id", "first"),
        is_perishable     = ("is_perishable", "first"),
    ).reset_index()

    df["sales_std"]   = df["sales_std"].fillna(0)
    df["fill_rate"]   = df["days_with_sales"] / (df["total_days"] + 1e-6)
    df["turnover"]    = df["total_units"] / (df["avg_stock"] + 1e-6)
    df["velocity_cv"] = df["sales_std"] / (df["avg_units_sold"] + 1e-6)

    # Per-store normalised composite velocity score
    store_max_units = df.groupby("store_id")["avg_units_sold"].transform("max") + 1e-6
    store_max_turn  = df.groupby("store_id")["turnover"].transform("max")       + 1e-6
    df["velocity_score"] = (
        0.4 * df["avg_units_sold"] / store_max_units +
        0.3 * df["turnover"]       / store_max_turn  +
        0.3 * df["fill_rate"]
    )

    # Per-store fast/slow labels
    df["is_fast_moving"] = (
        df.groupby("store_id")["velocity_score"]
          .transform(lambda s: (s >= s.quantile(FAST_PERCENTILE / 100)).astype(int))
    )
    df["is_slow_moving"] = (
        df.groupby("store_id")["velocity_score"]
          .transform(lambda s: (s <= s.quantile(SLOW_PERCENTILE / 100)).astype(int))
    )

    # Dead stock: low sales over the window AND material stock on hand
    df["is_dead_stock"] = (
        (df["avg_units_sold"] <= DEAD_STOCK_UNITS_THRESHOLD) &
        (df["avg_stock"] > 5)
    ).astype(int)

    return df


VELOCITY_FEATURE_COLS = [
    "avg_units_sold", "total_units", "max_units_day", "sales_std",
    "avg_stock", "avg_revenue", "avg_margin", "promo_pct",
    "days_with_sales", "total_days", "fill_rate", "turnover", "velocity_cv",
    "category_id", "is_perishable", "velocity_score",
]


# ─── Reorder Features ─────────────────────────────────────────────────────────

def build_reorder_features(
    daily: pd.DataFrame,
    stockout_preds: pd.DataFrame,   # output of stockout model inference
    current_inv: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build features for reorder quantity prediction.
    Target: economically optimal reorder quantity.
    """
    # Latest metrics per (store, product)
    latest = daily.sort_values("date").groupby(["store_id", "product_id"]).last().reset_index()

    agg = daily.groupby(["store_id", "product_id"]).agg(
        avg_daily_sales   = ("units_sold", "mean"),
        sales_std         = ("units_sold", "std"),
        avg_stock         = ("stock_on_hand", "mean"),
        avg_margin        = ("margin", "mean"),
        avg_price         = ("price", "mean"),
    ).reset_index()
    agg["sales_std"] = agg["sales_std"].fillna(0)

    df = agg.merge(current_inv, on=["store_id", "product_id"], how="left")
    df["current_stock"] = df["current_stock"].fillna(df["avg_stock"])

    # Merge lead time. After data_loader dedup the supplier join is one row per
    # product, so taking the first observed lead_time_days per product is deterministic.
    lead = (
        daily[["product_id", "lead_time_days"]]
        .dropna()
        .groupby("product_id", as_index=False)
        .agg(lead_time_days=("lead_time_days", "min"))
    )
    df = df.merge(lead, on="product_id", how="left")
    df["lead_time_days"] = df["lead_time_days"].fillna(3)

    # Safety stock (sigma * z * sqrt(L))
    df["safety_stock"] = SAFETY_STOCK_Z * df["sales_std"] * np.sqrt(df["lead_time_days"])
    df["reorder_point"] = df["avg_daily_sales"] * df["lead_time_days"] + df["safety_stock"]

    # Economic Order Quantity (Wilson EOQ): sqrt(2DS/H)
    # D = annual demand, S = ordering cost (proxy: price * 0.05), H = holding cost (proxy: price * 0.2)
    annual_demand = df["avg_daily_sales"] * 365
    order_cost    = df["avg_price"] * 0.05
    holding_cost  = df["avg_price"] * 0.20
    df["eoq"] = np.sqrt((2 * annual_demand * order_cost) / (holding_cost + 1e-6)).round(0)

    # Target: suggested reorder quantity
    # = max(EOQ, cover lead_time + safety_stock) when stock < reorder_point
    df["target_reorder_qty"] = np.where(
        df["current_stock"] < df["reorder_point"],
        np.maximum(df["eoq"], df["reorder_point"] - df["current_stock"] + df["safety_stock"]),
        0
    ).round(0)

    # ML training target: EOQ for every product (non-zero regardless of stock level).
    # ReorderOptimizer trains on this so it always has samples — XGBoost learns
    # the non-linear interactions (demand variance, lead time, perishability) that
    # cause the optimal order quantity to deviate from the simple EOQ formula.
    df["target_eoq"] = df["eoq"].clip(lower=1)

    # Merge stockout probabilities if available
    if stockout_preds is not None and len(stockout_preds) > 0:
        df = df.merge(
            stockout_preds[["store_id", "product_id", "prob_stockout_7d", "prob_stockout_30d"]],
            on=["store_id", "product_id"], how="left"
        )
        df["prob_stockout_7d"]  = df.get("prob_stockout_7d",  pd.Series(0.5, index=df.index)).fillna(0.5)
        df["prob_stockout_30d"] = df.get("prob_stockout_30d", pd.Series(0.5, index=df.index)).fillna(0.5)
    else:
        df["prob_stockout_7d"]  = 0.5
        df["prob_stockout_30d"] = 0.5

    return df


REORDER_FEATURE_COLS = [
    "avg_daily_sales", "sales_std", "avg_stock", "current_stock",
    "lead_time_days", "safety_stock", "reorder_point", "eoq",
    "avg_margin", "avg_price",
    "prob_stockout_7d", "prob_stockout_30d",
]
