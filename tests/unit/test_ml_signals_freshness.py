"""Unit tests for MLAdapter.signals_freshness().

This is the DB-backed freshness of the `ml_signals` table (what the forecast
and ML cards actually read), as opposed to freshness() which only measures the
CSV files. A retrain can leave the CSVs fresh while load_to_db() silently fails,
leaving this table stale — so this check is what alerting keys off.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from kirana.ml_adapter import MLAdapter, ML_STALE_AFTER_HOURS


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeConn:
    def __init__(self, row=None, raise_exc=None):
        self._row = row
        self._raise = raise_exc

    def execute(self, *_a, **_k):
        if self._raise:
            raise self._raise
        return _FakeResult(self._row)


class _FakeEngine:
    """Minimal stand-in for a SQLAlchemy Engine: connect() is a context manager."""
    def __init__(self, row=None, raise_exc=None):
        self._row = row
        self._raise = raise_exc

    @contextmanager
    def connect(self):
        yield _FakeConn(self._row, self._raise)


def _adapter(engine):
    # results_dir is irrelevant here — signals_freshness only touches the engine.
    return MLAdapter(".", engine=engine)


class TestSignalsFreshness:
    def test_no_engine_reports_unavailable(self):
        out = MLAdapter(".", engine=None).signals_freshness()
        assert out == {"available": False, "reason": "no_engine"}

    def test_fresh_table_is_not_stale(self):
        newest = datetime.now(timezone.utc) - timedelta(hours=1)
        eng = _FakeEngine(row={"rows": 875, "stores": 11, "newest": newest})
        out = _adapter(eng).signals_freshness()
        assert out["available"] is True
        assert out["rows"] == 875
        assert out["stores"] == 11
        assert out["stale"] is False
        assert 0.9 <= out["age_hours"] <= 1.2

    def test_old_table_is_stale(self):
        newest = datetime.now(timezone.utc) - timedelta(hours=ML_STALE_AFTER_HOURS + 100)
        eng = _FakeEngine(row={"rows": 875, "stores": 11, "newest": newest})
        out = _adapter(eng).signals_freshness()
        assert out["stale"] is True
        assert out["age_hours"] > ML_STALE_AFTER_HOURS

    def test_naive_timestamp_is_treated_as_utc(self):
        # Some drivers hand back a tz-naive datetime; it must not crash.
        newest = datetime.utcnow() - timedelta(hours=2)  # naive
        eng = _FakeEngine(row={"rows": 10, "stores": 1, "newest": newest})
        out = _adapter(eng).signals_freshness()
        assert out["available"] is True
        assert out["age_hours"] is not None

    def test_empty_table_is_stale(self):
        eng = _FakeEngine(row={"rows": 0, "stores": 0, "newest": None})
        out = _adapter(eng).signals_freshness()
        assert out["available"] is True
        assert out["rows"] == 0
        assert out["age_hours"] is None
        assert out["stale"] is True

    def test_read_error_reports_unavailable(self):
        eng = _FakeEngine(raise_exc=RuntimeError("connection refused"))
        out = _adapter(eng).signals_freshness()
        assert out["available"] is False
        assert "connection refused" in out["reason"]
