"""Unit tests for the recommendation-message builders in kirana/service.py.

These functions take an ML row dict and produce the human-readable string
shown in the mobile app's intelligence cards.
"""
from __future__ import annotations

from kirana.service import (
    _advice,
    _msg_dead_stock,
    _msg_fast_moving,
    _msg_profit,
    _msg_reorder,
    _msg_stockout,
    _vertical_family,
)


class TestStockoutMessage:
    def test_complete_payload_reports_units_velocity_and_eta(self):
        msg = _msg_stockout({
            "current_stock": 12,
            "forecast_demand": 4,
            "days_to_stockout": 3.0,
            "stockout_prob": 0.85,
        })
        assert "12 units left" in msg
        assert "around 4 units/day" in msg
        assert "around 3 days" in msg
        assert "85%" in msg

    def test_zero_velocity_falls_back_to_generic_message(self):
        msg = _msg_stockout({
            "current_stock": 0,
            "forecast_demand": 0,
            "stockout_prob": 0.6,
        })
        assert "Predicted to run out within a week" in msg
        assert "60%" in msg

    def test_missing_days_falls_back(self):
        msg = _msg_stockout({"stockout_prob": 0.9, "forecast_demand": 5})
        assert "Predicted to run out within a week" in msg


class TestReorderMessage:
    def test_full_payload_mentions_qty_and_cover(self):
        msg = _msg_reorder({
            "reorder_qty": 30,
            "current_stock": 5,
            "reorder_point": 10,
            "forecast_demand": 3,
            "days_to_stockout": 2.0,
        })
        # Below reorder point, includes velocity, days left, qty + cover days.
        assert "Stock 5 units" in msg
        assert "reorder point of 10" in msg
        assert "around 3 units/day" in msg
        assert "Order around 30 units" in msg
        # 30 qty / 3 per day = 10 days cover
        assert "around 10 days" in msg

    def test_handles_empty_payload(self):
        msg = _msg_reorder({})
        assert "Stock has dipped below the reorder point" in msg
        assert "Order around 0 units" in msg


class TestFastMovingMessage:
    def test_includes_velocity_and_cover_days(self):
        msg = _msg_fast_moving({"forecast_demand": 12, "current_stock": 48})
        assert "around 12 units/day" in msg
        # 48 stock / 12 per day = 4 days
        assert "around 4 days" in msg
        assert "morning rush" in msg

    def test_no_stock_still_includes_velocity(self):
        msg = _msg_fast_moving({"forecast_demand": 8, "current_stock": 0})
        assert "around 8 units/day" in msg
        assert "morning rush" in msg


class TestProfitMessage:
    def test_full_payload_includes_margin_velocity_and_projection(self):
        msg = _msg_profit({
            "effective_margin": 28,
            "forecast_demand": 6,
            "expected_profit": 4500,
        })
        assert "Margin around 28%" in msg
        assert "around 6 units/day" in msg
        # 4500 rounds to nearest hundred -> 4,500
        assert "₹4,500" in msg
        assert "eye-level" in msg

    def test_zero_projection_omits_rupee_clause(self):
        msg = _msg_profit({"effective_margin": 25, "forecast_demand": 4, "expected_profit": 0})
        assert "Margin around 25%" in msg
        assert "₹" not in msg


class TestDeadStockMessage:
    def test_with_price_includes_capital_tied_up(self):
        msg = _msg_dead_stock({"current_stock": 40, "current_price": 50})
        # 40 * 50 = 2000 rupees, rounded to nearest 100 = 2,000
        assert "40 units sitting on shelf" in msg
        assert "₹2,000" in msg
        assert "markdown" in msg

    def test_without_price_omits_capital_clause(self):
        msg = _msg_dead_stock({"current_stock": 15, "current_price": None})
        assert "15 units sitting on shelf" in msg
        assert "₹" not in msg

    def test_zero_price_omits_capital_clause(self):
        msg = _msg_dead_stock({"current_stock": 10, "current_price": 0})
        assert "₹" not in msg


class TestVerticalFamily:
    def test_grocery_is_the_default_for_unset_and_unknown(self):
        assert _vertical_family(None) == "grocery"
        assert _vertical_family("") == "grocery"
        assert _vertical_family("something_new") == "grocery"

    def test_apparel_family_collapses_together(self):
        for v in ("apparel", "footwear", "boutique", "sports_fitness", "cosmetics"):
            assert _vertical_family(v) == "apparel"

    def test_bakery_speaks_grocery(self):
        assert _vertical_family("bakery") == "grocery"

    def test_own_families_pass_through(self):
        for v in ("electronics", "optical", "services", "general"):
            assert _vertical_family(v) == v


class TestAdviceTail:
    def test_neutral_types_have_no_advice_tail(self):
        # stockout/reorder advice is already vertical-neutral, so no tail here.
        assert _advice("stockout_risk", "apparel") == ""
        assert _advice("reorder_now", "electronics") == ""

    def test_every_family_resolves_a_non_empty_tail(self):
        for rtype in ("fast_moving", "profit_opportunity", "dead_stock"):
            for v in ("grocery", "apparel", "electronics", "optical",
                      "services", "general", "bakery", "footwear"):
                assert _advice(rtype, v).strip()


class TestVerticalAwareMessages:
    """G6: grocery wording stays byte-identical; other verticals lose the
    grocery-only phrases (morning rush / eye-level / return-to-vendor / shelf)."""

    def test_grocery_default_is_unchanged(self):
        assert "morning rush" in _msg_fast_moving({"forecast_demand": 5})
        assert "eye-level" in _msg_profit({"effective_margin": 30})
        dead = _msg_dead_stock({"current_stock": 8})
        assert "sitting on shelf" in dead
        assert "return-to-vendor" in dead

    def test_apparel_drops_grocery_phrases(self):
        fast = _msg_fast_moving({"forecast_demand": 5, "current_stock": 20}, "apparel")
        assert "morning rush" not in fast
        assert "sizes and colours" in fast
        profit = _msg_profit({"effective_margin": 30}, "apparel")
        assert "eye-level" not in profit
        dead = _msg_dead_stock({"current_stock": 8}, "boutique")  # apparel family
        assert "on shelf" not in dead
        assert "clearance" in dead

    def test_services_gets_capacity_wording_not_shelf(self):
        fast = _msg_fast_moving({"forecast_demand": 5}, "services")
        assert "shelf" not in fast
        assert "capacity" in fast

    def test_electronics_dead_stock_reads_naturally(self):
        dead = _msg_dead_stock({"current_stock": 3, "current_price": 5000}, "electronics")
        assert "sitting in stock" in dead
        assert "bundle" in dead
