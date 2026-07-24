"""Unit tests for the memory-bounded load_to_db path in MLAdapter.

The bug these guard against: load_to_db used to materialise the entire
~170k-row signal + recommendation lists (each dict carrying a JSON payload)
before inserting, which OOM-killed the 1Gi container. Training and the CSVs
would succeed while this DB-load step died silently, leaving ml_signals stale.
The fix builds AND inserts in row-chunks; these tests lock in the chunking and
the record shapes.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pandas as pd

from kirana.ml_adapter import MLAdapter


class _RecordingConn:
    def __init__(self):
        self.executes: list[tuple[str, object]] = []

    def execute(self, stmt, params=None):
        self.executes.append((str(stmt), params))
        return None

    def inserts(self, table: str):
        """(sql, params) pairs that INSERT into the given table."""
        return [(s, p) for s, p in self.executes
                if "INSERT INTO" in s and table in s]


class _FakeEngine:
    def __init__(self):
        self.conn = _RecordingConn()

    @contextmanager
    def begin(self):
        yield self.conn


def _signals_frame(n: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"store_id": 1, "product_id": i, "avg_daily_sales": float(i)}
        for i in range(n)
    ])


def _reco_frame(n: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"store_id": 1, "sku_id": i, "recommendation_type": "reorder_now",
         "product_name": f"P{i}", "category_name": "Cat", "reorder_qty": float(i)}
        for i in range(n)
    ])


class _StubAdapter(MLAdapter):
    """MLAdapter whose _compute_from_csvs is replaced with in-memory frames, so
    load_to_db can be exercised end-to-end without any CSVs on disk."""
    def __init__(self, ml_state, reco, engine):
        super().__init__(".", engine=engine)
        self._stub = (ml_state, reco)

    def _compute_from_csvs(self):
        return self._stub


class TestInsertChunked:
    def test_inserts_every_row_across_chunks(self):
        conn = _RecordingConn()
        frame = _signals_frame(9)
        n = MLAdapter._insert_chunked(
            conn, frame, MLAdapter._SIG_INSERT_SQL,
            MLAdapter._signal_record, chunk_size=4)
        assert n == 9
        # 9 rows / 4 => three inserts of 4, 4, 1
        assert len(conn.executes) == 3
        sizes = [len(p) for _, p in conn.executes]
        assert sizes == [4, 4, 1]

    def test_no_chunk_exceeds_chunk_size(self):
        conn = _RecordingConn()
        n = MLAdapter._insert_chunked(
            conn, _signals_frame(100), MLAdapter._SIG_INSERT_SQL,
            MLAdapter._signal_record, chunk_size=7)
        assert n == 100
        assert all(len(p) <= 7 for _, p in conn.executes)

    def test_empty_frame_writes_nothing(self):
        conn = _RecordingConn()
        n = MLAdapter._insert_chunked(
            conn, pd.DataFrame(), MLAdapter._SIG_INSERT_SQL,
            MLAdapter._signal_record, chunk_size=5)
        assert n == 0
        assert conn.executes == []


class TestRecordShapes:
    def test_signal_record_has_json_payload(self):
        rec = MLAdapter._signal_record(
            {"store_id": 3, "product_id": 42, "avg_daily_sales": 5.0})
        assert rec["store_id"] == 3
        assert rec["product_id"] == 42
        payload = json.loads(rec["payload"])
        assert payload["avg_daily_sales"] == 5.0

    def test_reco_record_excludes_columns_from_payload(self):
        rec = MLAdapter._reco_record({
            "store_id": 1, "sku_id": 9, "recommendation_type": "reorder_now",
            "product_name": "Rice", "category_name": "Staples", "reorder_qty": 12.0,
        })
        assert rec["rtype"] == "reorder_now"
        assert rec["product_name"] == "Rice"
        payload = json.loads(rec["payload"])
        # The identity columns live in their own columns, not the JSON blob.
        assert "store_id" not in payload
        assert "recommendation_type" not in payload
        assert payload["reorder_qty"] == 12.0


class TestLoadToDbEndToEnd:
    def test_truncates_then_chunk_inserts_both_tables(self):
        engine = _FakeEngine()
        adapter = _StubAdapter(_signals_frame(10), _reco_frame(6), engine)
        out = adapter.load_to_db(chunk_size=4)

        assert out == {"recommendations": 6, "signals": 10}

        conn = engine.conn
        # Both tables truncated exactly once before loading.
        truncs = [s for s, _ in conn.executes if "TRUNCATE" in s]
        assert any("ml_recommendations" in s for s in truncs)
        assert any("ml_signals" in s for s in truncs)

        # Signals: 10 rows / 4 => 3 inserts; recommendations: 6 / 4 => 2 inserts.
        assert len(conn.inserts("ml_signals")) == 3
        assert len(conn.inserts("ml_recommendations")) == 2

    def test_empty_state_still_truncates_and_reports_zero(self):
        engine = _FakeEngine()
        adapter = _StubAdapter(pd.DataFrame(), pd.DataFrame(), engine)
        out = adapter.load_to_db()
        assert out == {"recommendations": 0, "signals": 0}
        # Stale data is cleared even when the new snapshot is empty.
        assert any("TRUNCATE" in s for s, _ in engine.conn.executes)
        assert engine.conn.inserts("ml_signals") == []
