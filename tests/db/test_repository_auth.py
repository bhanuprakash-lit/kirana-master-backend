"""DB-backed tests for KiranaRepository auth + session helpers.

These tests require a real PostgreSQL instance pointed at by
TEST_DATABASE_URL. Locally:

    docker run --rm -p 5433:5432 -e POSTGRES_PASSWORD=test postgres:16
    export TEST_DATABASE_URL=postgresql+psycopg2://postgres:test@localhost:5433/postgres
    pytest tests/db

CI provides a postgres service container; see .github/workflows/ci.yml.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


@pytest.fixture
def repo(clean_db, kirana_repo_no_bootstrap):
    """A KiranaRepository against the clean test DB, with bootstrap skipped."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    return KiranaRepository(clean_db)


def _seed_store_and_user(engine, *, username="ramesh", password=None, salt="abc"):
    """Insert a store and a user; returns (store_id, user_id)."""
    import hashlib

    pw_hash = hashlib.sha256((salt + password).encode()).hexdigest() if password else None
    with engine.begin() as conn:
        store_id = conn.execute(text(
            "INSERT INTO kirana_oltp.store (name) VALUES ('Test Store') RETURNING store_id"
        )).scalar()
        user_id = conn.execute(text("""
            INSERT INTO kirana_oltp.users
                (store_id, username, full_name, role, password_salt, password_hash, is_active)
            VALUES
                (:sid, :u, 'Test', 'store_owner', :salt, :ph, TRUE)
            RETURNING user_id
        """), {
            "sid": store_id,
            "u": username,
            "salt": salt if password else None,
            "ph": pw_hash,
        }).scalar()
    return store_id, user_id


# ── Username availability ────────────────────────────────────────────────────


class TestUsernameAvailability:
    def test_free_when_empty_table(self, repo):
        assert repo.check_username_available("newperson") is True

    def test_taken_after_insert(self, repo, clean_db):
        _seed_store_and_user(clean_db, username="ramesh")
        assert repo.check_username_available("ramesh") is False

    def test_case_insensitive(self, repo, clean_db):
        _seed_store_and_user(clean_db, username="Ramesh")
        assert repo.check_username_available("RAMESH") is False
        assert repo.check_username_available("ramesh") is False


# ── Authentication ───────────────────────────────────────────────────────────


class TestAuthenticateUser:
    def test_correct_password_returns_user(self, repo, clean_db):
        sid, uid = _seed_store_and_user(clean_db, password="secret")
        user = repo.authenticate_user("ramesh", "secret")
        assert user is not None
        assert user["user_id"] == uid
        assert user["store_id"] == sid
        assert user["role"] == "store_owner"

    def test_wrong_password_returns_none(self, repo, clean_db):
        _seed_store_and_user(clean_db, password="secret")
        assert repo.authenticate_user("ramesh", "wrong") is None

    def test_unknown_user_returns_none(self, repo):
        assert repo.authenticate_user("ghost", "anything") is None

    def test_inactive_user_cannot_authenticate(self, repo, clean_db):
        _seed_store_and_user(clean_db, password="secret")
        with clean_db.begin() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.users SET is_active = FALSE WHERE username = 'ramesh'"
            ))
        assert repo.authenticate_user("ramesh", "secret") is None

    def test_soft_deleted_user_cannot_authenticate(self, repo, clean_db):
        _seed_store_and_user(clean_db, password="secret")
        with clean_db.begin() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.users SET is_deleted = TRUE WHERE username = 'ramesh'"
            ))
        assert repo.authenticate_user("ramesh", "secret") is None


# ── Session lifecycle ────────────────────────────────────────────────────────


class TestSessions:
    def test_session_round_trip(self, repo, clean_db):
        sid, uid = _seed_store_and_user(clean_db, password="secret")

        token = repo.create_session(uid, login_method="password")
        assert len(token) == 64  # 32 bytes hex
        user = repo.get_user_by_token(token)

        assert user is not None
        assert user["user_id"] == uid
        assert user["username"] == "ramesh"
        assert user["store_id"] == sid

    def test_unknown_token_returns_none(self, repo):
        assert repo.get_user_by_token("not-a-real-token") is None

    def test_revoked_session_does_not_resolve(self, repo, clean_db):
        _, uid = _seed_store_and_user(clean_db, password="secret")
        token = repo.create_session(uid)

        with clean_db.begin() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.user_sessions SET revoked_at = NOW() WHERE access_token = :t"
            ), {"t": token})

        assert repo.get_user_by_token(token) is None

    def test_session_for_deleted_user_does_not_resolve(self, repo, clean_db):
        _, uid = _seed_store_and_user(clean_db, password="secret")
        token = repo.create_session(uid)

        with clean_db.begin() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.users SET is_deleted = TRUE WHERE user_id = :uid"
            ), {"uid": uid})

        assert repo.get_user_by_token(token) is None

    def test_each_call_returns_a_unique_token(self, repo, clean_db):
        _, uid = _seed_store_and_user(clean_db, password="secret")
        a = repo.create_session(uid)
        b = repo.create_session(uid)
        assert a != b


# ── Phone-OTP auth ───────────────────────────────────────────────────────────


class TestAuthenticateByPhone:
    def test_phone_lookup_returns_user(self, repo, clean_db):
        _, uid = _seed_store_and_user(clean_db, username="phoneuser", password=None)
        with clean_db.begin() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.users SET phone_number = :p, firebase_uid = :f WHERE user_id = :uid"
            ), {"p": "+919999999999", "f": "uid-abc", "uid": uid})

        # Phone match works
        user = repo.authenticate_by_phone("+919999999999")
        assert user is not None
        assert user["user_id"] == uid

        # Firebase UID fallback works (different phone)
        user = repo.authenticate_by_phone("unknown", firebase_uid="uid-abc")
        assert user is not None
        assert user["user_id"] == uid

    def test_unknown_phone_returns_none(self, repo):
        assert repo.authenticate_by_phone("+910000000000") is None
