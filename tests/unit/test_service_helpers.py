"""Unit tests for the pure helpers in kirana/service.py.

These functions are private (underscore prefix) but they're the core of
the recommendation-message pipeline, so they get their own tests.
"""
from __future__ import annotations

import math

import pytest

from kirana.service import (
    _PRIORITY,
    _round_days,
    _round_int,
    _round_rupees,
    _safe,
)


class TestSafe:
    def test_passes_through_regular_values(self):
        assert _safe(0) == 0
        assert _safe(1.5) == 1.5
        assert _safe(-7) == -7

    def test_returns_none_for_none(self):
        assert _safe(None) is None

    def test_returns_none_for_nan(self):
        assert _safe(float("nan")) is None

    def test_returns_none_for_inf(self):
        assert _safe(float("inf")) is None
        assert _safe(float("-inf")) is None

    def test_strings_pass_through_unchanged(self):
        # _safe only nulls numerics; strings have no NaN concept and are returned as-is.
        assert _safe("hello") == "hello"


class TestRoundInt:
    @pytest.mark.parametrize("inp,expected", [
        (3, 3),
        (3.4, 3),
        (3.6, 4),
        (-2.5, -2),    # banker's rounding ties to even
        ("12.7", 13),
        (0, 0),
    ])
    def test_rounds_to_int(self, inp, expected):
        assert _round_int(inp) == expected

    @pytest.mark.parametrize("bad", [None, "x", "", float("nan")])
    def test_invalid_input_returns_zero(self, bad):
        # NaN -> int(round(nan)) raises ValueError, which is caught.
        assert _round_int(bad) == 0


class TestRoundDays:
    @pytest.mark.parametrize("days,expected", [
        (0, "today"),
        (0.5, "today"),
        (0.51, "tomorrow"),
        (1.5, "tomorrow"),
        (1.51, "around 2 days"),
        (3, "around 3 days"),
        (10, "around 10 days"),
    ])
    def test_buckets(self, days, expected):
        assert _round_days(days) == expected

    def test_invalid_input_returns_soon(self):
        assert _round_days(None) == "soon"
        assert _round_days("nope") == "soon"


class TestRoundRupees:
    def test_below_thousand_rounds_to_nearest_ten(self):
        assert _round_rupees(457) == 460
        assert _round_rupees(454) == 450
        assert _round_rupees(999) == 1000

    def test_thousand_and_above_rounds_to_nearest_hundred(self):
        assert _round_rupees(1234) == 1200
        assert _round_rupees(1250) == 1200      # banker's rounding ties to even
        assert _round_rupees(1251) == 1300
        assert _round_rupees(12700) == 12700

    def test_invalid_input_returns_zero(self):
        assert _round_rupees(None) == 0
        assert _round_rupees("x") == 0


class TestPriorityRules:
    def test_stockout_high_above_threshold(self):
        assert _PRIORITY["stockout_risk"]({"stockout_prob": 0.85}) == "high"
        assert _PRIORITY["stockout_risk"]({"stockout_prob": 0.8}) == "high"
        assert _PRIORITY["stockout_risk"]({"stockout_prob": 0.5}) == "medium"

    def test_stockout_handles_missing_probability(self):
        # Missing -> 0 -> medium
        assert _PRIORITY["stockout_risk"]({}) == "medium"

    def test_reorder_high_when_runway_short(self):
        assert _PRIORITY["reorder_now"]({"days_to_stockout": 2}) == "high"
        assert _PRIORITY["reorder_now"]({"days_to_stockout": 3}) == "high"
        assert _PRIORITY["reorder_now"]({"days_to_stockout": 5}) == "medium"

    def test_reorder_defaults_to_medium_when_unknown(self):
        # Missing days -> falls through to 99 sentinel -> medium
        assert _PRIORITY["reorder_now"]({}) == "medium"

    def test_dead_stock_high_when_pile_is_large(self):
        assert _PRIORITY["dead_stock"]({"current_stock": 20}) == "high"
        assert _PRIORITY["dead_stock"]({"current_stock": 200}) == "high"
        assert _PRIORITY["dead_stock"]({"current_stock": 5}) == "medium"

    def test_fast_moving_and_profit_are_always_medium(self):
        assert _PRIORITY["fast_moving"]({}) == "medium"
        assert _PRIORITY["profit_opportunity"]({}) == "medium"

    def test_nan_stockout_prob_is_treated_as_zero(self):
        # _safe nulls the NaN; downstream `or 0` yields 0 -> medium.
        assert _PRIORITY["stockout_risk"]({"stockout_prob": math.nan}) == "medium"
