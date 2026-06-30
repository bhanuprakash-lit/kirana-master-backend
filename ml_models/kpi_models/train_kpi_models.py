"""
KPI ML Models Training Script
Trains 5 models and saves artifacts to ml_models/kpi_models/artifacts/

Models:
  1. customer_churn.pkl       — XGBoost: which customers will churn (>30d no visit)
  2. category_bcg.pkl         — KMeans(4): BCG quadrant cluster IDs per category
  3. new_product_trial.pkl    — XGBoost: will new product succeed in 30-day trial
  4. shrinkage_anomaly.pkl    — IsolationForest: flag abnormal shrinkage per SKU
  5. supplier_reliability.pkl — XGBoost regression: predict on-time delivery score

Usage:
  conda activate kirana-ml
  cd kirana-master-backend
  python ml_models/kpi_models/train_kpi_models.py
"""
from __future__ import annotations

import os
import sys
import logging
from urllib.parse import urlparse, parse_qs, unquote

import joblib
import numpy as np
import pandas as pd
import psycopg2
from sklearn.ensemble import IsolationForest
from sklearn.cluster import KMeans
from sklearn.model_selection import StratifiedKFold, KFold, cross_val_predict
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, roc_auc_score, mean_absolute_error
import xgboost as xgb

# ── Config ────────────────────────────────────────────────────────────────────
def _db_config_from_url(url: str) -> dict:
    dsn = url
    for prefix in ("postgresql+psycopg2://", "postgres+psycopg2://",
                   "postgresql+asyncpg://", "postgres://"):
        if dsn.startswith(prefix):
            dsn = "postgresql://" + dsn[len(prefix):]
            break
    u = urlparse(dsn)
    q = parse_qs(u.query)
    cfg = {
        "host": u.hostname or "localhost",
        "dbname": (u.path or "").lstrip("/") or "lit_db",
        "user": unquote(u.username) if u.username else "postgres",
        "password": unquote(u.password) if u.password else "",
        "port": u.port or 5432,
    }
    sslmode = (q.get("sslmode") or [None])[0] or os.getenv("PGSSLMODE")
    if not sslmode and "azure" in (u.hostname or "").lower():
        sslmode = "require"
    if sslmode:
        cfg["sslmode"] = sslmode
    return cfg

_db_url = os.getenv("DATABASE_URL")
DB = _db_config_from_url(_db_url) if _db_url else dict(
    host="localhost", dbname="lit_db", user="postgres", password="123456"
)
ARTIFACTS = os.getenv(
    "ML_KPI_ARTIFACTS_DIR",
    os.path.join(os.path.dirname(__file__), "artifacts")
)
os.makedirs(ARTIFACTS, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kpi_ml")

SEED = 42


def _conn():
    return psycopg2.connect(**DB)


def _q(sql: str, **kwargs) -> pd.DataFrame:
    conn = _conn()
    df = pd.read_sql(sql, conn)
    conn.close()
    return df


def _save(obj, name: str):
    path = os.path.join(ARTIFACTS, name)
    joblib.dump(obj, path)
    log.info("  Saved → %s", path)


# ══════════════════════════════════════════════════════════════════════════════
# MODEL 1 — Customer Churn Predictor
# ══════════════════════════════════════════════════════════════════════════════

def train_customer_churn():
    log.info("\n[1/5] Training Customer Churn Predictor")

    df = _q("""
    WITH intervals AS (
        SELECT customer_id, store_id,
               order_date::date AS od,
               LAG(order_date::date) OVER (PARTITION BY customer_id ORDER BY order_date) AS prev_od
        FROM kirana_oltp.orders
        WHERE order_status = 'completed' AND customer_id IS NOT NULL
    ),
    stats AS (
        SELECT customer_id, store_id,
               COUNT(*)                           AS order_count,
               MAX(od)                            AS last_visit,
               MIN(od)                            AS first_visit,
               AVG(od - prev_od)                  AS avg_interval_days,
               STDDEV((od - prev_od)::float) AS interval_std,
               (SELECT AVG(total_amount) FROM kirana_oltp.orders o2
                WHERE o2.customer_id = intervals.customer_id) AS avg_basket
        FROM intervals
        GROUP BY customer_id, store_id
    )
    SELECT s.*,
           (CURRENT_DATE - last_visit)             AS days_since_last,
           (last_visit - first_visit)               AS tenure_days,
           CASE WHEN (CURRENT_DATE - last_visit) > 30 THEN 1 ELSE 0 END AS is_churned
    FROM stats s
    """)

    if df.empty or len(df) < 10:
        log.warning("  Not enough customer data — skipping")
        return

    features = ["order_count", "avg_interval_days", "interval_std",
                "avg_basket", "tenure_days", "days_since_last"]
    df[features] = df[features].fillna(df[features].median())

    X = df[features].astype(np.float32)
    y = df["is_churned"].astype(int)

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    pos_rate = y.mean()
    spw = min((1 - pos_rate) / (pos_rate + 1e-6), 10.0)
    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=spw, eval_metric="auc",
        device="cpu", random_state=SEED, n_jobs=-1,
    )

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    proba = cross_val_predict(model, Xs, y, cv=cv, method="predict_proba")[:, 1]
    auc = roc_auc_score(y, proba)
    model.fit(Xs, y)

    log.info("  Customers=%d  churned_rate=%.1f%%  AUC=%.4f",
             len(df), pos_rate * 100, auc)
    print(classification_report(y, (proba >= 0.5).astype(int), zero_division=0))

    _save({"model": model, "scaler": scaler, "features": features,
           "auc": auc, "pos_rate": float(pos_rate)},
          "customer_churn.pkl")


