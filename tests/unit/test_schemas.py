"""Pydantic schema validation tests for kirana/schemas.py.

Covers the request/response shapes the mobile app depends on. A change
to defaults or required fields here is a backwards-incompatible API
change and should break these tests loudly.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from kirana.schemas import (
    AuthUser,
    BasketCreate,
    BasketItemInput,
    CashflowRequestCreate,
    ChangePasswordRequest,
    FcmTokenUpdate,
    IssueReportCreate,
    LoginRequest,
    PhoneLoginRequest,
    RecommendationItem,
    RecommendationQueryRequest,
    RegisterStoreOwnerRequest,
    StoreUpdateRequest,
    SubscriptionUpgradeRequest,
    UdhaarAddRequest,
    UdhaarRecoveryRequest,
    UserPrefs,
    UserPrefsUpdate,
)


class TestLoginRequest:
    def test_minimal_valid_payload(self):
        req = LoginRequest(username="ramesh", password="secret")
        assert req.username == "ramesh"
        assert req.password == "secret"

    def test_username_is_required(self):
        with pytest.raises(ValidationError):
            LoginRequest(password="secret")

    def test_password_is_required(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="ramesh")


class TestPhoneLoginRequest:
    def test_both_fields_required(self):
        with pytest.raises(ValidationError):
            PhoneLoginRequest(phone_number="+919999999999")
        with pytest.raises(ValidationError):
            PhoneLoginRequest(firebase_uid="abc")

    def test_complete_payload(self):
        req = PhoneLoginRequest(phone_number="+919999999999", firebase_uid="xyz")
        assert req.phone_number == "+919999999999"


class TestAuthUser:
    def test_role_must_be_string(self):
        u = AuthUser(user_id=1, username="r", full_name="Ramesh", role="store_owner")
        assert u.role == "store_owner"
        assert u.store_id is None       # optional

    def test_store_id_passes_through(self):
        u = AuthUser(user_id=1, username="r", full_name="R", role="admin", store_id=7)
        assert u.store_id == 7


class TestRegisterStoreOwnerRequest:
    def test_minimal_required_fields(self):
        req = RegisterStoreOwnerRequest(
            username="x", full_name="X", store_name="X Store"
        )
        # Defaults the doc claims
        assert req.password == ""
        assert req.store_type == "kirana"
        assert req.footfall == 40
        assert req.phone_number is None
        assert req.firebase_uid is None

    def test_phone_auth_path_allows_empty_password(self):
        req = RegisterStoreOwnerRequest(
            username="x", full_name="X", store_name="X Store",
            phone_number="+91...", firebase_uid="abc",
        )
        assert req.password == ""
        assert req.phone_number == "+91..."

    def test_lat_lng_optional(self):
        req = RegisterStoreOwnerRequest(
            username="x", full_name="X", store_name="X Store",
            latitude=12.9, longitude=77.5,
        )
        assert req.latitude == 12.9
        assert req.longitude == 77.5


class TestUserPrefsDefaults:
    def test_all_defaults_match_documented_values(self):
        p = UserPrefs()
        assert p.forecast_horizon_days == 7
        assert p.alert_stockout_threshold == 0.5
        assert p.alert_min_velocity == 0.3
        assert p.alert_reorder_days == 3
        assert p.alert_dead_stock_days == 21
        assert p.notify_in_app is True
        assert p.notify_whatsapp is False
        assert p.quiet_hours_start == 22
        assert p.quiet_hours_end == 7
        assert p.allow_social_marketing is False
        assert p.alert_expiry_days == 7


class TestUserPrefsUpdate:
    def test_all_fields_optional(self):
        # Empty update must validate — used for "leave everything alone".
        UserPrefsUpdate()

    def test_partial_update_keeps_unspecified_fields_none(self):
        u = UserPrefsUpdate(notify_in_app=False)
        assert u.notify_in_app is False
        assert u.forecast_horizon_days is None
        assert u.subscribed_kpis is None


class TestUdhaarSchemas:
    def test_add_requires_all_three_fields(self):
        with pytest.raises(ValidationError):
            UdhaarAddRequest(customer_name="R", phone="9999")
        # Complete payload validates.
        req = UdhaarAddRequest(customer_name="R", phone="9999", amount=450.0)
        assert req.amount == 450.0

    def test_recovery_requires_khata_id_and_amount(self):
        with pytest.raises(ValidationError):
            UdhaarRecoveryRequest(khata_id=1)
        req = UdhaarRecoveryRequest(khata_id=1, amount=100.0)
        assert req.amount == 100.0


class TestRecommendationItem:
    def test_only_three_fields_are_required(self):
        # store_id, sku_id, and recommendation_type are required;
        # everything else has a default.
        item = RecommendationItem(store_id=1, sku_id=42, recommendation_type="reorder_now")
        assert item.priority == "medium"     # default
        assert item.message == ""            # default
        assert item.stockout_probability is None

    def test_all_optional_metrics_default_to_none(self):
        item = RecommendationItem(store_id=1, sku_id=1, recommendation_type="x")
        for attr in (
            "stockout_probability", "prob_stockout_3d", "prob_stockout_7d",
            "prob_stockout_30d", "reorder_qty", "forecast_demand",
            "current_stock", "days_to_stockout", "current_price",
            "optimal_price", "price_change_pct", "expected_profit_impact",
            "effective_margin", "reorder_point",
        ):
            assert getattr(item, attr) is None


class TestRecommendationQueryRequest:
    def test_defaults(self):
        q = RecommendationQueryRequest()
        assert q.store_id is None
        assert q.sku_ids is None
        assert q.top_n is None
        assert q.only_reorder is False
        assert q.only_high_priority is False
        assert q.sort_by == "expected_profit"


class TestStoreUpdateRequest:
    def test_every_field_is_optional(self):
        # PATCH semantics — empty body is valid.
        StoreUpdateRequest()

    def test_partial_update(self):
        u = StoreUpdateRequest(store_name="New Name", footfall=80)
        assert u.store_name == "New Name"
        assert u.footfall == 80
        assert u.budget is None


class TestChangePasswordRequest:
    def test_old_password_optional_for_first_time_set(self):
        # Phone-OTP users have no password to begin with.
        req = ChangePasswordRequest(new_password="new", confirm_password="new")
        assert req.old_password is None


class TestSubscriptionAndPayment:
    def test_upgrade_request_carries_tier(self):
        u = SubscriptionUpgradeRequest(tier="pro")
        assert u.tier == "pro"

    def test_cashflow_request_minimal(self):
        c = CashflowRequestCreate(store_id=1, amount_requested=50000)
        assert c.selected_bank is None


class TestSupport:
    def test_issue_report_requires_all_fields(self):
        with pytest.raises(ValidationError):
            IssueReportCreate(category="bug")

    def test_complete_issue_report(self):
        r = IssueReportCreate(category="bug", title="t", description="d")
        assert r.title == "t"


class TestFcmTokenUpdate:
    def test_token_is_required(self):
        with pytest.raises(ValidationError):
            FcmTokenUpdate()
        FcmTokenUpdate(fcm_token="abc")


class TestBaskets:
    def test_minimal_basket_item(self):
        item = BasketItemInput(product_id=1)
        assert item.qty == 1.0
        assert item.product_name is None

    def test_basket_create_defaults_items_to_empty_list(self):
        b = BasketCreate(name="Pongal Combo")
        assert b.items == []
        assert b.description is None
        assert b.price is None
