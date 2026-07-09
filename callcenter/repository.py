"""Postgres persistence for the call center (kirana_oltp.call_executive /
call_executive_session / store_assignment / call_log / call_feedback_tag).

Executives are their OWN identities (not app users): they log into the admin panel
with a username+password → bearer session, so every call is attributed to a person.
The store-facing queries reuse signals already collected elsewhere (user_sessions,
orders, subscription) to rank which stores need a call.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from sqlalchemy import text

VALID_DISPOSITIONS = {
    "answered", "no_answer", "busy", "switched_off", "wrong_number", "invalid_number",
}
VALID_USAGE = {
    "using_active", "using_rare", "stopped", "never_started", "needs_training",
}
VALID_NEXT_ACTION = {"callback", "escalate", "done", "do_not_call"}
VALID_SENTIMENT = {"positive", "neutral", "negative"}
VALID_TAGS = {
    "bug", "feature_request", "pricing", "training", "happy", "churn_risk",
}
VALID_ROLES = {"call_executive", "call_manager"}


def _hash(password: str, salt: str) -> str:
    # Same scheme as kirana users (sha256(salt + password)).
    return hashlib.sha256((salt + password).encode()).hexdigest()


# ── Auth ──────────────────────────────────────────────────────────────────────

def authenticate(engine, username: str, password: str) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT executive_id, username, full_name, role, password_salt, password_hash
            FROM kirana_oltp.call_executive
            WHERE username = :u AND is_active = TRUE
        """), {"u": username}).mappings().first()
    if not row:
        return None
    if not secrets.compare_digest(
        _hash(password, row["password_salt"] or ""), row["password_hash"] or ""
    ):
        return None
    return {"executive_id": row["executive_id"], "username": row["username"],
            "full_name": row["full_name"], "role": row["role"]}


def create_session(engine, executive_id: int) -> str:
    token = secrets.token_hex(32)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO kirana_oltp.call_executive_session (executive_id, access_token)
            VALUES (:eid, :tok)
        """), {"eid": executive_id, "tok": token})
    return token


def executive_by_token(engine, token: str) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT e.executive_id, e.username, e.full_name, e.role
            FROM kirana_oltp.call_executive_session s
            JOIN kirana_oltp.call_executive e ON e.executive_id = s.executive_id
            WHERE s.access_token = :tok
              AND s.revoked_at IS NULL
              AND s.created_at > NOW() - INTERVAL '30 days'
              AND e.is_active = TRUE
        """), {"tok": token}).mappings().first()
    return dict(row) if row else None


def revoke_session(engine, token: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE kirana_oltp.call_executive_session SET revoked_at = NOW() "
            "WHERE access_token = :tok AND revoked_at IS NULL"
        ), {"tok": token})


# ── Executives (manager) ──────────────────────────────────────────────────────

def create_executive(engine, username: str, full_name: str, phone: Optional[str],
                     email: Optional[str], role: str, password: str) -> dict:
    salt = secrets.token_hex(16)
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO kirana_oltp.call_executive
                (username, full_name, phone, email, role, password_salt, password_hash)
            VALUES (:u, :fn, :ph, :em, :role, :salt, :hash)
            RETURNING executive_id, username, full_name, phone, email, role, is_active, created_at
        """), {"u": username, "fn": full_name, "ph": phone, "em": email, "role": role,
               "salt": salt, "hash": _hash(password, salt)}).mappings().first()
    return dict(row)


def list_executives(engine) -> list[dict]:
    """Executives with live counts: active store assignments + calls logged today."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT e.executive_id, e.username, e.full_name, e.phone, e.email, e.role,
                   e.is_active, e.created_at,
                   COALESCE(a.assigned_count, 0) AS assigned_count,
                   COALESCE(c.calls_today, 0)    AS calls_today
            FROM kirana_oltp.call_executive e
            LEFT JOIN (
                SELECT executive_id, COUNT(*) AS assigned_count
                FROM kirana_oltp.store_assignment WHERE status = 'active'
                GROUP BY executive_id
            ) a ON a.executive_id = e.executive_id
            LEFT JOIN (
                SELECT executive_id, COUNT(*) AS calls_today
                FROM kirana_oltp.call_log
                WHERE called_at >= CURRENT_DATE
                GROUP BY executive_id
            ) c ON c.executive_id = e.executive_id
            ORDER BY e.executive_id
        """)).mappings().all()
    return [dict(r) for r in rows]


def get_executive(engine, executive_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT executive_id, username, full_name, phone, email, role, is_active, created_at
            FROM kirana_oltp.call_executive WHERE executive_id = :eid
        """), {"eid": executive_id}).mappings().first()
    return dict(row) if row else None