# ══════════════════════════════════════════════════════════════════════════════
# MODEL 2 — Category BCG Classifier
# ══════════════════════════════════════════════════════════════════════════════

def train_category_bcg():
    log.info("\n[2/5] Training Category BCG Classifier")

    df = _q("""
    WITH sales AS (
        SELECT p.category_id, c.name AS cat_name, o.store_id,
               SUM(oi.quantity * oi.unit_price)               AS revenue,
               SUM(oi.quantity*(oi.unit_price - oi.cost_price)) AS profit,
               SUM(oi.quantity) / 60.0                         AS avg_daily_units
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        JOIN kirana_oltp.product p     ON oi.product_id = p.product_id
        JOIN kirana_oltp.category c    ON p.category_id = c.category_id
        WHERE o.order_status = 'completed'
        GROUP BY p.category_id, c.name, o.store_id
    ),
    totals AS (
        SELECT store_id, SUM(revenue) AS total_rev FROM sales GROUP BY store_id
    )
    SELECT s.category_id, s.cat_name, s.store_id,
           s.revenue / t.total_rev * 100           AS rev_share,
           s.profit  / NULLIF(s.revenue, 0) * 100  AS margin_pct,
           s.avg_daily_units                        AS velocity
    FROM sales s JOIN totals t USING (store_id)
    """)

    features = ["rev_share", "margin_pct", "velocity"]
    df[features] = df[features].fillna(0)

    scaler = StandardScaler()
    Xs = scaler.fit_transform(df[features])

    kmeans = KMeans(n_clusters=4, random_state=SEED, n_init=20)
    df["cluster"] = kmeans.fit_predict(Xs)

    # Label clusters by centroid characteristics
    centroids_orig = scaler.inverse_transform(kmeans.cluster_centers_)
    centroid_df = pd.DataFrame(centroids_orig, columns=features)
    med_rev = centroid_df["rev_share"].median()
    med_mar = centroid_df["margin_pct"].median()
    cluster_labels = {}
    for idx, row in centroid_df.iterrows():
        hs = row["rev_share"] >= med_rev
        hm = row["margin_pct"] >= med_mar
        cluster_labels[idx] = ("star" if hs and hm
                               else "cash_cow" if hs
                               else "question_mark" if hm
                               else "dog")

    log.info("  Categories=%d  Cluster distribution: %s",
             len(df), dict(df["cluster"].value_counts()))
    log.info("  BCG labels: %s", cluster_labels)

    _save({"model": kmeans, "scaler": scaler, "features": features,
           "cluster_labels": cluster_labels},
          "category_bcg.pkl")


# ══════════════════════════════════════════════════════════════════════════════
# MODEL 3 — New Product Trial Success Predictor
# ══════════════════════════════════════════════════════════════════════════════

