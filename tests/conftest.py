"""Shared pytest fixtures for the Kirana backend test suite.

Three classes of fixtures live here:

1. **App / route fixtures** — build a minimal FastAPI app with the routers
   mounted and `app.state` populated with mocks. No real DB or external
   services. Use for `tests/routes/`.

2. **DB engine fixture** (`db_engine`) — creates a real SQLAlchemy engine
   against ``TEST_DATABASE_URL`` if set, and bootstraps just enough schema
   to run the repository tests. Tests using this fixture must be marked
   ``@pytest.mark.db`` so they are skipped on machines without a test DB.

3. **Mock fixtures** — drop-in fakes for ``KiranaService`` and ``Settings``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── Settings stub ─────────────────────────────────────────────────────────────


@pytest.fixture
def test_settings():
    """A frozen Settings dataclass populated with safe defaults for tests."""
    from config import Settings

    return Settings(
        host="127.0.0.1",
        port=9000,
        debug=True,
        db_url="postgresql+psycopg2://test:test@localhost:5432/test",
        kirana_api_key="test-admin-key",
        ml_results_dir="/tmp/ml_results",
        ml_artifacts_dir="/tmp/ml_artifacts",
        pos_secret_key="test-pos-secret",
        pos_algorithm="HS256",
        pos_token_expire_minutes=60,
        whatsapp_api_base_url="https://graph.facebook.com/v25.0",
        whatsapp_access_token="",
        whatsapp_phone_number_id="",
        whatsapp_business_account_id="",
        whatsapp_verify_token="test-verify-token",
        gemini_api_key="",
        mistral_api_key="",
        mistral_model="mistral-small-latest",
        razorpay_key_id="",
        razorpay_key_secret="",
        trial_days=14,
        basic_price_inr=200,
        pro_price_inr=500,
        google_play_package_name="",
        google_play_credentials_json="",
    )


# ── Mock KiranaService ────────────────────────────────────────────────────────


class FakeKiranaService:
    """Drop-in replacement for ``KiranaService`` in route tests.

    Routes call this via ``request.app.state.kirana_service`` — set the
    attributes / return values you need per test.
    """

    def __init__(self):
        # token -> user dict
        self.tokens: dict[str, dict] = {}
        self.health_payload = {"status": "ok", "ml_rows": 0}
        # captured calls for assertions
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def user_by_token(self, token: str):
        self._record("user_by_token", token)
        return self.tokens.get(token)

    def health(self):
        return self.health_payload

    # Add more methods on demand from individual tests.


@pytest.fixture
def fake_kirana_service():
    return FakeKiranaService()


# ── Minimal FastAPI app for route tests ───────────────────────────────────────


@pytest.fixture
def test_app(test_settings, fake_kirana_service):
    """A FastAPI app with the kirana router mounted and mocked app.state.

    Does NOT run the real lifespan — no DB connection, no scheduler, no
    Firebase init. Mount any additional routers you need inside the test.
    """
    from fastapi import FastAPI

    app = FastAPI()
    app.state.settings = test_settings
    app.state.kirana_service = fake_kirana_service
    # Engine is set to None so tests catch any accidental DB access.
    app.state.engine = None
    return app


@pytest.fixture
def client(test_app):
    """A FastAPI TestClient wrapping ``test_app``.

    Tests that need extra routers (e.g. /pos) should mount them on
    ``test_app`` before requesting this fixture.
    """
    from fastapi.testclient import TestClient

    with TestClient(test_app) as c:
        yield c


# ── Real-DB fixture for repository tests ──────────────────────────────────────


def _test_db_url() -> str | None:
    """Return the test DB URL or None if not configured."""
    return os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db_engine():
    """A SQLAlchemy engine against ``TEST_DATABASE_URL``.

    Skips the test if not configured. Creates the ``kirana_oltp`` schema
    and a minimal subset of tables required by the repository tests; the
    full migration script lives in ``db_generation/`` and is too heavy to
    run for every CI invocation.
    """
    url = _test_db_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-backed tests")

    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True)

    # Verify connectivity before doing anything else.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Cannot reach TEST_DATABASE_URL: {exc}")

    # Create the minimal schema the tests actually exercise — with auth
    # columns inlined so KiranaRepository's password / session helpers can
    # run without first executing the full _ensure_schema bootstrap (which
    # touches many tables the test suite has no need to seed).
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS kirana_oltp"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kirana_oltp.store (
                store_id    BIGSERIAL PRIMARY KEY,
                name        VARCHAR(255) NOT NULL,
                location    VARCHAR(255),
                region      VARCHAR(100),
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                is_deleted  BOOLEAN DEFAULT FALSE
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kirana_oltp.users (
                user_id              BIGSERIAL PRIMARY KEY,
                store_id             BIGINT,
                username             VARCHAR(100) UNIQUE NOT NULL,
                email                VARCHAR(255),
                full_name            VARCHAR(255) NOT NULL DEFAULT '',
                role                 VARCHAR(50)  DEFAULT 'store_owner',
                password_salt        VARCHAR(64),
                password_hash        VARCHAR(128),
                password_changed_at  TIMESTAMPTZ,
                is_active            BOOLEAN NOT NULL DEFAULT TRUE,
                is_deleted           BOOLEAN DEFAULT FALSE,
                fcm_token            VARCHAR(255),
                phone_number         VARCHAR(20),
                firebase_uid         VARCHAR(128),
                created_at           TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kirana_oltp.user_sessions (
                session_id   BIGSERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                access_token VARCHAR(128) UNIQUE NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked_at   TIMESTAMPTZ,
                login_method VARCHAR(20) DEFAULT 'password'
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kirana_oltp.customer (
                customer_id BIGSERIAL PRIMARY KEY,
                store_id    BIGINT NOT NULL,
                name        VARCHAR(255) NOT NULL,
                phone       VARCHAR(20),
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """))

    yield engine

    engine.dispose()


@pytest.fixture
def clean_db(db_engine):
    """Truncate the test tables so each test starts from a known state."""
    from sqlalchemy import text

    def _truncate():
        with db_engine.begin() as conn:
            conn.execute(text(
                "TRUNCATE TABLE kirana_oltp.user_sessions, "
                "kirana_oltp.customer, kirana_oltp.users, kirana_oltp.store "
                "RESTART IDENTITY CASCADE"
            ))

    _truncate()
    yield db_engine
    _truncate()


@pytest.fixture
def kirana_repo_no_bootstrap(monkeypatch):
    """Construct KiranaRepository without running _ensure_schema.

    The bootstrap touches many tables the test fixture does not seed. The
    tests here only exercise auth/session helpers, so we mark the schema
    as already-initialized at the module level.
    """
    import kirana.repository as repo_mod

    monkeypatch.setattr(repo_mod, "_schema_initialized", True, raising=False)
    yield