def set_executive_active(engine, executive_id: int, active: bool) -> bool:
    with engine.begin() as conn:
        res = conn.execute(text(
            "UPDATE kirana_oltp.call_executive SET is_active = :a WHERE executive_id = :eid"
        ), {"a": active, "eid": executive_id})
    return res.rowcount > 0


def reset_password(engine, executive_id: int, password: str) -> bool:
    salt = secrets.token_hex(16)
    with engine.begin() as conn:
        res = conn.execute(text(
            "UPDATE kirana_oltp.call_executive SET password_salt = :salt, password_hash = :hash "
            "WHERE executive_id = :eid"
        ), {"salt": salt, "hash": _hash(password, salt), "eid": executive_id})
    return res.rowcount > 0


# ── Assignments (manager) ─────────────────────────────────────────────────────

def assign_stores(engine, executive_id: int, store_ids: list[int],
                  assigned_by: Optional[int]) -> dict:
    """Assign stores to an executive. A store has at most one ACTIVE assignment, so
    reassigning moves it (old active rows are marked unassigned first). Idempotent."""
    assigned, skipped = 0, 0
    with engine.begin() as conn:
        for sid in store_ids:
            exists = conn.execute(text(
                "SELECT 1 FROM kirana_oltp.store WHERE store_id = :sid "
                "AND NOT COALESCE(is_deleted, FALSE)"
            ), {"sid": sid}).first()
            if not exists:
                skipped += 1
                continue
            # Free any current active assignment (possibly to a different exec).
            conn.execute(text(
                "UPDATE kirana_oltp.store_assignment SET status = 'unassigned' "
                "WHERE store_id = :sid AND status = 'active'"
            ), {"sid": sid})
            conn.execute(text("""
                INSERT INTO kirana_oltp.store_assignment (store_id, executive_id, assigned_by)
                VALUES (:sid, :eid, :by)
            """), {"sid": sid, "eid": executive_id, "by": assigned_by})
            assigned += 1
    return {"assigned": assigned, "skipped": skipped}


def unassign_store(engine, store_id: int) -> bool:
    with engine.begin() as conn:
        res = conn.execute(text(
            "UPDATE kirana_oltp.store_assignment SET status = 'unassigned' "
            "WHERE store_id = :sid AND status = 'active'"
        ), {"sid": store_id})
    return res.rowcount > 0


def assignment_load(engine) -> list[dict]:
    """Per-executive active store count — for workload balancing."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT e.executive_id, e.full_name,
                   COUNT(a.assignment_id) FILTER (WHERE a.status = 'active') AS active_stores
            FROM kirana_oltp.call_executive e
            LEFT JOIN kirana_oltp.store_assignment a ON a.executive_id = e.executive_id
            WHERE e.role = 'call_executive' AND e.is_active = TRUE
            GROUP BY e.executive_id, e.full_name
            ORDER BY active_stores ASC, e.executive_id
        """)).mappings().all()
    return [dict(r) for r in rows]


def is_assigned(engine, executive_id: int, store_id: int) -> bool:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM kirana_oltp.store_assignment "
            "WHERE executive_id = :eid AND store_id = :sid AND status = 'active'"
        ), {"eid": executive_id, "sid": store_id}).first()
    return row is not None


# ── Store context + queue scoring ─────────────────────────────────────────────

# Raw per-store signals; deltas computed in SQL (ints) so Python never mixes
# tz-aware and naive datetimes. Owner = the active store_owner on users.store_id.
_STORE_SIGNALS_SELECT = """
    SELECT st.store_id, st.name AS store_name, st.location,
           o.full_name AS owner_name, o.phone_number,
           sub.tier, sub.is_trial,
           FLOOR(EXTRACT(EPOCH FROM (sub.trial_ends_at - NOW())) / 86400)::int AS trial_days_left,
           FLOOR(EXTRACT(EPOCH FROM (NOW() - lc.called_at)) / 86400)::int      AS days_since_call,
           FLOOR(EXTRACT(EPOCH FROM (NOW() - ll.last_login)) / 86400)::int     AS days_since_login,
           lc.called_at IS NULL                                                AS never_called,
           (lc.next_action = 'callback' AND lc.callback_at <= NOW())           AS callback_due,
           (lc.next_action = 'do_not_call')                                    AS is_dnc,
           lc.disposition   AS last_disposition,
           lc.app_usage_status AS last_app_usage,
           lc.called_at     AS last_call_at,
           COALESCE(ord.orders_7d, 0) AS orders_7d
    FROM kirana_oltp.store st
    LEFT JOIN LATERAL (
        SELECT full_name, phone_number, user_id FROM kirana_oltp.users
        WHERE store_id = st.store_id AND role = 'store_owner'
          AND NOT COALESCE(is_deleted, FALSE)
        ORDER BY user_id LIMIT 1
    ) o ON TRUE
    LEFT JOIN kirana_oltp.subscription sub ON sub.store_id = st.store_id
    LEFT JOIN LATERAL (
        SELECT MAX(created_at) AS last_login FROM kirana_oltp.user_sessions
        WHERE user_id = o.user_id
    ) ll ON TRUE
    LEFT JOIN LATERAL (
        SELECT COUNT(*) AS orders_7d FROM kirana_oltp.orders
        WHERE store_id = st.store_id AND order_date >= NOW() - INTERVAL '7 days'
    ) ord ON TRUE
    LEFT JOIN LATERAL (
        SELECT called_at, next_action, callback_at, disposition, app_usage_status
        FROM kirana_oltp.call_log WHERE store_id = st.store_id
        ORDER BY called_at DESC LIMIT 1
    ) lc ON TRUE
"""