def train_new_product_trial():
    log.info("\n[3/5] Training New Product Trial Success Predictor")

    df = _q("""
    WITH first_sale AS (
        SELECT oi.product_id, MIN(o.order_date::date) AS first_sale_date
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.order_status = 'completed'
        GROUP BY oi.product_id
    ),
    trial_sales AS (
        SELECT fs.product_id, fs.first_sale_date,
               SUM(oi.quantity) AS units_30d,
               SUM(oi.quantity * oi.unit_price) AS revenue_30d
        FROM first_sale fs
        LEFT JOIN kirana_oltp.order_item oi ON fs.product_id = oi.product_id
        LEFT JOIN kirana_oltp.orders o
                  ON oi.order_id = o.order_id
                 AND o.order_date::date <= fs.first_sale_date + 30
        GROUP BY fs.product_id, fs.first_sale_date
    )
    SELECT ts.product_id, ts.units_30d, ts.revenue_30d,
           p.category_id, p.is_perishable::int AS is_perishable,
           p.is_loose::int AS is_loose,
           pr.price, ps.cost_price,
           (pr.price - ps.cost_price) / NULLIF(pr.price, 0) * 100 AS margin_pct
    FROM trial_sales ts
    JOIN kirana_oltp.product p ON ts.product_id = p.product_id
    LEFT JOIN kirana_oltp.pricing pr ON ts.product_id = pr.product_id
    LEFT JOIN kirana_oltp.product_supplier ps ON ts.product_id = ps.product_id
    """)

    if df.empty or len(df) < 20:
        log.warning("  Not enough data — skipping")
        return

    df = df.fillna(df.median(numeric_only=True))
    p67 = df["units_30d"].quantile(0.67)
    df["success"] = (df["units_30d"] >= p67).astype(int)

    features = ["category_id", "is_perishable", "is_loose",
                "price", "cost_price", "margin_pct"]
    X = df[features].astype(np.float32)
    y = df["success"].values

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="auc", device="cpu",
        random_state=SEED, n_jobs=-1,
    )

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    proba = cross_val_predict(model, Xs, y, cv=cv, method="predict_proba")[:, 1]
    auc = roc_auc_score(y, proba)
    model.fit(Xs, y)

    log.info("  Products=%d  success_rate=%.1f%%  AUC=%.4f",
             len(df), y.mean() * 100, auc)

    _save({"model": model, "scaler": scaler, "features": features, "auc": auc},
          "new_product_trial.pkl")


# ══════════════════════════════════════════════════════════════════════════════
# MODEL 4 — Shrinkage Anomaly Detector
# ══════════════════════════════════════════════════════════════════════════════

def train_shrinkage_anomaly():
    from datetime import date, timedelta
    log.info("\n[4/5] Training Shrinkage Anomaly Detector")
    opening_date = (date.today() - timedelta(days=30)).isoformat()
    closing_date = (date.today() - timedelta(days=1)).isoformat()

    df = _q(f"""
    WITH opening AS (
        SELECT DISTINCT ON (store_id, product_id) store_id, product_id, stock_on_hand
        FROM kirana_oltp.inventory_snapshots
        WHERE snapshot_date >= '{opening_date}'
        ORDER BY store_id, product_id, snapshot_date ASC
    ),
    closing AS (
        SELECT DISTINCT ON (store_id, product_id) store_id, product_id, stock_on_hand
        FROM kirana_oltp.inventory_snapshots
        WHERE snapshot_date <= '{closing_date}'
        ORDER BY store_id, product_id, snapshot_date DESC
    ),
    moves AS (
        SELECT store_id, product_id,
               SUM(CASE WHEN reason='purchase' THEN change_quantity ELSE 0 END) AS purchased,
               SUM(CASE WHEN reason='sale' THEN ABS(change_quantity) ELSE 0 END) AS sold
        FROM kirana_oltp.inventory_movements
        WHERE created_at BETWEEN '{opening_date}' AND '{closing_date}'
        GROUP BY store_id, product_id
    )
    SELECT o.store_id, o.product_id,
           o.stock_on_hand AS opening,
           COALESCE(m.purchased, 0) AS purchased,
           COALESCE(m.sold, 0) AS sold,
           (o.stock_on_hand + COALESCE(m.purchased,0) - COALESCE(m.sold,0)) AS expected,
           cl.stock_on_hand AS actual,
           (o.stock_on_hand + COALESCE(m.purchased,0) - COALESCE(m.sold,0)) - cl.stock_on_hand AS shrinkage,
           COALESCE(m.sold, 1) AS sold_safe
    FROM opening o
    JOIN closing cl USING (store_id, product_id)
    LEFT JOIN moves m USING (store_id, product_id)
    WHERE (o.stock_on_hand + COALESCE(m.purchased,0) - COALESCE(m.sold,0)) - cl.stock_on_hand >= 0
    """)

    if df.empty or len(df) < 20:
        log.warning("  Not enough data — skipping")
        return

    df["shrinkage_rate"]    = df["shrinkage"] / (df["sold_safe"] + 1)
    df["opening_pct"]       = df["shrinkage"] / (df["opening"] + 1)
    df["purchased_ratio"]   = df["purchased"] / (df["sold_safe"] + 1)

    features = ["shrinkage", "shrinkage_rate", "opening_pct", "purchased_ratio",
                "purchased", "sold"]
    df[features] = df[features].fillna(0)
    X = df[features].astype(np.float32)

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    iso = IsolationForest(
        n_estimators=200, contamination=0.12,
        random_state=SEED, n_jobs=-1,
    )
    iso.fit(Xs)

    scores = -iso.score_samples(Xs)
    flagged = (iso.predict(Xs) == -1).sum()
    log.info("  Records=%d  flagged=%d  avg_anomaly_score=%.4f",
             len(df), flagged, scores.mean())

    _save({"model": iso, "scaler": scaler, "features": features},
          "shrinkage_anomaly.pkl")


