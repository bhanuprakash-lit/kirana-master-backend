"""
Dead Stock Detector
Isolation Forest anomaly detection + rule-based labeling.
Products with low/stagnant sales while holding inventory.
"""
import os
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.preprocessing import StandardScaler

from config import DEAD_STOCK_DAYS, DEAD_STOCK_UNITS_THRESHOLD, MODELS_DIR, RANDOM_SEED
from feature_engineering import VELOCITY_FEATURE_COLS


class DeadStockDetector:
    """
    Two-stage detection:
    1. Isolation Forest for unsupervised anomaly scoring
    2. XGBoost supervised on rule-based labels + anomaly score
    """

    def __init__(self):
        self.iso_forest:  IsolationForest | None = None
        self.xgb_model:   xgb.XGBClassifier | None = None
        self.scaler = StandardScaler()
        self.feature_cols = [c for c in VELOCITY_FEATURE_COLS if c not in ("velocity_score",)]
        self.metrics = {}

    def train(self, df: pd.DataFrame, daily: pd.DataFrame, verbose: bool = True):
        print(f"\n[DeadStockDetector] Training on {len(df):,} (store, SKU) rows")

        max_date = daily["date"].max()
        recent_cutoff = max_date - pd.Timedelta(days=DEAD_STOCK_DAYS)
        recent_grp = ["store_id", "product_id"] if "store_id" in daily.columns else ["product_id"]
        recent = daily[daily["date"] >= recent_cutoff].groupby(recent_grp).agg(
            recent_avg_sales  = ("units_sold", "mean"),
            recent_days_sold  = ("units_sold", lambda x: (x > 0).sum()),
            recent_total_sold = ("units_sold", "sum"),
            recent_avg_stock  = ("stock_on_hand", "mean"),
        ).reset_index()

        df = df.merge(recent, on=recent_grp, how="left")
        df["recent_avg_sales"]   = df["recent_avg_sales"].fillna(0)
        df["recent_days_sold"]   = df["recent_days_sold"].fillna(0)
        df["recent_total_sold"]  = df["recent_total_sold"].fillna(0)
        df["recent_avg_stock"]   = df["recent_avg_stock"].fillna(df["avg_stock"])

        df["is_dead_stock"] = (
            (df["recent_avg_sales"] <= DEAD_STOCK_UNITS_THRESHOLD) &
            (df["recent_total_sold"] <= max(1, DEAD_STOCK_UNITS_THRESHOLD)) &
            (df["recent_avg_stock"] > 5)
        ).astype(int)

        all_feat = [c for c in self.feature_cols if c in df.columns] + [
            "recent_avg_sales", "recent_days_sold", "recent_total_sold", "recent_avg_stock"
        ]
        X = df[all_feat].fillna(0).astype(np.float32)
        y = df["is_dead_stock"].values
        X_scaled = self.scaler.fit_transform(X)

        # Stage 1: Isolation Forest anomaly score (always trained)
        self.iso_forest = IsolationForest(
            n_estimators=200, contamination=0.15, random_state=RANDOM_SEED, n_jobs=-1
        )
        self.iso_forest.fit(X_scaled)
        anomaly_score = -self.iso_forest.score_samples(X_scaled)
        X_with_iso = np.column_stack([X_scaled, anomaly_score])

        pos_rate = float(y.mean())
        self.trained_feature_cols = all_feat

        unique = np.unique(y)
        if len(unique) < 2:
            self.xgb_model = None
            self._iso_score_min = float(anomaly_score.min())
            self._iso_score_max = float(anomaly_score.max())
            self.metrics = {
                "model": "isolation_forest_only",
                "pos_rate": round(pos_rate, 4),
                "note": "no positive labels — supervised head skipped",
            }
            if verbose:
                print(f"  dead_stock pos_rate={pos_rate:.1%} — supervised head SKIPPED")
                print(f"  Falling back to IsolationForest score as the prob.")
        else:
            spw = min((1 - pos_rate) / (pos_rate + 1e-6), 10.0)
            self.xgb_model = xgb.XGBClassifier(
                n_estimators=300, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=spw, eval_metric="auc",
                device="cpu", random_state=RANDOM_SEED, n_jobs=-1,
            )
            cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)
            try:
                y_prob = cross_val_predict(
                    self.xgb_model, X_with_iso, y, cv=cv, method="predict_proba"
                )[:, 1]
                y_pred = (y_prob >= 0.5).astype(int)
                auc = roc_auc_score(y, y_prob)
            except Exception as exc:
                if verbose:
                    print(f"  CV failed ({exc}); training on full data")
                y_prob = np.full_like(y, fill_value=pos_rate, dtype=float)
                y_pred = (y_prob >= 0.5).astype(int)
                auc = float("nan")

            self.xgb_model.fit(X_with_iso, y)
            self.metrics = {"auc": round(auc, 4) if auc == auc else None,
                            "pos_rate": round(pos_rate, 4)}

            if verbose:
                print(f"  dead_stock pos_rate={pos_rate:.1%}  AUC={auc:.4f}")
                labels_present = sorted(set(unique.tolist()) | set([0, 1]))
                names = ["active", "dead_stock"][:len(labels_present)]
                print(classification_report(
                    y, y_pred, zero_division=0,
                    labels=labels_present, target_names=names,
                ))

        self.save()

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        feat_cols = [c for c in self.trained_feature_cols if c in df.columns]
        X = df[feat_cols].fillna(0).astype(np.float32)
        X_scaled = self.scaler.transform(X)
        anomaly_score = -self.iso_forest.score_samples(X_scaled)
        X_full = np.column_stack([X_scaled, anomaly_score])
        if self.xgb_model is not None:
            prob = self.xgb_model.predict_proba(X_full)[:, 1]
        else:
            lo = getattr(self, "_iso_score_min", float(anomaly_score.min()))
            hi = getattr(self, "_iso_score_max", float(anomaly_score.max()))
            denom = max(hi - lo, 1e-6)
            prob = np.clip((anomaly_score - lo) / denom, 0.0, 1.0)
        out = pd.DataFrame({
            "store_id":          df["store_id"].values if "store_id" in df else 0,
            "product_id":        df["product_id"].values,
            "prob_dead_stock":   np.round(prob, 4),
            "is_dead_stock":     (prob >= 0.5).astype(int),
            "anomaly_score":     np.round(anomaly_score, 4),
            "recent_avg_sales":  df.get("recent_avg_sales", pd.Series([np.nan]*len(df))).values,
            "recent_avg_stock":  df.get("recent_avg_stock", pd.Series([np.nan]*len(df))).values,
            "avg_stock":         df["avg_stock"].values if "avg_stock" in df else np.nan,
        })
        return out

    def save(self):
        path = os.path.join(MODELS_DIR, "deadstock_detector.pkl")
        joblib.dump({
            "iso_forest": self.iso_forest,
            "xgb_model": self.xgb_model,
            "scaler": self.scaler,
            "feature_cols": self.trained_feature_cols,
            "iso_score_min": getattr(self, "_iso_score_min", None),
            "iso_score_max": getattr(self, "_iso_score_max", None),
        }, path)
        print(f"  Saved -> {path}")

    @classmethod
    def load(cls) -> "DeadStockDetector":
        path = os.path.join(MODELS_DIR, "deadstock_detector.pkl")
        obj = cls.__new__(cls)
        saved = joblib.load(path)
        obj.iso_forest  = saved["iso_forest"]
        obj.xgb_model   = saved["xgb_model"]
        obj.scaler      = saved["scaler"]
        obj.trained_feature_cols = saved["feature_cols"]
        obj._iso_score_min = saved.get("iso_score_min")
        obj._iso_score_max = saved.get("iso_score_max")
        return obj
