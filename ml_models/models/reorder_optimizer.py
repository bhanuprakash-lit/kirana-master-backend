"""
AI Reorder Optimizer
XGBoost regression trained on EOQ-based targets,
augmented with stockout probabilities from the stockout model.
"""
import os
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.preprocessing import StandardScaler

from config import MODELS_DIR, RANDOM_SEED, SAFETY_STOCK_Z
from feature_engineering import REORDER_FEATURE_COLS


class ReorderOptimizer:
    """
    Predicts optimal reorder quantity per (store, product).
    Target is EOQ + safety-stock adjusted quantity (>0 only when stock < reorder_point).
    """

    def __init__(self):
        self.model: xgb.XGBRegressor | None = None
        self.scaler = StandardScaler()
        self.feature_cols = REORDER_FEATURE_COLS
        self.metrics = {}

    def train(self, df: pd.DataFrame, verbose: bool = True):
        # Train on ALL (store, product) pairs using EOQ as the target.
        # EOQ is non-zero for every product regardless of current stock level,
        # so we always have training samples. XGBoost learns the non-linear
        # interactions (demand variance, lead time, margin) that cause optimal
        # order quantity to deviate from pure EOQ math.
        target_col = "target_eoq" if "target_eoq" in df.columns else "eoq"
        train_df = df[df[target_col] > 0].copy()
        print(f"\n[ReorderOptimizer] Training on {len(train_df):,} / {len(df):,} store-SKUs "
              f"(target={target_col})")

        if len(train_df) < 10:
            self.model = None
            self.trained_feature_cols = [c for c in self.feature_cols if c in df.columns]
            self.metrics = {"mode": "rule_only", "note": "insufficient training data (<10 samples)"}
            if verbose:
                print("  Too few samples — falling back to pure EOQ + safety-stock rule.")
            self.save()
            return

        feat_cols = [c for c in self.feature_cols if c in train_df.columns]
        X = train_df[feat_cols].fillna(0).astype(np.float32)
        y = np.log1p(train_df[target_col].values)
        X_scaled = self.scaler.fit_transform(X)

        self.model = xgb.XGBRegressor(
            n_estimators=400,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            objective="reg:squarederror",
            device="cpu",
            random_state=RANDOM_SEED,
            n_jobs=-1,
        )

        cv = KFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED)
        y_pred_cv = cross_val_predict(self.model, X_scaled, y, cv=cv)

        y_orig      = np.expm1(y)
        y_pred_orig = np.expm1(y_pred_cv)

        mae  = mean_absolute_error(y_orig, y_pred_orig)
        mape = mean_absolute_percentage_error(y_orig, y_pred_orig) * 100
        r2   = r2_score(y, y_pred_cv)

        self.model.fit(X_scaled, y)
        self.trained_feature_cols = feat_cols
        self.metrics = {"mode": "ai_eoq", "n_samples": len(train_df),
                        "mae": round(mae, 2), "mape": round(mape, 2), "r2": round(r2, 4)}

        if verbose:
            print(f"  5-Fold CV  MAE={mae:.1f} units  MAPE={mape:.1f}%  R²={r2:.4f}")

        self.save()

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.model is None:
            model_qty = np.zeros(len(df))
        else:
            feat_cols = [c for c in self.trained_feature_cols if c in df.columns]
            X = df[feat_cols].fillna(0).astype(np.float32)
            X_scaled = self.scaler.transform(X)
            y_log = self.model.predict(X_scaled)
            model_qty = np.expm1(y_log).clip(0)

        current_stock = df["current_stock"].astype(float).values
        reorder_point = df["reorder_point"].astype(float).values
        safety_stock  = df["safety_stock"].astype(float).values
        eoq           = df["eoq"].astype(float).values
        avg_sales     = df["avg_daily_sales"].astype(float).values

        needs = current_stock < reorder_point
        rule_qty = np.maximum(eoq, reorder_point - current_stock + safety_stock)
        final_qty = np.where(needs, np.maximum(rule_qty, model_qty), 0.0)
        final_qty = np.round(final_qty).astype(int).clip(min=0)

        out = df[["store_id", "product_id"]].copy()
        out["predicted_reorder_qty"] = final_qty
        out["current_stock"]         = current_stock
        out["reorder_point"]         = np.round(reorder_point, 1)
        out["eoq"]                   = np.round(eoq, 0).astype(int)
        out["lead_time_days"]        = df["lead_time_days"].values
        out["avg_daily_sales"]       = np.round(avg_sales, 2)
        out["safety_stock"]          = np.round(safety_stock, 1)
        out["needs_reorder"]         = needs.astype(int)
        out["days_until_stockout"]   = np.round(current_stock / (avg_sales + 1e-6), 1)
        return out

    def save(self):
        path = os.path.join(MODELS_DIR, "reorder_optimizer.pkl")
        joblib.dump({
            "model": self.model,
            "scaler": self.scaler,
            "feature_cols": self.trained_feature_cols,
        }, path)
        print(f"  Saved -> {path}")

    @classmethod
    def load(cls) -> "ReorderOptimizer":
        path = os.path.join(MODELS_DIR, "reorder_optimizer.pkl")
        obj = cls.__new__(cls)
        saved = joblib.load(path)
        obj.model  = saved["model"]
        obj.scaler = saved["scaler"]
        obj.trained_feature_cols = saved.get("feature_cols", REORDER_FEATURE_COLS)
        obj.metrics = {}
        return obj
