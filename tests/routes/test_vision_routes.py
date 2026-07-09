"""Route-layer tests for /kirana/vision/* — auth + input validation.

These cover the paths that don't need a DB (auth, session_type, image-count
guards). Full persistence is exercised in tests/db/test_vision_repository.py.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def vision_app(test_app, fake_kirana_service):
    from vision.routes import router as vision_router

    test_app.include_router(vision_router)
    # A logged-in store owner.
    fake_kirana_service.tokens["t-owner"] = {
        "user_id": 7, "username": "ramesh", "role": "store_owner", "store_id": 10,
    }
    # A user with no store context.
    fake_kirana_service.tokens["t-nostore"] = {
        "user_id": 8, "username": "nostore", "role": "store_owner", "store_id": None,
    }
    return test_app


@pytest.fixture
def vision_client(vision_app):
    from fastapi.testclient import TestClient

    with TestClient(vision_app) as c:
        yield c


_OWNER = {"Authorization": "Bearer t-owner"}


def _img(name="a.jpg"):
    return ("files", (name, b"\xff\xd8\xff fake jpeg bytes", "image/jpeg"))


class TestAuth:
    def test_sessions_requires_auth(self, vision_client):
        assert vision_client.get("/kirana/vision/sessions").status_code == 401

    def test_unknown_token_rejected(self, vision_client):
        res = vision_client.get(
            "/kirana/vision/sessions", headers={"Authorization": "Bearer ghost"})
        assert res.status_code == 401

    def test_account_without_store_is_rejected(self, vision_client):
        res = vision_client.get(
            "/kirana/vision/sessions", headers={"Authorization": "Bearer t-nostore"})
        assert res.status_code == 400

    def test_analytics_requires_auth(self, vision_client):
        assert vision_client.get("/kirana/vision/analytics").status_code == 401


class TestAnalyticsValidation:
    def test_days_out_of_range_rejected(self, vision_client):
        assert vision_client.get(
            "/kirana/vision/analytics?days=0", headers=_OWNER).status_code == 422
        assert vision_client.get(
            "/kirana/vision/analytics?days=400", headers=_OWNER).status_code == 422


class TestAnalyzeValidation:
    def test_invalid_session_type_rejected(self, vision_client):
        res = vision_client.post(
            "/kirana/vision/shelf/analyze?session_type=noon",
            files=[_img(), _img("b.jpg"), _img("c.jpg")],
            headers=_OWNER,
        )
        assert res.status_code == 400

    def test_too_few_images_rejected(self, vision_client):
        res = vision_client.post(
            "/kirana/vision/shelf/analyze?session_type=morning",
            files=[_img()],  # only 1, min is 3
            headers=_OWNER,
        )
        assert res.status_code == 400
        assert "between" in res.json()["detail"].lower()

    def test_too_many_images_rejected(self, vision_client):
        res = vision_client.post(
            "/kirana/vision/shelf/analyze?session_type=morning",
            files=[_img(f"{i}.jpg") for i in range(11)],  # 11, max is 10
            headers=_OWNER,
        )
        assert res.status_code == 400

    def test_analyze_requires_auth(self, vision_client):
        res = vision_client.post(
            "/kirana/vision/shelf/analyze?session_type=morning",
            files=[_img(), _img("b.jpg"), _img("c.jpg")],
        )
        assert res.status_code == 401