def _score_and_reason(r: dict) -> tuple[int, str]:
    """Needs-attention score (higher = call sooner) + a short human reason.
    Pure int math on the SQL-computed deltas."""
    score, reasons = 0, []
    if r["callback_due"]:
        # A scheduled callback is a promise to the owner — always top the queue,
        # above any accumulation of other signals.
        score += 10000
        reasons.append("Callback due")
    if r["never_called"]:
        score += 500
        reasons.append("Never called")
    elif r["days_since_call"] is not None and r["days_since_call"] >= 14:
        score += 100
        reasons.append(f"No call in {r['days_since_call']}d")
    tdl = r["trial_days_left"]
    if tdl is not None:
        if 0 <= tdl <= 7:
            score += 300
            reasons.append(f"Trial ends in {tdl}d")
        elif tdl < 0 and (r["tier"] in (None, "trial", "pending_trial") or r["is_trial"]):
            score += 250
            reasons.append("Trial expired")
    if (r["orders_7d"] or 0) == 0:
        score += 200
        reasons.append("No sales 7d")
    dsl = r["days_since_login"]
    if dsl is None:
        score += 150
        reasons.append("Never logged in")
    elif dsl >= 14:
        score += 120
        reasons.append(f"Inactive {dsl}d")
    return score, ", ".join(reasons[:2]) if reasons else "Routine check"


def get_queue(engine, executive_id: int, limit: int = 100) -> list[dict]:
    """The executive's assigned stores, scored + sorted by needs-attention.
    Do-not-call stores are excluded from the queue."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT sig.* FROM kirana_oltp.store_assignment a
            JOIN LATERAL (
                {_STORE_SIGNALS_SELECT}
                WHERE st.store_id = a.store_id AND NOT COALESCE(st.is_deleted, FALSE)
            ) sig ON TRUE
            WHERE a.executive_id = :eid AND a.status = 'active'
        """), {"eid": executive_id}).mappings().all()

    out = []
    for r in rows:
        r = dict(r)
        if r["is_dnc"]:
            continue
        score, reason = _score_and_reason(r)
        out.append({
            "store_id": r["store_id"], "store_name": r["store_name"], "location": r["location"],
            "owner_name": r["owner_name"], "phone_number": r["phone_number"],
            "tier": r["tier"], "trial_days_left": r["trial_days_left"],
            "orders_7d": r["orders_7d"], "days_since_login": r["days_since_login"],
            "last_call_at": str(r["last_call_at"]) if r["last_call_at"] else None,
            "last_disposition": r["last_disposition"], "last_app_usage": r["last_app_usage"],
            "callback_due": bool(r["callback_due"]), "never_called": bool(r["never_called"]),
            "score": score, "reason": reason,
        })
    out.sort(key=lambda d: (d["score"], d["store_id"]), reverse=True)
    return out[:limit]


def store_call_history(engine, store_id: int) -> list[dict]:
    """All calls logged for a store, newest first (with tags + executive name)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.call_id, c.called_at, c.disposition, c.answered, c.app_usage_status,
                   c.feedback_text, c.sentiment, c.rating, c.next_action, c.callback_at,
                   e.full_name AS executive_name,
                   COALESCE(ARRAY_AGG(t.tag) FILTER (WHERE t.tag IS NOT NULL), '{}') AS tags
            FROM kirana_oltp.call_log c
            JOIN kirana_oltp.call_executive e ON e.executive_id = c.executive_id
            LEFT JOIN kirana_oltp.call_feedback_tag t ON t.call_id = c.call_id
            WHERE c.store_id = :sid
            GROUP BY c.call_id, e.full_name
            ORDER BY c.called_at DESC
        """), {"sid": store_id}).mappings().all()
    return [{**dict(r), "called_at": str(r["called_at"]),
             "callback_at": str(r["callback_at"]) if r["callback_at"] else None,
             "tags": list(r["tags"])} for r in rows]


