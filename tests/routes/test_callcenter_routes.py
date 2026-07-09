"""Route-layer auth/validation guards for /kirana/callcenter/* (no DB needed).
Full behaviour is exercised in tests/db/test_callcenter_repository.py and the
end-to-end HTTP check."""
from __future__ import annotations

import pytest


@pytest.fixture
def cc_app(test_app):
    from callcenter.routes import router as cc_router
    test_app.include_router(cc_router)
    return test_app


@pytest.fixture
def cc_client(cc_app):
    from fastapi.testclient import TestClient
    with TestClient(cc_app) as c:
        yield c


class TestGuards:
    def test_queue_requires_auth(self, cc_client):
        assert cc_client.get("/kirana/callcenter/queue").status_code == 401

    def test_me_requires_auth(self, cc_client):
        assert cc_client.get("/kirana/callcenter/me").status_code == 401

    def test_executives_requires_auth(self, cc_client):
        assert cc_client.get("/kirana/callcenter/executives").status_code == 401

    def test_login_needs_body(self, cc_client):
        assert cc_client.post("/kirana/callcenter/login").status_code == 422

    # Bad-bearer rejection is exercised against a real engine in the HTTP e2e check
    # (the no-DB test app can't resolve a token), so it isn't repeated here.
