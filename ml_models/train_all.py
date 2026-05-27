"""
Master training script — runs all 5 ML pipelines sequentially.
Usage: python train_all.py

Automatically discovers all active stores from inventory_snapshots.
Writes artifacts to ml_models/artifacts/ and CSVs to ml_models/results/
(paths relative to this file — resolves to kirana-master-backend/ml_models/).
"""
import os, sys, time
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import (
    load_daily_metrics, load_inventory_snapshots,
    load_products, load_product_supplier,
    load_current_inventory, load_order_items,
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


def banner(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def main():
    t0 = time.time()
    banner("Loading data from PostgreSQL")

    active_stores = get_active_stores()
    print(f"  Active stores: {active_stores}")

    daily    = load_daily_metrics(active_stores)
    snaps    = load_inventory_snapshots(active_stores)
    products = load_products(active_stores)
    curr_inv = load_current_inventory(active_stores)
    orders   = load_order_items(active_stores)

    print(f"  daily_metrics:      {len(daily):,} rows  ({daily['store_id'].nunique()} stores)")
    print(f"  inventory_snaps:    {len(snaps):,} rows")
    print(f"  products:           {len(products):,}")
    print(f"  current_inventory:  {len(curr_inv):,}")
    print(f"  order_items:        {len(orders):,}")

    # ── 1. Stockout Prediction ────────────────────────────────────────────────
    # Uses Poisson-based statistical model (not XGBoost) because real sales data
    # is sparse — rolling-window binary labels collapse to 0% positive rate.
    banner("1/5  Stockout Prediction (Statistical / Poisson)")

    sp = StockoutPredictor()
    sp.train(daily)

    # Predict using CURRENT inventory stock for most accurate signal
    latest_snap = (
        daily.sort_values("date")
             .groupby(["store_id", "product_id"])
             .last()
             .reset_index()[["store_id", "product_id", "date", "stock_on_hand"]]
    )
    # Override stock_on_hand with the live inventory quantity where available
    latest_snap = latest_snap.merge(
        curr_inv.rename(columns={"current_stock": "live_stock"}),
        on=["store_id", "product_id"], how="left"
    )
    latest_snap["stock_on_hand"] = latest_snap["live_stock"].fillna(latest_snap["stock_on_hand"])
    latest_snap = latest_snap.drop(columns=["live_stock"])

    stockout_inf = sp.predict(latest_snap)

    # ── 2. High Profit Margin SKUs ────────────────────────────────────────────
    banner("2/5  High Profit Margin SKU Classifier")
    margin_feats = build_margin_features(daily, orders)
    print(f"  Feature matrix: {margin_feats.shape}")
    mc = MarginClassifier()
    mc.train(margin_feats)

    # ── 3. Fast / Slow Moving SKUs ────────────────────────────────────────────
    banner("3/5  SKU Velocity (Fast / Slow Moving)")
    velocity_feats = build_velocity_features(daily)
    print(f"  Feature matrix: {velocity_feats.shape}")
    vc = SKUVelocityClassifier()
    vc.train(velocity_feats)

    # ── 4. Dead Stock Detection ───────────────────────────────────────────────
    banner("4/5  Dead Stock Detector")
    dd = DeadStockDetector()
    dd.train(velocity_feats, daily)

    # ── 5. AI Reorder Optimizer ───────────────────────────────────────────────
    banner("5/5  AI Reorder Optimizer")
    reorder_feats = build_reorder_features(daily, stockout_inf, curr_inv)
    print(f"  Feature matrix: {reorder_feats.shape}")
    ro = ReorderOptimizer()
    ro.train(reorder_feats)

    # ── 6. Generate & Save Full Inference Reports ─────────────────────────────
    banner("6  Generating Inference Reports")

    # Stockout report
    stockout_report = stockout_inf.merge(
        products[["product_id", "name", "sku", "category_name"]], on="product_id", how="left"
    )
    stockout_report.to_csv(os.path.join(RESULTS_DIR, "stockout_predictions.csv"), index=False)
    print(f"  stockout_predictions.csv  ({len(stockout_report):,} rows)")

    # Margin report
    margin_inf = mc.predict(margin_feats)
    margin_inf = margin_inf.merge(
        products[["product_id", "name", "sku", "category_name"]], on="product_id", how="left"
    )
    margin_inf.to_csv(os.path.join(RESULTS_DIR, "margin_predictions.csv"), index=False)
    print(f"  margin_predictions.csv  ({len(margin_inf):,} rows)")

    # Velocity report
    vel_inf = vc.predict(velocity_feats)
    vel_inf = vel_inf.merge(
        products[["product_id", "name", "sku", "category_name"]], on="product_id", how="left"
    )
    vel_inf.to_csv(os.path.join(RESULTS_DIR, "velocity_predictions.csv"), index=False)
    print(f"  velocity_predictions.csv  ({len(vel_inf):,} rows)")

    # Dead stock report
    max_date = daily["date"].max()
    cutoff   = max_date - pd.Timedelta(days=DEAD_STOCK_DAYS)
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

    dead_inf = dd.predict(dead_input)
    dead_inf = dead_inf.merge(
        products[["product_id", "name", "sku", "category_name"]], on="product_id", how="left"
    )
    dead_inf.to_csv(os.path.join(RESULTS_DIR, "deadstock_predictions.csv"), index=False)
    print(f"  deadstock_predictions.csv  ({len(dead_inf):,} rows)")

    # Reorder report
    reorder_inf = ro.predict(reorder_feats)
    reorder_inf = reorder_inf.merge(
        products[["product_id", "name", "sku", "category_name"]], on="product_id", how="left"
    )
    reorder_inf["urgency"] = pd.cut(
        reorder_inf["days_until_stockout"],
        bins=[0, 3, 7, 21, float("inf")],
        labels=["CRITICAL", "HIGH", "MEDIUM", "OK"]
    )
    reorder_inf.to_csv(os.path.join(RESULTS_DIR, "reorder_recommendations.csv"), index=False)
    print(f"  reorder_recommendations.csv  ({len(reorder_inf):,} rows)")

    # ── Summary ───────────────────────────────────────────────────────────────
    banner("Training Complete")
    print(f"  Total time: {time.time()-t0:.1f}s")
    print(f"  Stores trained: {active_stores}")
    print("\n  Model Metrics:")
    print(f"  Stockout  : {sp.metrics}")
    print(f"  Margin    : {mc.metrics}")
    print(f"  Velocity  : {vc.metrics}")
    print(f"  Dead Stock: {dd.metrics}")
    print(f"  Reorder   : {ro.metrics}")

    print("\n  === Business Summary ===")
    at_risk_3d  = (stockout_report["risk_3d"] == 1).sum()
    at_risk_7d  = (stockout_report["risk_7d"] == 1).sum()
    at_risk_30d = (stockout_report["risk_30d"] == 1).sum()
    high_m      = (margin_inf["is_high_margin"] == 1).sum()
    fast_s      = (vel_inf["is_fast_moving"] == 1).sum()
    slow_s      = (vel_inf["is_slow_moving"] == 1).sum()
    dead_s      = (dead_inf["is_dead_stock"] == 1).sum()
    urgent_r    = (reorder_inf["needs_reorder"] == 1).sum()
    print(f"  Products at stockout risk (3d) : {at_risk_3d}")
    print(f"  Products at stockout risk (7d) : {at_risk_7d}")
    print(f"  Products at stockout risk (30d): {at_risk_30d}")
    print(f"  High margin products           : {high_m}")
    print(f"  Fast moving products           : {fast_s}")
    print(f"  Slow moving products           : {slow_s}")
    print(f"  Dead stock products            : {dead_s}")
    print(f"  Reorder actions needed         : {urgent_r}")

    # Duplicate sanity checks
    print("\n  === Duplicate Check ===")
    checks = [
        ("stockout",  stockout_report, ["store_id", "product_id"]),
        ("margin",    margin_inf,      ["store_id", "product_id"]),
        ("velocity",  vel_inf,         ["store_id", "product_id"]),
        ("deadstock", dead_inf,        ["store_id", "product_id"]),
        ("reorder",   reorder_inf,     ["store_id", "product_id"]),
    ]
    for name, frame, keys in checks:
        dupes  = frame.duplicated(subset=keys).sum()
        unique = len(frame.drop_duplicates(subset=keys))
        status = "✓" if dupes == 0 else "✗ DUPES"
        print(f"  {name:10s}: {len(frame):4d} rows  {unique:4d} unique  {status}")
        if dupes > 0:
            raise AssertionError(f"{name} has {dupes} duplicates by {keys}")

    # Top 5 most urgent reorder
    needs_reorder = reorder_inf[reorder_inf["needs_reorder"] == 1]
    print("\n  === Top 5 Most Urgent Reorder ===")
    if len(needs_reorder) > 0:
        top5 = needs_reorder.nsmallest(5, "days_until_stockout")[
            ["name", "current_stock", "predicted_reorder_qty", "days_until_stockout", "urgency"]
        ]
        print(top5.to_string(index=False))
    else:
        print("  No products currently need reordering.")

    # Top 5 highest stockout risk (7d)
    print("\n  === Top 5 Stockout Risk (7-day horizon) ===")
    top5_so = stockout_report.nlargest(5, "prob_stockout_7d")[
        ["name", "category_name", "prob_stockout_7d", "days_of_supply", "avg_daily_demand"]
    ]
    print(top5_so.to_string(index=False))


if __name__ == "__main__":
    main()