# ══════════════════════════════════════════════════════════════════════════════
# MODEL 5 — Supplier Reliability Score Predictor
# ══════════════════════════════════════════════════════════════════════════════

def train_supplier_reliability():
    log.info("\n[5/5] Training Supplier Reliability Score Predictor")

    df = _q("""
    SELECT pu.supplier_id,
           pi.cost_price AS actual_cost,
           ps.cost_price AS standard_cost,
           ps.lead_time_days AS expected_lead,
           EXTRACT(EPOCH FROM (pu.arrival_date - pu.order_date))/86400 AS actual_lead,
           (ps.lead_time_days - EXTRACT(EPOCH FROM (pu.arrival_date - pu.order_date))/86400)::float
               / NULLIF(ps.lead_time_days, 0) AS lead_time_accuracy_raw
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id = pi.purchase_id
    JOIN kirana_oltp.product_supplier ps
          ON pi.product_id = ps.product_id AND pu.supplier_id = ps.supplier_id
    WHERE pu.arrival_date IS NOT NULL
    """)

    if df.empty or len(df) < 20:
        log.warning("  Not enough data — skipping")
        return

    df["price_accuracy"] = 1 - (df["actual_cost"] - df["standard_cost"]).abs() / (df["standard_cost"] + 1e-6)
    df["lead_variance"]  = (df["actual_lead"] - df["expected_lead"]).abs()
    df["on_time"]        = (df["actual_lead"] <= df["expected_lead"] + 0.5).astype(int)

    # Target: on_time_score (0-1)
    features = ["actual_cost", "standard_cost", "expected_lead", "lead_variance", "price_accuracy"]
    df[features] = df[features].fillna(df[features].median())
    X = df[features].astype(np.float32)
    y = df["on_time"].astype(float).values

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    model = xgb.XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="reg:squarederror", device="cpu",
        random_state=SEED, n_jobs=-1,
    )
    cv = KFold(n_splits=5, shuffle=True, random_state=SEED)
    preds = cross_val_predict(model, Xs, y, cv=cv)
    mae = mean_absolute_error(y, preds)
    model.fit(Xs, y)

    log.info("  Records=%d  on_time_rate=%.1f%%  MAE=%.4f",
             len(df), y.mean() * 100, mae)

    _save({"model": model, "scaler": scaler, "features": features, "mae": mae},
          "supplier_reliability.pkl")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("Starting KPI ML model training...")
    train_customer_churn()
    train_category_bcg()
    train_new_product_trial()
    train_shrinkage_anomaly()
    train_supplier_reliability()
    log.info("\nAll KPI models trained. Artifacts in: %s", ARTIFACTS)
    for f in os.listdir(ARTIFACTS):
        if f.endswith(".pkl"):
            size_kb = os.path.getsize(os.path.join(ARTIFACTS, f)) // 1024
            log.info("  %-35s  %d KB", f, size_kb)
