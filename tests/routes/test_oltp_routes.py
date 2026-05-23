"""Smoke tests for /oltp/{table} — the allowlist gatekeeper.

The OLTP repository touches the DB, so we monkeypatch it to avoid a real
connection. The interesting behaviour at the route layer is the table
allowlist and the auth dependency.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def oltp_app(test_app, monkeypatch):
    """`test_app` with the OLTP router mounted and the repository stubbed."""
    from oltp import routes as oltp_routes

    class FakeOltpRepo:
        def __init__(self, engine):
            pass

        def list_rows(self, table, user, filters, limit, offset):
            return {
                "rows": [],
                "table": table,
                "filters": filters,
                "user_role": user.get("role"),
            }

        def schema_overview(self):
            return [{"name": "customer"}, {"name": "orders"}]

    monkeypatch.setattr(oltp_routes, "OltpRepository", FakeOltpRepo)
    test_app.include_router(oltp_routes.router)
    return test_app


@pytest.fixture
def oltp_client(oltp_app):
    from fastapi.testclient import TestClient

    with TestClient(oltp_app) as c:
        yield c


class TestTableAllowlist:
    def test_known_table_works(self, oltp_client):
        res = oltp_client.get(
            "/oltp/customer",
            headers={"X-API-Key": "test-admin-key"},
        )
        assert res.status_code == 200
        assert res.json()["table"] == "customer"

    def test_unknown_table_returns_404(self, oltp_client):
        res = oltp_client.get(
            "/oltp/secret_admin_table",
            headers={"X-API-Key": "test-admin-key"},
        )
        assert res.status_code == 404

    def test_auth_required(self, oltp_client):
        res = oltp_client.get("/oltp/customer")
        assert res.status_code == 401

    def test_query_params_become_filters(self, oltp_client):
        res = oltp_client.get(
            "/oltp/customer?store_id=10&phone=999",
            headers={"X-API-Key": "test-admin-key"},
        )
        body = res.json()
        # `limit` and `offset` should be stripped by _query_filters.
        assert body["filters"] == {"store_id": "10", "phone": "999"}


class TestSchemaEndpoint:
    def test_schema_overview(self, oltp_client):
        res = oltp_client.get(
            "/oltp/schema",
            headers={"X-API-Key": "test-admin-key"},
        )
        assert res.status_code == 200
        assert res.json() == {
            "schema": "kirana_oltp",
            "tables": [{"name": "customer"}, {"name": "orders"}],
        }
