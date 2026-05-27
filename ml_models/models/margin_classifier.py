"""
High Profit Margin SKU Classifier
XGBoost binary classifier + percentile-based labeling.
"""
import os
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, roc_auc_score, average_precision_score
from sklearn.preprocessing import StandardScaler

from config import HIGH_MARGIN_PERCENTILE, MODELS_DIR, RANDOM_SEED
from feature_engineering import MARGIN_FEATURE_COLS


class MarginClassifier:
    """Classifies SKUs as high-profit or standard margin."""

    def __init__(self):
        self.model: xgb.XGBClassifier | None = None
        self.scaler = StandardScaler()
        self.feature_cols = [c for c in MARGIN_FEATURE_COLS if c != "effective_margin"]
        self.threshold = None

    def train(self, df: pd.DataFrame, verbose: bool = True):
        print(f"\n[MarginClassifier] Training on {len(df):,} SKUs, threshold={HIGH_MARGIN_PERCENTILE}th pct")

        self.threshold = df["effective_margin"].quantile(HIGH_MARGIN_PERCENTILE / 100)
        print(f"  High-margin threshold: {self.threshold:.2f}%")

        feat_cols = [c for c in self.feature_cols if c in df.columns]
        X = df[feat_cols].fillna(df[feat_cols].median()).astype(np.float32)
        y = df["is_high_margin"].values

        X_scaled = self.scaler.fit_transform(X)

        pos_rate = y.mean()
        spw = (1 - pos_rate) / (pos_rate + 1e-6)

        self.model = xgb.XGBClassifier(
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

        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)
        y_prob_cv = cross_val_predict(self.model, X_scaled, y, cv=cv, method="predict_proba")[:, 1]
        y_pred_cv = (y_prob_cv >= 0.5).astype(int)

        auc  = roc_auc_score(y, y_prob_cv)
        aupr = average_precision_score(y, y_prob_cv)

        if verbose:
            print(f"  5-Fold CV  AUC={auc:.4f}  AUPR={aupr:.4f}  pos_rate={pos_rate:.1%}")
            print(classification_report(y, y_pred_cv, zero_division=0, target_names=["standard", "high_margin"]))

        self.model.fit(X_scaled, y)
        self.trained_feature_cols = feat_cols
        self.metrics = {"auc": round(auc, 4), "aupr": round(aupr, 4)}
        self.save()

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        feat_cols = [c for c in self.trained_feature_cols if c in df.columns]
        X = df[feat_cols].fillna(0).astype(np.float32)
        X_scaled = self.scaler.transform(X)
        prob = self.model.predict_proba(X_scaled)[:, 1]
        out = pd.DataFrame({
            "store_id":         df["store_id"].values if "store_id" in df else 0,
            "product_id":       df["product_id"].values,
            "prob_high_margin": np.round(prob, 4),
            "is_high_margin":   (prob >= 0.5).astype(int),
            "effective_margin": df["effective_margin"].values if "effective_margin" in df else np.nan,
            "avg_units_sold":   df["avg_units_sold"].values if "avg_units_sold" in df else np.nan,
            "avg_price":        df["avg_price"].values if "avg_price" in df else np.nan,
        })
        return out

    def save(self):
        path = os.path.join(MODELS_DIR, "margin_classifier.pkl")
        joblib.dump({
            "model": self.model,
            "scaler": self.scaler,
            "threshold": self.threshold,
            "feature_cols": self.trained_feature_cols,
        }, path)
        print(f"  Saved -> {path}")

    @classmethod
    def load(cls) -> "MarginClassifier":
        path = os.path.join(MODELS_DIR, "margin_classifier.pkl")
        obj = cls.__new__(cls)
        saved = joblib.load(path)
        obj.model               = saved["model"]
        obj.scaler              = saved["scaler"]
        obj.threshold           = saved["threshold"]
        obj.trained_feature_cols = saved["feature_cols"]
        return obj