def get_call_sheet(engine, executive_id: int, store_id: int) -> Optional[dict]:
    """Focused store context (no financials) + full call history. Assumes the caller
    already checked the assignment (routes enforce it)."""
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            {_STORE_SIGNALS_SELECT}
            WHERE st.store_id = :sid AND NOT COALESCE(st.is_deleted, FALSE)
        """), {"sid": store_id}).mappings().first()
    if not row:
        return None
    r = dict(row)
    score, reason = _score_and_reason(r)
    return {
        "store_id": r["store_id"], "store_name": r["store_name"], "location": r["location"],
        "owner_name": r["owner_name"], "phone_number": r["phone_number"],
        "tier": r["tier"], "trial_days_left": r["trial_days_left"],
        "orders_7d": r["orders_7d"], "days_since_login": r["days_since_login"],
        "never_called": bool(r["never_called"]), "callback_due": bool(r["callback_due"]),
        "is_dnc": bool(r["is_dnc"]), "score": score, "reason": reason,
        "history": store_call_history(engine, store_id),
    }


# ── Call logging ──────────────────────────────────────────────────────────────

def log_call(engine, executive_id: int, store_id: int, payload: dict) -> dict:
    """Insert one call_log row (+ feedback tags). `payload` keys mirror the schema
    columns; disposition is required. answered is derived from the disposition."""
    disposition = payload["disposition"]
    answered = disposition == "answered"
    tags = payload.get("tags") or []
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO kirana_oltp.call_log
                (store_id, executive_id, answered, disposition, app_usage_status,
                 feedback_text, sentiment, rating, next_action, callback_at)
            VALUES
                (:sid, :eid, :answered, :disp, :usage, :fb, :sent, :rating, :na, :cb)
            RETURNING call_id, called_at
        """), {
            "sid": store_id, "eid": executive_id, "answered": answered, "disp": disposition,
            "usage": payload.get("app_usage_status"), "fb": payload.get("feedback_text"),
            "sent": payload.get("sentiment"), "rating": payload.get("rating"),
            "na": payload.get("next_action"), "cb": payload.get("callback_at"),
        }).mappings().first()
        call_id = row["call_id"]
        for tag in tags:
            conn.execute(text(
                "INSERT INTO kirana_oltp.call_feedback_tag (call_id, tag) VALUES (:cid, :tag)"
            ), {"cid": call_id, "tag": tag})
    return {"call_id": int(call_id), "called_at": str(row["called_at"])}


def get_callbacks(engine, executive_id: int) -> list[dict]:
    """The executive's scheduled callbacks that are still the store's latest action
    (a newer call supersedes an old callback). Overdue flagged."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.call_id, c.store_id, st.name AS store_name, o.phone_number,
                   c.callback_at, (c.callback_at <= NOW()) AS overdue
            FROM kirana_oltp.call_log c
            JOIN kirana_oltp.store st ON st.store_id = c.store_id
            LEFT JOIN LATERAL (
                SELECT phone_number FROM kirana_oltp.users
                WHERE store_id = c.store_id AND role = 'store_owner'
                  AND NOT COALESCE(is_deleted, FALSE) ORDER BY user_id LIMIT 1
            ) o ON TRUE
            WHERE c.executive_id = :eid AND c.next_action = 'callback'
              AND c.callback_at IS NOT NULL
              AND c.call_id = (
                  SELECT call_id FROM kirana_oltp.call_log
                  WHERE store_id = c.store_id ORDER BY called_at DESC LIMIT 1
              )
            ORDER BY c.callback_at ASC
        """), {"eid": executive_id}).mappings().all()
    return [{**dict(r), "callback_at": str(r["callback_at"]), "overdue": bool(r["overdue"])}
            for r in rows]


