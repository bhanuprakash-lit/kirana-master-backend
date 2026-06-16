"""Unit tests for vision.matcher — the fuzzy name → product_id mapping.

These bypass the DB by populating the matcher's in-memory index directly, so
they run without a TEST_DATABASE_URL.
"""
from __future__ import annotations

import pytest

from vision.analyzer import DetectedProduct
from vision.matcher import CatalogMatcher, UNKNOWN_THRESHOLD, match_detections


def _loaded_matcher(rows):
    """rows = list of (product_id, name). Build a matcher with that index, no DB."""
    m = CatalogMatcher(engine=None)
    m._ids = [r[0] for r in rows]
    m._names = [r[1] for r in rows]
    m._search = [r[1] for r in rows]
    m._loaded = True
    return m


CATALOG = [
    (1, "Parle-G Glucose Biscuits 200g"),
    (2, "Tata Salt 1kg"),
    (3, "Maggi 2-Minute Noodles Masala"),
    (4, "Santoor Sandal & Turmeric Soap"),
]


def test_exact_name_matches_with_high_score():
    m = _loaded_matcher(CATALOG)
    res = m.match("Tata Salt 1kg")
    assert res is not None
    assert res.product_id == 2
    assert res.is_unknown is False
    assert res.score >= UNKNOWN_THRESHOLD


def test_fuzzy_partial_name_still_matches_right_product():
    m = _loaded_matcher(CATALOG)
    res = m.match("parle g biscuit")
    assert res is not None and res.product_id == 1


def test_unrelated_query_is_marked_unknown():
    m = _loaded_matcher(CATALOG)
    res = m.match("completely unrelated widget xyz")
    # Either no confident row, or one below threshold → unknown.
    assert res is None or res.is_unknown is True


def test_blank_query_returns_none():
    m = _loaded_matcher(CATALOG)
    assert m.match("") is None
    assert m.match("   ") is None


def test_empty_catalog_returns_none():
    m = _loaded_matcher([])
    assert m.match("anything") is None


def test_match_detections_populates_known_fields():
    m = _loaded_matcher(CATALOG)
    # Inject our matcher as the module singleton via the public accessor cache.
    import vision.matcher as matcher_mod
    matcher_mod._matcher = m
    try:
        dets = [
            DetectedProduct(raw_name="Tata Salt 1kg", count=3, x1=0, y1=0, x2=1, y2=1,
                            visible_text="TATA SALT"),
            DetectedProduct(raw_name="zzz nonsense product", count=1, x1=0, y1=0, x2=1, y2=1,
                            visible_text="???"),
        ]
        match_detections(dets, engine=None)
    finally:
        matcher_mod._matcher = None

    known, unknown = dets
    assert known.product_id == 2
    assert known.is_unknown is False
    assert known.display_name == "Tata Salt 1kg"
    assert unknown.product_id is None
    assert unknown.is_unknown is True


def test_difflib_fallback_when_rapidfuzz_absent(monkeypatch):
    """If rapidfuzz import fails, the matcher must still work via difflib."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rapidfuzz" or name.startswith("rapidfuzz."):
            raise ImportError("simulated: rapidfuzz not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    m = _loaded_matcher(CATALOG)
    res = m.match("Maggi Masala Noodles")
    assert res is not None and res.product_id == 3
