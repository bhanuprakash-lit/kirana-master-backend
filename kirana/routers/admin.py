import logging
from typing import TYPE_CHECKING
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from kirana.intelligence.engine import IntelligenceEngine

from kirana.service import KiranaService

router = APIRouter(prefix="/kirana", tags=["Kirana AI"])


def _svc(request: Request) -> KiranaService:
    return request.app.state.kirana_service


def _auth(request: Request):
    s = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer ") :] if auth_hdr.startswith("Bearer ") else ""

    if api_key and api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = _svc(request).user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _require_admin(user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _require_store(store_id: int, user: dict = Depends(_auth)):
    if user.get("role") == "admin":
        return user
    if user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return user


# ── Admin: ML model freshness + retraining ──────────────────────────────────────


@router.get("/admin/ml/status")
async def ml_status(
    request: Request, refresh: bool = False, user: dict = Depends(_auth)
):
    """Prediction-CSV freshness (per-file age + overall stale flag).
    Pass ?refresh=true to reload the CSVs from disk first."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    svc = _svc(request)
    if refresh:
        svc.ml.refresh()
    return svc.ml.freshness()


@router.post("/admin/ml/retrain")
async def ml_retrain(request: Request, user: dict = Depends(_auth)):
    """Kick off model retraining (ml_models/train_all.py) in the background.
    Output is appended to logs/ml_retrain.log. Re-check /admin/ml/status?refresh=true after."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    import os as _os
    import sys as _sys
    import subprocess as _sp

    root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    script = _os.path.join(root, "ml_models", "train_all.py")
    if not _os.path.exists(script):
        raise HTTPException(status_code=500, detail="train_all.py not found")
    try:
        log_path = _os.path.join(root, "logs", "ml_retrain.log")
        logf = open(log_path, "a", encoding="utf-8")
        _sp.Popen([_sys.executable, script], cwd=root, stdout=logf, stderr=_sp.STDOUT)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not start retraining: {exc}"
        )
    return {
        "status": "started",
        "note": "Retraining runs in the background (logs/ml_retrain.log). "
        "Check /admin/ml/status?refresh=true in a few minutes.",
    }


@router.get("/admin/stats")
async def admin_stats(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(
                _text("""
            SELECT
                (SELECT COUNT(*) FROM kirana_oltp.store WHERE NOT is_deleted) AS total_stores,
                (SELECT COUNT(*) FROM kirana_oltp.users WHERE role = 'store_owner' AND NOT COALESCE(is_deleted, FALSE)) AS total_users,
                (SELECT COUNT(*) FROM kirana_oltp.subscription
                 WHERE tier = 'pending_trial' AND (ended_at IS NULL OR ended_at > NOW())) AS pending_trials,
                (SELECT COUNT(*) FROM kirana_oltp.subscription
                 WHERE tier = 'trial' AND (ended_at IS NULL OR ended_at > NOW())) AS active_trials,
                (SELECT COUNT(*) FROM kirana_oltp.subscription
                 WHERE tier = 'basic' AND (ended_at IS NULL OR ended_at > NOW())) AS basic_count,
                (SELECT COUNT(*) FROM kirana_oltp.subscription
                 WHERE tier = 'pro' AND (ended_at IS NULL OR ended_at > NOW())) AS pro_count
        """)
            )
            .mappings()
            .first()
        )
    return dict(row)


@router.get("/admin/stores")
async def admin_list_stores(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _text("""
            -- Owner comes from the store_user membership table (true ownership),
            -- NOT users.store_id (that's the owner's *active* store pointer — using
            -- it showed an owner against only their last-opened store).
            SELECT s.store_id, s.name AS store_name, s.location, s.created_at,
                   COALESCE(s.vertical_code, 'grocery') AS vertical_code,
                   o.user_id, o.username, o.phone_number,
                   COALESCE(o.full_name, o.username) AS owner_name,
                   COALESCE(o.store_count, 0) AS owner_store_count,
                   sub.tier, sub.trial_tier,
                   sub.trial_ends_at, sub.ended_at,
                   COALESCE(up.allow_social_marketing, FALSE) AS allow_social_marketing
            FROM kirana_oltp.store s
            LEFT JOIN LATERAL (
                SELECT u.user_id, u.username, u.phone_number, u.full_name,
                       (SELECT COUNT(*) FROM kirana_oltp.store_user su2
                        JOIN kirana_oltp.store s2 ON s2.store_id = su2.store_id
                         AND NOT COALESCE(s2.is_deleted, FALSE)
                        WHERE su2.user_id = u.user_id AND su2.role = 'owner') AS store_count
                FROM kirana_oltp.store_user su
                JOIN kirana_oltp.users u ON u.user_id = su.user_id
                 AND NOT COALESCE(u.is_deleted, FALSE)
                WHERE su.store_id = s.store_id AND su.role = 'owner'
                ORDER BY u.user_id LIMIT 1
            ) o ON TRUE
            LEFT JOIN kirana_oltp.user_prefs up ON up.user_id = o.user_id
            LEFT JOIN kirana_oltp.subscription sub ON sub.store_id = s.store_id
            WHERE NOT s.is_deleted
            ORDER BY s.created_at DESC
        """)
            )
            .mappings()
            .all()
        )
    return {"stores": [dict(r) for r in rows]}


