"""Route-layer tests for multi-store + GST endpoints — auth + input validation.

These cover the paths that short-circuit before the repository (and thus don't
need a DB): missing auth, missing user context, missing required fields. Full
persistence is covered by DB-backed tests when TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def stores_app(test_app, fake_kirana_service):
    from kirana.routers.stores import router as stores_router
    from kirana.routers.tax import router as tax_router

    test_app.include_router(stores_router)
    test_app.include_router(tax_router)
    # A logged-in owner with an active store.
    fake_kirana_service.tokens["t-owner"] = {
        "user_id": 7, "username": "ramesh", "role": "store_owner", "store_id": 10,
    }
    # Authenticated but no user_id (e.g. admin API context) — can't manage personal stores.
    fake_kirana_service.tokens["t-nouid"] = {
        "user_id": None, "username": None, "role": "store_owner", "store_id": None,
    }
    # Owner with no active store selected.
    fake_kirana_service.tokens["t-nostore"] = {
        "user_id": 9, "username": "nostore", "role": "store_owner", "store_id": None,
    }
    return test_app


@pytest.fixture
def client(stores_app):
    with TestClient(stores_app) as c:
        yield c


_OWNER = {"Authorization": "Bearer t-owner"}
_NOUID = {"Authorization": "Bearer t-nouid"}
_NOSTORE = {"Authorization": "Bearer t-nostore"}


class TestMyStores:
    def test_requires_auth(self, client):
        assert client.get("/kirana/my-stores").status_code == 401

    def test_requires_user_context(self, client):
        # No user_id → 403 (can't list personal stores)
        assert client.get("/kirana/my-stores", headers=_NOUID).status_code == 403


class TestAddStore:
    def test_requires_auth(self, client):
        assert client.post("/kirana/stores/add", json={"store_name": "X"}).status_code == 401

    def test_requires_user_context(self, client):
        r = client.post("/kirana/stores/add", json={"store_name": "X"}, headers=_NOUID)
        assert r.status_code == 403

    def test_requires_store_name(self, client):
        # Owner present, but no store_name → 400 (validated before the repo)
        r = client.post("/kirana/stores/add", json={}, headers=_OWNER)
        assert r.status_code == 400


class TestSwitchStore:
    def test_requires_auth(self, client):
        assert client.post("/kirana/stores/switch", json={"store_id": 1}).status_code == 401

    def test_requires_user_context(self, client):
        r = client.post("/kirana/stores/switch", json={"store_id": 1}, headers=_NOUID)
        assert r.status_code == 403

    def test_requires_store_id(self, client):
        r = client.post("/kirana/stores/switch", json={}, headers=_OWNER)
        assert r.status_code == 400


class TestGstSummary:
    _PARAMS = "?date_from=2026-06-01&date_to=2026-06-30"

    def test_requires_auth(self, client):
        assert client.get(f"/kirana/tax/gst-summary{self._PARAMS}").status_code == 401

    def test_requires_store_context(self, client):
        # Authenticated owner but no active store → 403 (before the repo)
        r = client.get(f"/kirana/tax/gst-summary{self._PARAMS}", headers=_NOSTORE)
        assert r.status_code == 403
