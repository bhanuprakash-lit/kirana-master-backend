"""Smoke tests for /kirana routes via the FastAPI TestClient.

These tests exercise the _auth dependency, the health endpoint, and
representative read endpoints. The real DB is not touched — every backend
collaborator is a mock attached to `app.state`.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def kirana_app(test_app):
    """`test_app` with the kirana router mounted."""
    from kirana.routes import router as kirana_router

    test_app.include_router(kirana_router)
    return test_app


@pytest.fixture
def kirana_client(kirana_app):
    from fastapi.testclient import TestClient

    with TestClient(kirana_app) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_service_payload(self, kirana_client, fake_kirana_service):
        fake_kirana_service.health_payload = {"status": "ok", "ml_rows": 42}
        res = kirana_client.get("/kirana/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok", "ml_rows": 42}


# ── Auth middleware ──────────────────────────────────────────────────────────


class TestAuthMiddleware:
    """The `_auth` dependency is duplicated across modules. Verify the
    canonical implementation in kirana/routes.py covers all three paths:
    admin via X-API-Key, user via Bearer, and the 401 fallback.
    """

    def test_missing_credentials_returns_401(self, kirana_client):
        # `/kirana/auth/me` requires _auth.
        res = kirana_client.get("/kirana/auth/me")
        assert res.status_code == 401

    def test_admin_api_key_grants_admin_role(self, kirana_client):
        res = kirana_client.get(
            "/kirana/auth/me",
            headers={"X-API-Key": "test-admin-key"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body == {"role": "admin", "user_id": None, "store_id": None}

    def test_wrong_api_key_returns_401(self, kirana_client):
        res = kirana_client.get(
            "/kirana/auth/me",
            headers={"X-API-Key": "completely-wrong"},
        )
        assert res.status_code == 401

    def test_bearer_token_resolves_to_user(self, kirana_client, fake_kirana_service):
        fake_kirana_service.tokens["good-token"] = {
            "user_id": 7,
            "username": "ramesh",
            "role": "store_owner",
            "store_id": 10,
        }
        res = kirana_client.get(
            "/kirana/auth/me",
            headers={"Authorization": "Bearer good-token"},
        )
        assert res.status_code == 200
        assert res.json()["user_id"] == 7
        assert res.json()["store_id"] == 10

    def test_unknown_bearer_returns_401(self, kirana_client):
        res = kirana_client.get(
            "/kirana/auth/me",
            headers={"Authorization": "Bearer ghost"},
        )
        assert res.status_code == 401

    def test_admin_key_via_bearer_header_works_for_kpis_route(self, kirana_client):
        # `/kirana/kpis/*` accepts the API key as either header — verify
        # the variant that places it in the Authorization header.
        # (Not part of the _auth in kirana/routes.py but worth keeping
        # honest — the kpis router's _auth allows it.)
        from kpis.routes import router as kpi_router
        # Mount lazily for this one assertion.
        kirana_client.app.include_router(kpi_router)
        res = kirana_client.get(
            "/kirana/kpis/registry",
            headers={"Authorization": "Bearer test-admin-key"},
        )
        # 200 or 5xx is fine — we just want to assert we got past auth.
        assert res.status_code != 401


# ── Login route ──────────────────────────────────────────────────────────────


class TestLoginRoute:
    def test_login_delegates_to_service(self, kirana_client, fake_kirana_service):
        """The route should call KiranaService.login and forward its return value."""

        def fake_login(body, telemetry=None):
            assert body.username == "ramesh"
            assert body.password == "secret"
            return {"access_token": "abc.def", "user": {"user_id": 1, "username": "ramesh"}}

        fake_kirana_service.login = fake_login

        res = kirana_client.post(
            "/kirana/auth/login",
            json={"username": "ramesh", "password": "secret"},
        )
        assert res.status_code == 200
        assert res.json()["access_token"] == "abc.def"

    def test_invalid_credentials_returns_401(self, kirana_client, fake_kirana_service):
        """Service raises ValueError on bad credentials -> route returns 401."""

        def fake_login(body, telemetry=None):
            raise ValueError("bad creds")

        fake_kirana_service.login = fake_login

        res = kirana_client.post(
            "/kirana/auth/login",
            json={"username": "ramesh", "password": "wrong"},
        )
        assert res.status_code == 401

    def test_login_body_must_be_valid_login_request(self, kirana_client):
        # 422 because Pydantic rejects the body before _auth or the handler runs.
        res = kirana_client.post("/kirana/auth/login", json={})
        assert res.status_code == 422


# ── Username availability ────────────────────────────────────────────────────


class TestUsernameCheck:
    def test_available(self, kirana_client, fake_kirana_service):
        fake_kirana_service.check_username_available = lambda u: True
        res = kirana_client.get("/kirana/auth/check-username/newuser")
        assert res.status_code == 200
        assert res.json() == {"available": True, "username": "newuser"}

    def test_taken(self, kirana_client, fake_kirana_service):
        fake_kirana_service.check_username_available = lambda u: False
        res = kirana_client.get("/kirana/auth/check-username/taken")
        assert res.status_code == 200
        assert res.json()["available"] is False
