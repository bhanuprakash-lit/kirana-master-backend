"""DB-backed tests for callcenter.repository (marked `db` — need TEST_DATABASE_URL).

Covers the full lifecycle: executive auth + sessions, store assignment (incl.
reassignment moving the active row), the needs-attention queue + scoring, call
logging with tags, call sheet, callbacks, stats, and the feedback digest.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from callcenter import repository as repo

pytestmark = pytest.mark.db


# Minimal schema the repository touches (self-contained, like the vision tests).
_DDL = [
    """CREATE TABLE IF NOT EXISTS kirana_oltp.store (
        store_id BIGSERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
        location VARCHAR(255), is_deleted BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.users (
        user_id BIGSERIAL PRIMARY KEY, store_id BIGINT, username VARCHAR(100) UNIQUE NOT NULL,
        full_name VARCHAR(255) DEFAULT '', role VARCHAR(50) DEFAULT 'store_owner',
        phone_number VARCHAR(20), is_deleted BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.user_sessions (
        session_id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, access_token VARCHAR(128),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.subscription (
        subscription_id BIGSERIAL PRIMARY KEY, store_id BIGINT NOT NULL UNIQUE,
        tier VARCHAR(40), is_trial BOOLEAN DEFAULT FALSE, trial_ends_at TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.orders (
        order_id BIGSERIAL PRIMARY KEY, store_id BIGINT NOT NULL,
        order_date TIMESTAMP DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.call_executive (
        executive_id BIGSERIAL PRIMARY KEY, username VARCHAR(100) UNIQUE NOT NULL,
        full_name VARCHAR(255) NOT NULL, phone VARCHAR(20), email VARCHAR(255),
        role VARCHAR(20) NOT NULL DEFAULT 'call_executive', password_salt VARCHAR(64),
        password_hash VARCHAR(128), is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.call_executive_session (
        session_id BIGSERIAL PRIMARY KEY, executive_id BIGINT NOT NULL,
        access_token VARCHAR(128) UNIQUE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        revoked_at TIMESTAMPTZ)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.store_assignment (
        assignment_id BIGSERIAL PRIMARY KEY, store_id BIGINT NOT NULL, executive_id BIGINT NOT NULL,
        assigned_by BIGINT, assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        status VARCHAR(20) NOT NULL DEFAULT 'active', priority SMALLINT NOT NULL DEFAULT 0)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS uidx_store_assignment_active "
    "ON kirana_oltp.store_assignment(store_id) WHERE status = 'active'",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.call_log (
        call_id BIGSERIAL PRIMARY KEY, store_id BIGINT NOT NULL, executive_id BIGINT NOT NULL,
        called_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), answered BOOLEAN, disposition VARCHAR(24) NOT NULL,
        app_usage_status VARCHAR(24), feedback_text TEXT, sentiment VARCHAR(12), rating SMALLINT,
        next_action VARCHAR(16), callback_at TIMESTAMPTZ, duration_sec INT, recording_url TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.call_feedback_tag (
        id BIGSERIAL PRIMARY KEY, call_id BIGINT NOT NULL, tag VARCHAR(24) NOT NULL)""",
]

_TABLES = ("call_feedback_tag", "call_log", "store_assignment", "call_executive_session",
           "call_executive", "orders", "subscription", "user_sessions", "users", "store")


@pytest.fixture
def cc_db(db_engine):
    with db_engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
        conn.execute(text(
            f"TRUNCATE TABLE {', '.join('kirana_oltp.' + t for t in _TABLES)} "
            "RESTART IDENTITY CASCADE"))
    yield db_engine
    with db_engine.begin() as conn:
        conn.execute(text(
            f"TRUNCATE TABLE {', '.join('kirana_oltp.' + t for t in _TABLES)} "
            "RESTART IDENTITY CASCADE"))


def _make_store(engine, name, *, owner_phone="9990001111", tier="trial",
                trial_days=None, orders_days_ago=None, login_days_ago=None):
    with engine.begin() as conn:
        sid = int(conn.execute(text(
            "INSERT INTO kirana_oltp.store (name) VALUES (:n) RETURNING store_id"
        ), {"n": name}).scalar())
        uid = int(conn.execute(text(
            "INSERT INTO kirana_oltp.users (store_id, username, full_name, role, phone_number) "
            "VALUES (:sid, :u, :fn, 'store_owner', :ph) RETURNING user_id"
        ), {"sid": sid, "u": f"owner_{name}", "fn": f"Owner {name}", "ph": owner_phone}).scalar())
        if tier is not None:
            te = None
            if trial_days is not None:
                te = datetime.now(timezone.utc) + timedelta(days=trial_days)
            conn.execute(text(
                "INSERT INTO kirana_oltp.subscription (store_id, tier, is_trial, trial_ends_at) "
                "VALUES (:sid, :t, :it, :te)"
            ), {"sid": sid, "t": tier, "it": tier == "trial", "te": te})
        if orders_days_ago is not None:
            conn.execute(text(
                "INSERT INTO kirana_oltp.orders (store_id, order_date) VALUES (:sid, :d)"
            ), {"sid": sid, "d": datetime.now(timezone.utc) - timedelta(days=orders_days_ago)})
        if login_days_ago is not None:
            conn.execute(text(
                "INSERT INTO kirana_oltp.user_sessions (user_id, access_token, created_at) "
                "VALUES (:uid, :tok, :d)"
            ), {"uid": uid, "tok": secrets.token_hex(16),
                "d": datetime.now(timezone.utc) - timedelta(days=login_days_ago)})
    return sid


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_executive_auth_and_session(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "ravi", "Ravi Kumar", "9998887777", None,
                               "call_executive", "secret123")
    assert ex["executive_id"] and ex["role"] == "call_executive"
    assert repo.authenticate(engine, "ravi", "wrong") is None
    auth = repo.authenticate(engine, "ravi", "secret123")
    assert auth["executive_id"] == ex["executive_id"]

    token = repo.create_session(engine, ex["executive_id"])
    who = repo.executive_by_token(engine, token)
    assert who["executive_id"] == ex["executive_id"] and who["role"] == "call_executive"
    repo.revoke_session(engine, token)
    assert repo.executive_by_token(engine, token) is None


def test_inactive_executive_cannot_auth(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "sita", "Sita", None, None, "call_executive", "pw123456")
    repo.set_executive_active(engine, ex["executive_id"], False)
    assert repo.authenticate(engine, "sita", "pw123456") is None


# ── Assignments ───────────────────────────────────────────────────────────────

def test_assignment_and_reassignment_moves_active_row(cc_db):
    engine = cc_db
    a = repo.create_executive(engine, "a", "Exec A", None, None, "call_executive", "pw123456")
    b = repo.create_executive(engine, "b", "Exec B", None, None, "call_executive", "pw123456")
    s1 = _make_store(engine, "s1")
    s2 = _make_store(engine, "s2")

    res = repo.assign_stores(engine, a["executive_id"], [s1, s2, 999999], assigned_by=None)
    assert res == {"assigned": 2, "skipped": 1}   # 999999 doesn't exist
    assert repo.is_assigned(engine, a["executive_id"], s1)

    # Reassign s1 to B → A keeps only s2, exactly one active row for s1.
    repo.assign_stores(engine, b["executive_id"], [s1], assigned_by=None)
    assert not repo.is_assigned(engine, a["executive_id"], s1)
    assert repo.is_assigned(engine, b["executive_id"], s1)
    with engine.connect() as conn:
        active = conn.execute(text(
            "SELECT COUNT(*) FROM kirana_oltp.store_assignment "
            "WHERE store_id = :sid AND status = 'active'"
        ), {"sid": s1}).scalar()
    assert active == 1

    load = {r["executive_id"]: r["active_stores"] for r in repo.assignment_load(engine)}
    assert load[a["executive_id"]] == 1 and load[b["executive_id"]] == 1


def test_unassign(cc_db):
    engine = cc_db
    a = repo.create_executive(engine, "a", "Exec A", None, None, "call_executive", "pw123456")
    s1 = _make_store(engine, "s1")
    repo.assign_stores(engine, a["executive_id"], [s1], assigned_by=None)
    assert repo.unassign_store(engine, s1) is True
    assert not repo.is_assigned(engine, a["executive_id"], s1)
    assert repo.unassign_store(engine, s1) is False   # already gone


# ── Queue + scoring ───────────────────────────────────────────────────────────

def test_queue_ranks_by_need_and_excludes_dnc(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "q", "Queue Exec", None, None, "call_executive", "pw123456")
    # Healthy store: active tier, recent order + login → low score.
    healthy = _make_store(engine, "healthy", tier="pro", orders_days_ago=1, login_days_ago=1)
    # Struggling store: trial ending, no orders, no login → high score.
    struggling = _make_store(engine, "struggling", tier="trial", trial_days=3)
    # A store we'll mark do-not-call → excluded from queue.
    dnc = _make_store(engine, "dnc", tier="basic", orders_days_ago=1, login_days_ago=1)
    for s in (healthy, struggling, dnc):
        repo.assign_stores(engine, ex["executive_id"], [s], assigned_by=None)
    repo.log_call(engine, ex["executive_id"], dnc, {"disposition": "answered",
                  "next_action": "do_not_call"})

    q = repo.get_queue(engine, ex["executive_id"])
    ids = [r["store_id"] for r in q]
    assert dnc not in ids                      # DNC excluded
    assert ids[0] == struggling                # highest need first
    assert q[0]["score"] > q[-1]["score"]
    assert "Trial ends in" in q[0]["reason"]
    hq = next(r for r in q if r["store_id"] == healthy)
    assert hq["never_called"] is True


def test_callback_due_tops_the_queue(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "c", "CB Exec", None, None, "call_executive", "pw123456")
    healthy = _make_store(engine, "healthy", tier="pro", orders_days_ago=1, login_days_ago=1)
    other = _make_store(engine, "other", tier="trial", trial_days=2)
    repo.assign_stores(engine, ex["executive_id"], [healthy, other], assigned_by=None)
    # Overdue callback on the otherwise-healthy store.
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    repo.log_call(engine, ex["executive_id"], healthy,
                  {"disposition": "no_answer", "next_action": "callback", "callback_at": past})

    q = repo.get_queue(engine, ex["executive_id"])
    assert q[0]["store_id"] == healthy and q[0]["callback_due"] is True
    assert "Callback due" in q[0]["reason"]


# ── Call logging, sheet, callbacks, stats ─────────────────────────────────────

def test_log_call_with_tags_and_sheet_history(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "e", "Exec", None, None, "call_executive", "pw123456")
    s = _make_store(engine, "s", tier="trial", trial_days=5)
    repo.assign_stores(engine, ex["executive_id"], [s], assigned_by=None)

    out = repo.log_call(engine, ex["executive_id"], s, {
        "disposition": "answered", "app_usage_status": "needs_training",
        "feedback_text": "Wants a POS demo", "sentiment": "positive", "rating": 4,
        "next_action": "callback", "callback_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "tags": ["training", "feature_request"],
    })
    assert out["call_id"]

    sheet = repo.get_call_sheet(engine, ex["executive_id"], s)
    assert sheet["store_name"] == "s" and sheet["phone_number"] == "9990001111"
    assert "financial" not in sheet and "revenue" not in sheet  # focused view, no financials
    assert len(sheet["history"]) == 1
    h = sheet["history"][0]
    assert h["disposition"] == "answered" and h["rating"] == 4
    assert set(h["tags"]) == {"training", "feature_request"}
    assert h["executive_name"] == "Exec"


def test_callbacks_and_supersede(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "e", "Exec", None, None, "call_executive", "pw123456")
    s = _make_store(engine, "s")
    repo.assign_stores(engine, ex["executive_id"], [s], assigned_by=None)
    repo.log_call(engine, ex["executive_id"], s, {"disposition": "no_answer",
                  "next_action": "callback",
                  "callback_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    cbs = repo.get_callbacks(engine, ex["executive_id"])
    assert len(cbs) == 1 and cbs[0]["overdue"] is True and cbs[0]["store_id"] == s

    # A newer call (done) supersedes the callback → drops off the list.
    repo.log_call(engine, ex["executive_id"], s, {"disposition": "answered", "next_action": "done"})
    assert repo.get_callbacks(engine, ex["executive_id"]) == []


def test_stats(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "e", "Exec", None, None, "call_executive", "pw123456")
    s1 = _make_store(engine, "s1")
    s2 = _make_store(engine, "s2")
    repo.assign_stores(engine, ex["executive_id"], [s1, s2], assigned_by=None)
    repo.log_call(engine, ex["executive_id"], s1, {"disposition": "answered",
                  "app_usage_status": "using_active", "rating": 5})
    repo.log_call(engine, ex["executive_id"], s2, {"disposition": "no_answer"})

    st = repo.get_stats(engine, ex["executive_id"])
    assert st["assigned_stores"] == 2
    assert st["calls_today"] == 2 and st["calls_30d"] == 2
    assert st["answered_30d"] == 1 and st["connect_rate"] == 0.5
    assert st["using_30d"] == 1 and st["avg_rating"] == 5.0


def test_feedback_digest_filters(cc_db):
    engine = cc_db
    ex = repo.create_executive(engine, "e", "Exec", None, None, "call_executive", "pw123456")
    s = _make_store(engine, "s")
    repo.assign_stores(engine, ex["executive_id"], [s], assigned_by=None)
    repo.log_call(engine, ex["executive_id"], s, {"disposition": "answered",
                  "feedback_text": "App is buggy", "sentiment": "negative", "tags": ["bug"]})
    repo.log_call(engine, ex["executive_id"], s, {"disposition": "answered",
                  "feedback_text": "Loves it", "sentiment": "positive", "tags": ["happy"]})

    allfb = repo.list_feedback(engine)
    assert len(allfb) == 2
    bugs = repo.list_feedback(engine, tag="bug")
    assert len(bugs) == 1 and bugs[0]["feedback_text"] == "App is buggy"
    neg = repo.list_feedback(engine, sentiment="negative")
    assert len(neg) == 1 and neg[0]["tags"] == ["bug"]