def get_stats(engine, executive_id: int) -> dict:
    """The executive's personal numbers over the last 30 days + today."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE called_at >= CURRENT_DATE)              AS calls_today,
                COUNT(*) FILTER (WHERE called_at >= NOW() - INTERVAL '30 days') AS calls_30d,
                COUNT(*) FILTER (WHERE called_at >= NOW() - INTERVAL '30 days' AND answered) AS answered_30d,
                COUNT(*) FILTER (WHERE called_at >= NOW() - INTERVAL '30 days'
                                 AND app_usage_status IN ('using_active','using_rare')) AS using_30d,
                AVG(rating) FILTER (WHERE called_at >= NOW() - INTERVAL '30 days') AS avg_rating
            FROM kirana_oltp.call_log WHERE executive_id = :eid
        """), {"eid": executive_id}).mappings().first()
        assigned = conn.execute(text(
            "SELECT COUNT(*) FROM kirana_oltp.store_assignment "
            "WHERE executive_id = :eid AND status = 'active'"
        ), {"eid": executive_id}).scalar()
        pending_cb = conn.execute(text("""
            SELECT COUNT(*) FROM kirana_oltp.call_log c
            WHERE c.executive_id = :eid AND c.next_action = 'callback'
              AND c.call_id = (SELECT call_id FROM kirana_oltp.call_log
                               WHERE store_id = c.store_id ORDER BY called_at DESC LIMIT 1)
        """), {"eid": executive_id}).scalar()

    calls_30d = int(row["calls_30d"] or 0)
    answered_30d = int(row["answered_30d"] or 0)
    return {
        "assigned_stores": int(assigned or 0),
        "calls_today": int(row["calls_today"] or 0),
        "calls_30d": calls_30d,
        "answered_30d": answered_30d,
        "connect_rate": round(answered_30d / calls_30d, 4) if calls_30d else 0.0,
        "using_30d": int(row["using_30d"] or 0),
        "avg_rating": round(float(row["avg_rating"]), 2) if row["avg_rating"] is not None else None,
        "pending_callbacks": int(pending_cb or 0),
    }


# ── Manager oversight ─────────────────────────────────────────────────────────

def list_stores_for_assignment(engine, q: Optional[str] = None,
                              unassigned_only: bool = False, limit: int = 500) -> list[dict]:
    """Stores with their current active assignment — powers the manager Assignments
    screen (works for a manager token, not just the admin key)."""
    where = ["NOT COALESCE(st.is_deleted, FALSE)"]
    params: dict = {"limit": limit}
    if q:
        where.append("st.name ILIKE :q")
        params["q"] = f"%{q}%"
    if unassigned_only:
        where.append("a.executive_id IS NULL")
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT st.store_id, st.name AS store_name, st.location,
                   a.executive_id AS assigned_executive_id,
                   e.full_name    AS assigned_executive_name
            FROM kirana_oltp.store st
            LEFT JOIN kirana_oltp.store_assignment a
                   ON a.store_id = st.store_id AND a.status = 'active'
            LEFT JOIN kirana_oltp.call_executive e ON e.executive_id = a.executive_id
            WHERE {' AND '.join(where)}
            ORDER BY st.name
            LIMIT :limit
        """), params).mappings().all()
    return [dict(r) for r in rows]


def list_feedback(engine, days: int = 30, tag: Optional[str] = None,
                  sentiment: Optional[str] = None, limit: int = 200) -> list[dict]:
    """Feedback digest: calls that carry feedback text or tags, newest first,
    filterable by tag / sentiment. For the product team."""
    filters = ["c.called_at >= NOW() - (:days || ' days')::interval",
               "(c.feedback_text IS NOT NULL OR t.tag IS NOT NULL)"]
    params: dict = {"days": days, "limit": limit}
    if sentiment:
        filters.append("c.sentiment = :sentiment")
        params["sentiment"] = sentiment
    tag_join = ""
    if tag:
        tag_join = "JOIN kirana_oltp.call_feedback_tag ft ON ft.call_id = c.call_id AND ft.tag = :tag"
        params["tag"] = tag
    where = " AND ".join(filters)
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT c.call_id, c.store_id, st.name AS store_name, c.called_at,
                   c.feedback_text, c.sentiment, c.rating, c.app_usage_status,
                   e.full_name AS executive_name,
                   COALESCE(ARRAY_AGG(DISTINCT t.tag) FILTER (WHERE t.tag IS NOT NULL), '{{}}') AS tags
            FROM kirana_oltp.call_log c
            JOIN kirana_oltp.store st ON st.store_id = c.store_id
            JOIN kirana_oltp.call_executive e ON e.executive_id = c.executive_id
            {tag_join}
            LEFT JOIN kirana_oltp.call_feedback_tag t ON t.call_id = c.call_id
            WHERE {where}
            GROUP BY c.call_id, st.name, e.full_name
            ORDER BY c.called_at DESC
            LIMIT :limit
        """), params).mappings().all()
    return [{**dict(r), "called_at": str(r["called_at"]), "tags": list(r["tags"])}
            for r in rows]
