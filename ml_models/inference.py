"""
Inference script — loads saved models and produces fresh predictions from DB.
Usage:  python inference.py

Automatically discovers all active stores from inventory_snapshots.
Outputs written to ml_models/results/ (relative to this file).
"""
import os, sys
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import (
    load_daily_metrics, load_current_inventory,
    load_products, load_order_items,
    get_active_stores,
)
from feature_engineering import (
    build_margin_features,
    build_velocity_features,
    build_reorder_features,
)
from models.stockout_predictor import StockoutPredictor
from models.margin_classifier  import MarginClassifier
from models.sku_velocity        import SKUVelocityClassifier
from models.deadstock_detector  import DeadStockDetector
from models.reorder_optimizer   import ReorderOptimizer
from config import DEAD_STOCK_DAYS, RESULTS_DIR

os.makedirs(RESULTS_DIR, exist_ok=True)


def run_inference():
    active_stores = get_active_stores()
    print(f"Running inference for stores: {active_stores}")

    print("Loading models...")
    sp = StockoutPredictor.load()
    mc = MarginClassifier.load()
    vc = SKUVelocityClassifier.load()
    dd = DeadStockDetector.load()
    ro = ReorderOptimizer.load()

    print("Loading data...")
    daily    = load_daily_metrics(active_stores)
    curr_inv = load_current_inventory(active_stores)
    products = load_products(active_stores)
    orders   = load_order_items(active_stores)

    # ── Stockout predictions using current inventory stock ────────────────────
    latest_snap = (
        daily.sort_values("date")
             .groupby(["store_id", "product_id"])
             .last()
             .reset_index()[["store_id", "product_id", "date", "stock_on_hand"]]
    )
    # Override with live inventory quantity where available
    latest_snap = latest_snap.merge(
        curr_inv.rename(columns={"current_stock": "live_stock"}),
        on=["store_id", "product_id"], how="left"
    )
    latest_snap["stock_on_hand"] = latest_snap["live_stock"].fillna(latest_snap["stock_on_hand"])
    latest_snap = latest_snap.drop(columns=["live_stock"])

    stockout_preds = sp.predict(latest_snap)

    # ── Other feature matrices ────────────────────────────────────────────────
    margin_feats   = build_margin_features(daily, orders)
    velocity_feats = build_velocity_features(daily)
    reorder_feats  = build_reorder_features(daily, stockout_preds, curr_inv)

    # ── Dead stock needs recent window features ───────────────────────────────
    cutoff = daily["date"].max() - pd.Timedelta(days=DEAD_STOCK_DAYS)
    recent = daily[daily["date"] >= cutoff].groupby(["store_id", "product_id"]).agg(
        recent_avg_sales  = ("units_sold", "mean"),
        recent_days_sold  = ("units_sold", lambda x: (x > 0).sum()),
        recent_total_sold = ("units_sold", "sum"),
        recent_avg_stock  = ("stock_on_hand", "mean"),
    ).reset_index()
    dead_input = velocity_feats.merge(recent, on=["store_id", "product_id"], how="left")
    for col in ["recent_avg_sales", "recent_days_sold", "recent_total_sold"]:
        dead_input[col] = dead_input[col].fillna(0)
    dead_input["recent_avg_stock"] = dead_input["recent_avg_stock"].fillna(dead_input["avg_stock"])

    # ── Generate all predictions ──────────────────────────────────────────────
    results = {
        "stockout":  stockout_preds,
        "margin":    mc.predict(margin_feats),
        "velocity":  vc.predict(velocity_feats),
        "deadstock": dd.predict(dead_input),
        "reorder":   ro.predict(reorder_feats),
    }

    # ── Add urgency tier to reorder ───────────────────────────────────────────
    results["reorder"]["urgency"] = pd.cut(
        results["reorder"]["days_until_stockout"],
        bins=[0, 3, 7, 21, float("inf")],
        labels=["CRITICAL", "HIGH", "MEDIUM", "OK"]
    )

    # ── Save CSVs with product names ──────────────────────────────────────────
    for name, df in results.items():
        df = df.merge(
            products[["product_id", "name", "sku", "category_name"]],
            on="product_id", how="left"
        )
        path = os.path.join(RESULTS_DIR, f"{name}_predictions.csv")
        df.to_csv(path, index=False)
        print(f"  {path}: {len(df):,} rows")

    return results


if __name__ == "__main__":
    run_inference()