@router.get("/admin/director/stores")
async def admin_director_stores(request: Request, user: dict = Depends(_auth)):
    """List all stores with their director-dashboard inclusion flag, so the admin
    can curate which stores' analytics the director sees (excludes dev/test stores)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        rows = conn.execute(_text("""
            SELECT store_id, name, location,
                   COALESCE(vertical_code, 'grocery') AS vertical_code,
                   COALESCE(include_in_director, TRUE) AS include_in_director
            FROM kirana_oltp.store
            WHERE NOT COALESCE(is_deleted, FALSE)
            ORDER BY include_in_director DESC, name
        """)).mappings().all()
    return {"stores": [dict(r) for r in rows]}


@router.post("/admin/director/stores/{store_id}")
async def admin_set_director_store(
    store_id: int, request: Request, user: dict = Depends(_auth)
):
    """Toggle whether a store's data appears in the director analytics dashboard."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    include = bool(body.get("include", True))
    from sqlalchemy import text as _text

    with request.app.state.engine.begin() as conn:
        res = conn.execute(_text(
            "UPDATE kirana_oltp.store SET include_in_director = :inc "
            "WHERE store_id = :sid AND NOT COALESCE(is_deleted, FALSE)"
        ), {"inc": include, "sid": store_id})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Store not found")
    return {"store_id": store_id, "include_in_director": include}


