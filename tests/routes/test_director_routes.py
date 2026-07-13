"""Route + auth tests for the director analytics dashboard.

Auth gating and wiring are covered here without a DB (the JSON endpoints'
SQL is exercised end-to-end against a real Postgres in the deploy/verify step).
The auth dependency itself is unit-tested directly since it is security-critical.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


# ── app / client ─────────────────────────────────────────────────────────────
@pytest.fixture
def director_app(test_app):
    from analytics.routes import router as analytics_router

    test_app.include_router(analytics_router)
    return test_app


@pytest.fixture
def director_client(director_app):
    from fastapi.testclient import TestClient

    with TestClient(director_app) as c:
        yield c


# Representative JSON endpoints spanning the domains.
API_ENDPOINTS = [
    "/director/api/stores",
    "/director/api/overview",
    "/director/api/sales",
    "/director/api/customers",
    "/director/api/ai",
    "/director/api/subscriptions",
    "/director/api/footfall",
    "/director/api/vision",
]


class TestAuthGating:
    @pytest.mark.parametrize("path", API_ENDPOINTS)
    def test_no_token_is_401(self, director_client, path):
        assert director_client.get(path).status_code == 401

    @pytest.mark.parametrize("path", API_ENDPOINTS)
    def test_wrong_token_is_401(self, director_client, path):
        r = director_client.get(path, headers={"X-Director-Token": "nope"})
        assert r.status_code == 401

    def test_wrong_admin_key_is_401(self, director_client):
        r = director_client.get(
            "/director/api/overview", headers={"X-API-Key": "not-the-key"})
        assert r.status_code == 401

    def test_days_out_of_range_still_requires_auth_first(self, director_client):
        # Unauthorized wins over validation — no data leaks via error messages.
        assert director_client.get(
            "/director/api/sales?days=9999").status_code == 401


class TestStaticWiring:
    def test_dashboard_page_served(self, director_client):
        r = director_client.get("/director")
        assert r.status_code == 200
        assert "Director Analytics" in r.text
        # The page must reference the vendored chart lib route.
        assert "/director/vendor/chart.js" in r.text

    def test_vendored_chartjs_served(self, director_client):
        r = director_client.get("/director/vendor/chart.js")
        assert r.status_code == 200
        assert "chart" in r.headers.get("content-type", "").lower() or \
               "javascript" in r.headers.get("content-type", "").lower()


# ── require_director unit tests ──────────────────────────────────────────────
class _FakeState:
    def __init__(self, director_token, kirana_api_key):
        self.settings = type("S", (), {
            "director_token": director_token, "kirana_api_key": kirana_api_key})()


class _FakeReq:
    def __init__(self, state, query=None, headers=None):
        self.app = type("A", (), {"state": state})()
        self.query_params = query or {}
        self.headers = headers or {}


def _state(dt="secret-tok", ak="admin-key"):
    return _FakeState(dt, ak)


class TestRequireDirector:
    def test_valid_token_via_query(self):
        from analytics.auth import require_director
        req = _FakeReq(_state(), query={"token": "secret-tok"})
        assert require_director(req)["role"] == "director"

    def test_valid_token_via_header(self):
        from analytics.auth import require_director
        req = _FakeReq(_state(), headers={"X-Director-Token": "secret-tok"})
        assert require_director(req)["role"] == "director"

    def test_admin_key_authorizes(self):
        from analytics.auth import require_director
        req = _FakeReq(_state(), headers={"X-API-Key": "admin-key"})
        assert require_director(req)["role"] == "admin"

    def test_admin_bearer_authorizes(self):
        from analytics.auth import require_director
        req = _FakeReq(_state(), headers={"Authorization": "Bearer admin-key"})
        assert require_director(req)["role"] == "admin"

    def test_wrong_token_denied(self):
        from analytics.auth import require_director
        req = _FakeReq(_state(), query={"token": "wrong"})
        with pytest.raises(HTTPException) as e:
            require_director(req)
        assert e.value.status_code == 401

    def test_empty_secret_fails_closed(self):
        # If DIRECTOR_TOKEN is unset, even an empty token must be rejected.
        from analytics.auth import require_director
        req = _FakeReq(_state(dt="", ak="admin-key"), query={"token": ""})
        with pytest.raises(HTTPException):
            require_director(req)

    def test_empty_secret_blank_admin_key_denied(self):
        from analytics.auth import require_director
        req = _FakeReq(_state(dt="", ak=""), headers={"X-API-Key": ""})
        with pytest.raises(HTTPException):
            require_director(req)
