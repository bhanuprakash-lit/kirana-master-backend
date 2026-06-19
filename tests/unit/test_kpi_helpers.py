"""Unit tests for kpis/calculator.py pure helpers.

These power every KPI's period calculation and trend interpretation,
so they need to be airtight.
"""
from __future__ import annotations

from datetime import date, timedelta

# from kpis.calculator import _period, _prev_period, _trend
from kpis.calculators.core import _period, _prev_period, _trend


class TestPeriod:
    def test_period_starts_n_days_before_today(self):
        start, end = _period(30)
        assert end == date.today()
        assert (end - start).days == 30

    def test_period_zero_days_collapses_to_today(self):
        start, end = _period(0)
        assert start == end == date.today()


class TestPrevPeriod:
    def test_prev_period_ends_where_current_period_starts(self):
        days = 30
        cur_start, _ = _period(days)
        prev_start, prev_end = _prev_period(days)
        assert prev_end == cur_start
        assert (prev_end - prev_start).days == days

    def test_prev_period_is_immediately_before_current(self):
        prev_start, prev_end = _prev_period(7)
        cur_start, _ = _period(7)
        assert prev_end == cur_start


class TestTrend:
    def test_up_when_growing_and_higher_is_better(self):
        t = _trend(120, 100, higher_is_better=True)
        assert t["direction"] == "up"
        assert t["pct_change"] == 20.0
        assert "Improving" in t["interpretation"]

    def test_down_when_shrinking_and_higher_is_better(self):
        t = _trend(80, 100, higher_is_better=True)
        assert t["direction"] == "down"
        assert t["pct_change"] == -20.0
        assert "Declining" in t["interpretation"]

    def test_up_when_shrinking_and_lower_is_better(self):
        # E.g. dead stock — going down means improving.
        t = _trend(50, 100, higher_is_better=False)
        assert t["direction"] == "up"
        assert "Improving" in t["interpretation"]

    def test_stable_when_change_under_one_percent(self):
        t = _trend(100.5, 100, higher_is_better=True)
        assert t["direction"] == "stable"
        assert "No significant change" in t["interpretation"]

    def test_handles_none_inputs(self):
        t = _trend(None, 100)
        assert t["direction"] == "stable"
        assert t["pct_change"] is None
        assert "Insufficient data" in t["interpretation"]

    def test_handles_zero_baseline(self):
        # Division by zero is dodged by returning the "insufficient data" payload.
        t = _trend(50, 0)
        assert t["direction"] == "stable"
        assert t["pct_change"] is None

    def test_payload_includes_raw_values(self):
        t = _trend(120, 100, higher_is_better=True)
        assert t["current_value"] == 120
        assert t["previous_value"] == 100