@router.get("/admin/stores/{store_id}/deep-dive")
async def admin_store_deep_dive(
    store_id: int, request: Request, user: dict = Depends(_auth)
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    data = repo.get_store_deep_dive(store_id)
    if not data:
        raise HTTPException(status_code=404, detail="Store not found")
    return data


@router.get("/admin/vision/analytics")
async def admin_vision_analytics(
    request: Request,
    days: int = 30,
    store_id: int | None = None,
    user: dict = Depends(_auth),
):
    """Vision AI analytics for the admin panel. Fleet-wide by default (all stores);
    pass ?store_id= to scope to one store. Returns the same analytics shape the
    store-facing /kirana/vision/analytics endpoint does, plus a per-store breakdown
    so the admin can see which stores use vision and how accurate it is for each."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    from vision import repository as vision_repo

    engine = request.app.state.engine
    data = vision_repo.get_analytics(engine, store_id, days)
    data["store_id"] = store_id
    # The per-store table is only meaningful for the fleet view.
    data["stores"] = [] if store_id is not None else vision_repo.get_store_breakdown(engine, days)
    return data


@router.get("/admin/intelligence/triggers")
async def admin_list_triggers(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    engine = getattr(request.app.state, "intelligence", None)
    if engine is None:
        return {"triggers": []}
    return {"triggers": engine.available_triggers()}


@router.get("/admin/sessions")
async def admin_list_sessions(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    sessions = repo.list_active_sessions(limit=100)
    return {"sessions": sessions}


@router.get("/admin/vouchers")
async def admin_list_vouchers(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    vouchers = repo.list_vouchers(limit=100)
    return {"vouchers": vouchers}


@router.get("/admin/intelligence/all-logs")
async def admin_intelligence_all_logs(
    request: Request, limit: int = 50, user: dict = Depends(_auth)
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from kirana.intelligence.repository import IntelligenceRepository

    repo = IntelligenceRepository(request.app.state.engine)
    logs = repo.list_all_logs(limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.post("/admin/notify")
async def admin_notify(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    title = body.get("title", "Kirana AI")
    message = body.get("body", "")
    store_id = body.get("store_id")  # null = broadcast to all
    svc = _svc(request)
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        if store_id:
            row = (
                conn.execute(
                    _text(
                        "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND NOT COALESCE(is_deleted, FALSE) LIMIT 1"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .first()
            )
            if not row:
                raise HTTPException(
                    status_code=404, detail="No user found for this store"
                )
            user_ids = [row["user_id"]]
        else:
            rows = (
                conn.execute(
                    _text(
                        "SELECT user_id FROM kirana_oltp.users WHERE role = 'store_owner' AND NOT COALESCE(is_deleted, FALSE)"
                    )
                )
                .mappings()
                .all()
            )
            user_ids = [r["user_id"] for r in rows]
    sent = sum(
        1
        for uid in user_ids
        if svc.send_fcm_to_user(
            uid,
            title,
            message,
            data={"action": "admin_notify", "channel": "kirana_account"},
        )
    )
    return {"sent": sent, "total": len(user_ids)}


@router.post("/admin/payment/mock-confirm")
async def admin_mock_payment(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    store_id = body.get("store_id")
    tier = body.get("tier", "basic")
    if tier not in ("basic", "pro"):
        raise HTTPException(status_code=400, detail="tier must be basic or pro")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        conn.execute(
            _text("""
            INSERT INTO kirana_oltp.subscription (store_id, tier, started_at)
            VALUES (:sid, :tier, NOW())
            ON CONFLICT (store_id) DO UPDATE
              SET tier = :tier, started_at = NOW(), ended_at = NULL,
                  is_trial = FALSE, trial_ends_at = NULL
        """),
            {"sid": store_id, "tier": tier},
        )
        conn.commit()
        owner = (
            conn.execute(
                _text(
                    "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND NOT COALESCE(is_deleted, FALSE) LIMIT 1"
                ),
                {"sid": store_id},
            )
            .mappings()
            .first()
        )
    if owner:
        tier_label = "Pro" if tier == "pro" else "Basic"
        _svc(request).send_fcm_to_user(
            owner["user_id"],
            f"Your Kirana AI {tier_label} plan is active!",
            f"Admin activated your {tier_label} plan.",
            data={"action": "open_subscription", "channel": "kirana_account"},
        )
    return {"success": True}


@router.get("/admin/all-subscriptions")
async def list_all_subscriptions(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _text("""
            SELECT s.store_id, st.name AS store_name, s.tier,
                   s.started_at, s.ended_at, s.is_trial, s.trial_ends_at
            FROM kirana_oltp.subscription s
            JOIN kirana_oltp.store st ON st.store_id = s.store_id
            ORDER BY s.started_at DESC
        """)
            )
            .mappings()
            .all()
        )
    return {"subscriptions": [dict(r) for r in rows]}


@router.get("/admin/user-activity")
async def admin_user_activity(request: Request, user: dict = Depends(_auth)):
    """Per-user app activity: last seen, opens today, time in app, last login, login method, sales."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _text("""
            SELECT
                u.user_id,
                u.username,
                COALESCE(u.full_name, u.username) AS full_name,
                s.name AS store_name,
                -- Last foreground event (actual app open), fall back to last session
                COALESCE(
                    (SELECT MAX(a.created_at) FROM kirana_oltp.app_activity a
                     WHERE a.user_id = u.user_id AND a.event = 'foreground'),
                    (SELECT MAX(sess.created_at) FROM kirana_oltp.user_sessions sess
                     WHERE sess.user_id = u.user_id)
                ) AS last_seen,
                -- Last login timestamp
                (SELECT MAX(sess.created_at) FROM kirana_oltp.user_sessions sess
                 WHERE sess.user_id = u.user_id) AS last_login,
                -- Login method of the most recent session
                (SELECT sess.login_method FROM kirana_oltp.user_sessions sess
                 WHERE sess.user_id = u.user_id
                 ORDER BY sess.created_at DESC LIMIT 1) AS last_login_method,
                -- App opens today (foreground events)
                COALESCE((
                    SELECT COUNT(*)::int FROM kirana_oltp.app_activity a
                    WHERE a.user_id = u.user_id AND a.event = 'foreground'
                      AND DATE(a.created_at AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                ), 0) AS opens_today,
                -- Total foreground seconds today (from background events that carry duration)
                COALESCE((
                    SELECT SUM(a.duration_sec)::int FROM kirana_oltp.app_activity a
                    WHERE a.user_id = u.user_id AND a.event = 'background'
                      AND DATE(a.created_at AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                      AND a.duration_sec IS NOT NULL
                ), 0) AS foreground_sec_today,
                -- Total login sessions (historical)
                COALESCE((
                    SELECT COUNT(*)::int FROM kirana_oltp.user_sessions sess
                    WHERE sess.user_id = u.user_id
                ), 0) AS total_sessions,
                -- Sales today
                COALESCE((
                    SELECT COUNT(*)::int FROM kirana_oltp.orders o
                    WHERE o.store_id = u.store_id
                      AND DATE(o.order_date AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                ), 0) AS sales_today,
                -- How many stores this owner runs (store_user membership)
                COALESCE((
                    SELECT COUNT(*)::int FROM kirana_oltp.store_user su
                    JOIN kirana_oltp.store s2 ON s2.store_id = su.store_id
                     AND NOT COALESCE(s2.is_deleted, FALSE)
                    WHERE su.user_id = u.user_id AND su.role = 'owner'
                ), 0) AS stores_owned
            FROM kirana_oltp.users u
            LEFT JOIN kirana_oltp.store s
                ON s.store_id = u.store_id AND NOT s.is_deleted
            WHERE u.role = 'store_owner' AND NOT COALESCE(u.is_deleted, FALSE)
            ORDER BY last_seen DESC NULLS LAST
        """)
            )
            .mappings()
            .all()
        )
    return {"users": [dict(r) for r in rows]}
