"""JWT encode/decode round-trip for pos/auth.py."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi import HTTPException

from pos.auth import create_access_token, decode_token


@pytest.fixture(autouse=True)
def _override_settings(monkeypatch, test_settings):
    """Force pos/auth to use the deterministic test settings.

    ``get_settings`` is lru_cached so we monkeypatch it module-by-module.
    """
    monkeypatch.setattr("pos.auth.get_settings", lambda: test_settings)


def test_token_round_trips_username_and_store_id():
    token = create_access_token({"sub": "ramesh", "store_id": 7})
    payload = decode_token(token)
    assert payload["sub"] == "ramesh"
    assert payload["store_id"] == 7
    assert "exp" in payload


def test_token_carries_expiry_in_the_future():
    token = create_access_token({"sub": "x"})
    payload = decode_token(token)
    assert payload["exp"] > time.time()


def test_custom_expiry_is_honoured():
    token = create_access_token({"sub": "x"}, expires_delta=timedelta(seconds=60))
    payload = decode_token(token)
    # ~60 seconds from now, within a 5s tolerance for slow CI runners.
    assert abs(payload["exp"] - (time.time() + 60)) < 5


def test_tampered_token_raises_401():
    token = create_access_token({"sub": "x"})
    # Flip a character in the signature segment.
    head, _, tail = token.rsplit(".", 2)[0], ".", "x" * 8
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(HTTPException) as exc:
        decode_token(bad)
    assert exc.value.status_code == 401


def test_missing_sub_claim_raises_401():
    # A token built without "sub" should be rejected.
    from jose import jwt
    from config import get_settings

    s = get_settings()
    bad = jwt.encode({"foo": "bar", "exp": time.time() + 60}, s.pos_secret_key, algorithm=s.pos_algorithm)
    with pytest.raises(HTTPException) as exc:
        decode_token(bad)
    assert exc.value.status_code == 401


def test_expired_token_raises_401():
    token = create_access_token({"sub": "x"}, expires_delta=timedelta(seconds=-1))
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401
