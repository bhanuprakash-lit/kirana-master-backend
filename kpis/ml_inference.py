"""
KPI ML Inference — loads trained KPI models and exposes prediction methods
used to enrich the SQL-calculated KPI responses.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger("kpis.ml")

_ARTIFACTS = os.path.join(
    os.path.dirname(__file__), "..", "ml_models", "kpi_models", "artifacts"
)


def _load(name: str):
    path = os.path.join(_ARTIFACTS, name)
    if not os.path.exists(path):
        logger.warning("KPI model not found: %s — run train_kpi_models.py", path)
        return None
    return joblib.load(path)


class KPIMLModels:
    """Lazy-loaded container for all 5 KPI ML models."""

    def __init__(self):
        self._churn      = None
        self._bcg        = None
        self._trial      = None
        self._shrinkage  = None
        self._supplier   = None
        self._loaded     = False

    def _ensure(self):
        if self._loaded:
            return
        self._churn     = _load("customer_churn.pkl")
        self._bcg       = _load("category_bcg.pkl")
        self._trial     = _load("new_product_trial.pkl")
        self._shrinkage = _load("shrinkage_anomaly.pkl")
        self._supplier  = _load("supplier_reliability.pkl")
        self._loaded    = True

    # ── Customer Churn ────────────────────────────────────────────────────────

    def predict_churn(self, customer_features: list[dict]) -> list[dict]:
        """
        customer_features: list of dicts with keys matching training features.
        Returns list of {customer_id, churn_prob, churn_risk}.
        """
        self._ensure()
        if not self._churn or not customer_features:
            return []
        feats = self._churn["features"]
        scaler = self._churn["scaler"]
        model  = self._churn["model"]
        X = np.array([[r.get(f, 0) for f in feats] for r in customer_features], dtype=np.float32)
        Xs = scaler.transform(X)
        probs = model.predict_proba(Xs)[:, 1]
        return [
            {"customer_id": r.get("customer_id"),
             "churn_prob": round(float(p), 4),
             "churn_risk": "high" if p >= 0.7 else ("medium" if p >= 0.4 else "low")}
            for r, p in zip(customer_features, probs)
        ]

    # ── Category BCG ──────────────────────────────────────────────────────────

    def predict_bcg(self, category_features: list[dict]) -> list[dict]:
        """
        Assigns BCG quadrant label to each (category, store) row.
        category_features: list of dicts with rev_share, margin_pct, velocity.
        """
        self._ensure()
        if not self._bcg or not category_features:
            return [{**r, "bcg_quadrant": "unknown"} for r in category_features]
        feats  = self._bcg["features"]
        scaler = self._bcg["scaler"]
        km     = self._bcg["model"]
        labels = self._bcg["cluster_labels"]
        X = np.array([[r.get(f, 0) for f in feats] for r in category_features], dtype=np.float64)
        Xs = scaler.transform(X).astype(np.float64)
        clusters = km.predict(Xs)
        return [
            {**r, "bcg_quadrant": labels.get(int(c), "unknown")}
            for r, c in zip(category_features, clusters)
        ]

    # ── New Product Trial ─────────────────────────────────────────────────────

    def predict_trial_success(self, product_features: list[dict]) -> list[float]:
        """Returns probability of 30d success for each product."""
        self._ensure()
        if not self._trial or not product_features:
            return [0.5] * len(product_features)
        feats  = self._trial["features"]
        scaler = self._trial["scaler"]
        model  = self._trial["model"]
        X = np.array([[r.get(f, 0) for f in feats] for r in product_features], dtype=np.float32)
        Xs = scaler.transform(X)
        return [round(float(p), 4) for p in model.predict_proba(Xs)[:, 1]]

    # ── Shrinkage Anomaly ─────────────────────────────────────────────────────

    def score_shrinkage(self, product_ids: list[int], shrinkage_units: list[int]) -> dict[int, float]:
        """
        Returns anomaly score (0-1) per product_id.
        Higher = more anomalous.
        """
        self._ensure()
        if not self._shrinkage or not product_ids:
            return {}
        feats  = self._shrinkage["features"]
        scaler = self._shrinkage["scaler"]
        iso    = self._shrinkage["model"]

        # Build feature vectors with available data
        # shrinkage, shrinkage_rate, opening_pct, purchased_ratio, purchased, sold
        rows = []
        for pid, su in zip(product_ids, shrinkage_units):
            rows.append({"shrinkage": su, "shrinkage_rate": su / max(1, 10),
                         "opening_pct": su / max(1, 30), "purchased_ratio": 0.5,
                         "purchased": 5, "sold": 20})
        X = np.array([[r.get(f, 0) for f in feats] for r in rows], dtype=np.float32)
        Xs = scaler.transform(X)
        raw_scores = -iso.score_samples(Xs)
        # Normalise to 0-1
        mn, mx = raw_scores.min(), raw_scores.max()
        if mx > mn:
            normed = (raw_scores - mn) / (mx - mn)
        else:
            normed = np.zeros_like(raw_scores)
        return {pid: round(float(s), 4) for pid, s in zip(product_ids, normed)}

    # ── Supplier Reliability ──────────────────────────────────────────────────

    def score_supplier_reliability(self, supplier_features: list[dict]) -> list[float]:
        """
        Predicts on-time score (0-1) per supplier.
        supplier_features: list of dicts with actual_cost, standard_cost,
                           expected_lead, lead_variance, price_accuracy.
        """
        self._ensure()
        if not self._supplier or not supplier_features:
            return [0.5] * len(supplier_features)
        feats  = self._supplier["features"]
        scaler = self._supplier["scaler"]
        model  = self._supplier["model"]
        X = np.array([[r.get(f, 0) for f in feats] for r in supplier_features], dtype=np.float32)
        Xs = scaler.transform(X)
        preds = model.predict(Xs)
        return [round(float(np.clip(p, 0, 1)), 4) for p in preds]


# Singleton
_kpi_models = KPIMLModels()


def get_kpi_models() -> KPIMLModels:
    return _kpi_models
