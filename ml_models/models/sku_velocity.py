"""
SKU Velocity Classifier
Detects Fast Moving and Slow Moving SKUs using XGBoost + velocity scoring.
"""
import os
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.preprocessing import StandardScaler

from config import FAST_PERCENTILE, SLOW_PERCENTILE, MODELS_DIR, RANDOM_SEED
from feature_engineering import VELOCITY_FEATURE_COLS


class SKUVelocityClassifier:
    """
    Two binary classifiers: is_fast_moving and is_slow_moving.
    Uses velocity_score (composite of turnover, fill_rate, avg_units_sold).
    """

    def __init__(self):
        self.fast_model:  xgb.XGBClassifier | None = None
        self.slow_model:  xgb.XGBClassifier | None = None
        self.scaler = StandardScaler()
        self.feature_cols = [c for c in VELOCITY_FEATURE_COLS if c != "velocity_score"]
        self.metrics = {}

    def _build(self, spw: float) -> xgb.XGBClassifier:
        return xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=spw,
            eval_metric="auc",
            device="cpu",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )

    def train(self, df: pd.DataFrame, verbose: bool = True):
        print(f"\n[SKUVelocityClassifier] Training on {len(df):,} SKUs")
        print(f"  Fast threshold: {FAST_PERCENTILE}th pct  |  Slow threshold: {SLOW_PERCENTILE}th pct")

        feat_cols = [c for c in self.feature_cols if c in df.columns]
        X = df[feat_cols].fillna(df[feat_cols].median()).astype(np.float32)
        X_scaled = self.scaler.fit_transform(X)

        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)
        self.trained_feature_cols = feat_cols

        for name, label in [("fast", "is_fast_moving"), ("slow", "is_slow_moving")]:
            y = df[label].values
            pos_rate = y.mean()
            spw = min((1 - pos_rate) / (pos_rate + 1e-6), 10.0)
            model = self._build(spw)

            y_prob = cross_val_predict(model, X_scaled, y, cv=cv, method="predict_proba")[:, 1]
            y_pred = (y_prob >= 0.5).astype(int)
            auc = roc_auc_score(y, y_prob)

            model.fit(X_scaled, y)

            if name == "fast":
                self.fast_model = model
            else:
                self.slow_model = model

            self.metrics[name] = {"auc": round(auc, 4), "pos_rate": round(pos_rate, 4)}

            if verbose:
                print(f"\n  [{name.upper()} moving]  pos_rate={pos_rate:.1%}  AUC={auc:.4f}")
                print(classification_report(y, y_pred, zero_division=0))

        self.save()

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        feat_cols = [c for c in self.trained_feature_cols if c in df.columns]
        X = df[feat_cols].fillna(0).astype(np.float32)
        X_scaled = self.scaler.transform(X)

        fast_prob = self.fast_model.predict_proba(X_scaled)[:, 1]
        slow_prob = self.slow_model.predict_proba(X_scaled)[:, 1]

        out = pd.DataFrame({
            "store_id":         df["store_id"].values if "store_id" in df else 0,
            "product_id":       df["product_id"].values,
            "prob_fast_moving": np.round(fast_prob, 4),
            "prob_slow_moving": np.round(slow_prob, 4),
            "is_fast_moving":   (fast_prob >= 0.5).astype(int),
            "is_slow_moving":   (slow_prob >= 0.5).astype(int),
            "velocity_score":   df["velocity_score"].values if "velocity_score" in df else np.nan,
            "avg_units_sold":   df["avg_units_sold"].values if "avg_units_sold" in df else np.nan,
            "avg_stock":        df["avg_stock"].values if "avg_stock" in df else np.nan,
            "avg_margin":       df["avg_margin"].values if "avg_margin" in df else np.nan,
        })
        return out

    def save(self):
        path = os.path.join(MODELS_DIR, "sku_velocity.pkl")
        joblib.dump({
            "fast_model": self.fast_model,
            "slow_model": self.slow_model,
            "scaler": self.scaler,
            "feature_cols": self.trained_feature_cols,
        }, path)
        print(f"  Saved -> {path}")

    @classmethod
    def load(cls) -> "SKUVelocityClassifier":
        path = os.path.join(MODELS_DIR, "sku_velocity.pkl")
        obj = cls.__new__(cls)
        saved = joblib.load(path)
        obj.fast_model = saved["fast_model"]
        obj.slow_model = saved["slow_model"]
        obj.scaler     = saved["scaler"]
        obj.trained_feature_cols = saved["feature_cols"]
        return obj
