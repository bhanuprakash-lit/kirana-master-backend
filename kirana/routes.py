from typing import Optional, List, TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

if TYPE_CHECKING:
    from kirana.intelligence.engine import IntelligenceEngine

from kirana.schemas import (
    AgentQueryRequest, ExplainRequest,
    IssueReportCreate, FcmTokenUpdate,
    LoginRequest, SnapshotSummary,
    RecommendationQueryRequest,
    StoreUpdateRequest, UserPrefsUpdate, PhoneLoginRequest,
    RegisterStoreOwnerRequest,
    InventorySnapshotWriteRequest,
    UdhaarAddRequest, UdhaarRecoveryRequest, UdhaarRemindRequest, CustomerSyncRequest,
    CustomerSyncItem,
    CashflowRequestCreate,
    ReferralCampaignCreate, ReferralTokenRequest, ReferralScanRequest, VoucherUseRequest,
    SubscriptionUpgradeRequest,
    PaymentOrderRequest, PaymentVerifyRequest,
    ChangePasswordRequest,
)
from kirana.service import KiranaService

router = APIRouter(prefix="/kirana", tags=["Kirana AI"])

def _svc(request: Request) -> KiranaService:
    return request.app.state.kirana_service

def _auth(request: Request):
    s = request.app.state.settings
    api_key  = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer   = auth_hdr[len("Bearer "):] if auth_hdr.startswith("Bearer ") else ""

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


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", include_in_schema=True)
async def health(request: Request):
    return _svc(request).health()


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/auth/login")
async def login(request: Request, body: LoginRequest):
    try:
        return _svc(request).login(body)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/auth/register")
async def register(request: Request, body: RegisterStoreOwnerRequest):
    return _svc(request).register_store_owner(body).model_dump()


@router.post("/auth/phone-login")
async def phone_login(request: Request, body: PhoneLoginRequest):
    """Log in using a Firebase-verified phone number. Returns 404 if no account exists."""
    try:
        return _svc(request).phone_login(body)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/auth/check-username/{username}")
async def check_username(username: str, request: Request):
    """Returns {available: bool} — call before registration to validate uniqueness."""
    available = _svc(request).check_username_available(username)
    return {"available": available, "username": username}


@router.get("/auth/me")
async def me(user: dict = Depends(_auth)):
    return user


@router.get("/catalog/search")
async def catalog_search(
    request: Request,
    q: str = "",
    barcode: str = "",
    limit: int = 20,
    user: dict = Depends(_auth),
):
    """Search global product catalog by name (ILIKE) or barcode (exact)."""
    from sqlalchemy import text as _text
    engine = request.app.state.engine
    params: dict = {"limit": limit}

    q = q.strip()
    barcode = barcode.strip()

    if barcode:
        where = "p.barcode = :barcode"
        params["barcode"] = barcode
    elif len(q) >= 2:
        where = "p.name ILIKE :q OR p.brand ILIKE :q"
        params["q"] = f"%{q}%"
    else:
        return {"products": []}

    sql = f"""
    SELECT p.product_id, p.name, p.brand, p.unit, p.weight,
           p.barcode, p.is_perishable, p.is_loose, p.image_url, p.sku,
           p.category_id,
           c.name AS category_name,
           pc.name AS parent_category_name
    FROM kirana_oltp.product p
    JOIN kirana_oltp.category c ON p.category_id = c.category_id
    LEFT JOIN kirana_oltp.category pc ON c.parent_category_id = pc.category_id
    WHERE {where}
    ORDER BY p.name
    LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(_text(sql), params).mappings().all()
    return {"products": [dict(r) for r in rows]}


@router.get("/auth/password-status")
async def password_status(request: Request, user: dict = Depends(_auth)):
    from kirana.repository import KiranaRepository
    repo = KiranaRepository(request.app.state.engine)
    return repo.get_password_status(user["user_id"])


@router.post("/auth/change-password")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    user: dict = Depends(_auth),
):
    from kirana.repository import KiranaRepository
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    repo = KiranaRepository(request.app.state.engine)
    try:
        repo.change_password(user["user_id"], body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True}


@router.post("/auth/fcm-token")
async def update_fcm_token(request: Request, body: FcmTokenUpdate, user: dict = Depends(_auth)):
    ok = _svc(request).update_fcm_token(user["user_id"], body.fcm_token)
    return {"success": ok}


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(request: Request, admin: dict = Depends(_require_admin)):
    return _svc(request).list_users()


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, admin: dict = Depends(_require_admin)):
    ok = _svc(request).delete_user(user_id)
    return {"deleted": ok}


# ── Stores ────────────────────────────────────────────────────────────────────

@router.get("/stores")
async def list_stores(request: Request, user: dict = Depends(_auth)):
    stores = _svc(request).list_stores()
    if user.get("role") == "admin":
        return {"stores": stores}
    # Non-admins only see their own store
    filtered = [s for s in stores if s["store_id"] == user.get("store_id")]
    return {"stores": filtered}


@router.patch("/stores/{store_id}")
async def update_store(store_id: int, body: StoreUpdateRequest, request: Request, user: dict = Depends(_auth)):
    _require_store(store_id, user)
    return _svc(request).update_store_profile(store_id, body)


# ── Recommendations ───────────────────────────────────────────────────────────

@router.get("/recommendations")
async def query_recommendations(
    request: Request,
    store_id: Optional[int] = None,
    sku_ids: Optional[str] = None,
    top_n: int = 5,
    only_reorder: bool = False,
    only_high_priority: bool = False,
    recommendation_type: Optional[str] = None,
    sort_by: str = "expected_profit",
    user: dict = Depends(_auth),
):
    # Enforce store scoping
    sid = store_id or user.get("store_id")
    if sid:
        _require_store(sid, user)

    q = RecommendationQueryRequest(
        store_id=sid,
        sku_ids=[int(x) for x in sku_ids.split(",")] if sku_ids else None,
        top_n=top_n, only_reorder=only_reorder,
        only_high_priority=only_high_priority,
        recommendation_type=recommendation_type,
        sort_by=sort_by,
    )
    return _svc(request).query_recommendations(q)


@router.get("/stores/{store_id}/recommendations")
async def store_recommendations(store_id: int, request: Request, user: dict = Depends(_auth)):
    _require_store(store_id, user)
    return _svc(request).store_recommendations(store_id)


# ── Snapshots / Inventory Ingestion ───────────────────────────────────────────

@router.post("/stores/{store_id}/snapshot")
async def ingest_snapshot(store_id: int, body: InventorySnapshotWriteRequest, request: Request, user: dict = Depends(_auth)):
    _require_store(store_id, user)
    return _svc(request).ingest_store_snapshot(store_id, body)


@router.get("/stores/{store_id}/snapshot")
async def get_latest_snapshot(store_id: int, request: Request, user: dict = Depends(_auth)):
    _require_store(store_id, user)
    return _svc(request).get_store_snapshot(store_id)


# ── AI Agents ─────────────────────────────────────────────────────────────────

@router.post("/explain")
async def explain(request: Request, body: ExplainRequest, user: dict = Depends(_auth)):
    if body.store_id:
        _require_store(body.store_id, user)
    return _svc(request).explain(body)


@router.post("/query")
async def agent_query(request: Request, body: AgentQueryRequest, user: dict = Depends(_auth)):
    if body.store_id:
        _require_store(body.store_id, user)
    return _svc(request).agent_query(body)


# ── Support ───────────────────────────────────────────────────────────────────

@router.post("/support/report")
async def report_issue(request: Request, body: IssueReportCreate, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=400, detail="Store ID required for reporting")
    return _svc(request).report_issue(user["user_id"], sid, body)


# ── Preferences ───────────────────────────────────────────────────────────────

@router.get("/preferences")
async def get_prefs(request: Request, user: dict = Depends(_auth)):
    return _svc(request).get_user_prefs(user["user_id"])


@router.patch("/preferences")
async def update_prefs(request: Request, body: UserPrefsUpdate, user: dict = Depends(_auth)):
    return _svc(request).update_user_prefs(user["user_id"], body)


# ── Finance ───────────────────────────────────────────────────────────────────

@router.get("/finance/overview")
async def get_finance_overview(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).get_finance_overview(int(sid))


@router.get("/finance/udhaar")
async def get_udhaar_list(request: Request, include_recovered: bool = False, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).get_udhaar_list(int(sid), include_recovered)


@router.post("/finance/udhaar/recovery")
async def record_recovery(request: Request, body: UdhaarRecoveryRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).record_udhaar_recovery(int(sid), body.khata_id, body.amount)


@router.post("/finance/udhaar/add")
async def add_udhaar(request: Request, body: UdhaarAddRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).add_udhaar(int(sid), body.customer_name, body.phone, body.amount)


# ── Subscription ──────────────────────────────────────────────────────────────

@router.get("/subscription")
async def get_subscription(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    sub = _svc(request).get_active_subscription(int(sid))
    if sub is None:
        return {"has_active": False}
    return {"has_active": True, **sub}


class _TrialRequest(BaseModel):
    tier: str = "basic"   # "basic" or "pro"

@router.post("/subscription/request-trial")
async def request_trial(request: Request, body: _TrialRequest = _TrialRequest(), user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    tier = body.tier if body.tier in ("basic", "pro") else "basic"
    return _svc(request).request_trial(int(sid), tier)


@router.post("/subscription/cancel")
async def cancel_subscription(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    try:
        return _svc(request).cancel_subscription(int(sid))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/upgrade")
async def upgrade_subscription(request: Request, body: SubscriptionUpgradeRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    try:
        return _svc(request).upgrade_subscription(int(sid), body.tier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/send-reminder")
async def send_subscription_reminder(
    request: Request,
    days_left: int = 0,
    message: str = "",
    user: dict = Depends(_auth),
):
    user_id = user.get("user_id")
    if not user_id:
        return {"sent": False}
    title = "Kirana AI Trial Expiring" if days_left > 0 else "Kirana AI Trial Expired"
    body = message or (
        f"Your trial ends in {days_left} day{'s' if days_left != 1 else ''}. Upgrade to continue."
        if days_left > 0 else
        "Your free trial has ended. Upgrade to keep your store running smoothly."
    )
    sent = _svc(request).send_fcm_to_user(user_id, title, body, data={"action": "open_subscription", "days_left": str(days_left)})
    return {"sent": sent}


# ── Admin — subscription approval ─────────────────────────────────────────────

@router.post("/admin/approve-trial/{store_id}")
async def approve_trial(store_id: int, request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    trial_days = request.app.state.settings.trial_days
    try:
        result = _svc(request).approve_trial(store_id, trial_days)
        # Send FCM to notify the user
        from sqlalchemy import text as _text
        with request.app.state.engine.connect() as conn:
            row = conn.execute(
                _text("SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND role = 'store_owner' LIMIT 1"),
                {"sid": store_id}
            ).mappings().first()
        if row:
            trial_tier = result.get("trial_tier", "basic")
            tier_label = "Pro" if trial_tier == "pro" else "Basic"
            sent = _svc(request).send_fcm_to_user(
                row["user_id"],
                f"Your Kirana AI {tier_label} Trial is Active!",
                f"Your {tier_label} trial has been activated. You have {trial_days} days to explore {tier_label} features.",
                data={"action": "open_subscription"},
            )
            import logging as _log
            _log.getLogger("kirana.routes").info(
                "approve_trial: FCM to user_id=%s sent=%s", row["user_id"], sent
            )
        else:
            import logging as _log
            _log.getLogger("kirana.routes").warning(
                "approve_trial: no store_owner found for store_id=%s — FCM skipped", store_id
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/admin/pending-trials")
async def list_pending_trials(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(_text("""
            SELECT s.store_id, s.started_at, st.name AS store_name,
                   COALESCE(s.requested_tier, 'basic') AS requested_tier
            FROM kirana_oltp.subscription s
            JOIN kirana_oltp.store st ON st.store_id = s.store_id
            WHERE s.tier = 'pending_trial'
            ORDER BY s.started_at DESC
        """)).mappings().all()
    return {"pending": [dict(r) for r in rows]}


@router.get("/kpis/tiers")
async def get_kpi_tiers(request: Request, user: dict = Depends(_auth)):
    """Returns {kpi_id: 'basic'|'pro'} for every KPI in the registry.
    DB config wins; missing entries fall back to the default rule:
    'Core Insight' category → pro, first 3 per other category → basic, rest → pro.
    """
    from kpis import registry as kpi_registry
    db_config = _get_kpi_tier_config(request)
    all_kpis = kpi_registry.all_kpis()
    category_counts: dict[str, int] = {}
    tiers: dict[str, str] = {}
    for kpi in all_kpis:
        if kpi.kpi_id in db_config:
            tiers[kpi.kpi_id] = db_config[kpi.kpi_id]
            continue
        cat = kpi.category
        if cat.lower() in ("core insight", "common"):
            tiers[kpi.kpi_id] = "pro"
        else:
            idx = category_counts.get(cat, 0)
            tiers[kpi.kpi_id] = "basic" if idx < 3 else "pro"
            category_counts[cat] = idx + 1
    return {"tiers": tiers}


@router.get("/admin/kpi-tiers")
async def admin_get_kpi_tiers(request: Request, user: dict = Depends(_auth)):
    """Admin view: all KPIs with their current tier assignment."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from kpis import registry as kpi_registry
    db_config = _get_kpi_tier_config(request)
    all_kpis = kpi_registry.all_kpis()
    category_counts: dict[str, int] = {}
    result = []
    for kpi in all_kpis:
        if kpi.kpi_id in db_config:
            tier = db_config[kpi.kpi_id]
        else:
            cat = kpi.category
            if cat.lower() in ("core insight", "common"):
                tier = "pro"
            else:
                idx = category_counts.get(cat, 0)
                tier = "basic" if idx < 3 else "pro"
                category_counts[cat] = idx + 1
        result.append({
            "kpi_id":   kpi.kpi_id,
            "name":     kpi.name,
            "category": kpi.category,
            "tier":     tier,
            "is_custom": kpi.kpi_id in db_config,
        })
    return {"kpis": result}


@router.put("/admin/kpi-tiers")
async def admin_save_kpi_tiers(request: Request, user: dict = Depends(_auth)):
    """Admin: bulk-save tier assignments. Body: {configs: [{kpi_id, tier}]}"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    configs = body.get("configs", [])
    if not isinstance(configs, list):
        raise HTTPException(status_code=400, detail="configs must be a list")
    for c in configs:
        if c.get("tier") not in ("basic", "pro"):
            raise HTTPException(status_code=400, detail=f"Invalid tier '{c.get('tier')}' for {c.get('kpi_id')}")
    from kirana.repository import KiranaRepository
    KiranaRepository(request.app.state.engine).upsert_kpi_tier_config(configs)
    return {"saved": len(configs)}


def _get_kpi_tier_config(request: Request) -> dict[str, str]:
    from kirana.repository import KiranaRepository
    return KiranaRepository(request.app.state.engine).get_kpi_tier_config()


@router.get("/admin/stats")
async def admin_stats(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text
    with request.app.state.engine.connect() as conn:
        row = conn.execute(_text("""
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
        """)).mappings().first()
    return dict(row)


@router.get("/admin/stores")
async def admin_list_stores(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(_text("""
            SELECT s.store_id, s.name AS store_name, s.location, s.created_at,
                   u.user_id, u.username,
                   COALESCE(u.full_name, u.username) AS owner_name,
                   sub.tier, sub.trial_tier,
                   sub.trial_ends_at, sub.ended_at
            FROM kirana_oltp.store s
            LEFT JOIN kirana_oltp.users u
                ON u.store_id = s.store_id AND NOT COALESCE(u.is_deleted, FALSE)
            LEFT JOIN kirana_oltp.subscription sub ON sub.store_id = s.store_id
            WHERE NOT s.is_deleted
            ORDER BY s.created_at DESC
        """)).mappings().all()
    return {"stores": [dict(r) for r in rows]}


@router.post("/admin/notify")
async def admin_notify(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    title   = body.get("title", "Kirana AI")
    message = body.get("body", "")
    store_id = body.get("store_id")  # null = broadcast to all
    svc = _svc(request)
    from sqlalchemy import text as _text
    with request.app.state.engine.connect() as conn:
        if store_id:
            row = conn.execute(_text(
                "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND NOT COALESCE(is_deleted, FALSE) LIMIT 1"
            ), {"sid": store_id}).mappings().first()
            if not row:
                raise HTTPException(status_code=404, detail="No user found for this store")
            user_ids = [row["user_id"]]
        else:
            rows = conn.execute(_text(
                "SELECT user_id FROM kirana_oltp.users WHERE role = 'store_owner' AND NOT COALESCE(is_deleted, FALSE)"
            )).mappings().all()
            user_ids = [r["user_id"] for r in rows]
    sent = sum(1 for uid in user_ids if svc.send_fcm_to_user(uid, title, message, data={"action": "admin_notify"}))
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
        conn.execute(_text("""
            INSERT INTO kirana_oltp.subscription (store_id, tier, started_at)
            VALUES (:sid, :tier, NOW())
            ON CONFLICT (store_id) DO UPDATE
              SET tier = :tier, started_at = NOW(), ended_at = NULL,
                  is_trial = FALSE, trial_ends_at = NULL
        """), {"sid": store_id, "tier": tier})
        conn.commit()
        owner = conn.execute(_text(
            "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND NOT COALESCE(is_deleted, FALSE) LIMIT 1"
        ), {"sid": store_id}).mappings().first()
    if owner:
        tier_label = "Pro" if tier == "pro" else "Basic"
        _svc(request).send_fcm_to_user(
            owner["user_id"],
            f"Your Kirana AI {tier_label} plan is active!",
            f"Admin activated your {tier_label} plan.",
            data={"action": "open_subscription"},
        )
    return {"success": True}


@router.get("/admin/all-subscriptions")
async def list_all_subscriptions(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text
    with request.app.state.engine.connect() as conn:
        rows = conn.execute(_text("""
            SELECT s.store_id, st.name AS store_name, s.tier,
                   s.started_at, s.ended_at, s.is_trial, s.trial_ends_at
            FROM kirana_oltp.subscription s
            JOIN kirana_oltp.store st ON st.store_id = s.store_id
            ORDER BY s.started_at DESC
        """)).mappings().all()
    return {"subscriptions": [dict(r) for r in rows]}


@router.post("/admin/cancel-subscription/{store_id}")
async def admin_cancel_subscription(store_id: int, request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        return _svc(request).cancel_subscription(store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Store Associations ────────────────────────────────────────────────────────

@router.get("/associations")
async def list_associations(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    from kirana.repository import KiranaRepository
    return {"associations": KiranaRepository(request.app.state.engine).list_associations(int(sid))}


@router.post("/associations")
async def add_association(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    body = await request.json()
    name       = body.get("name", "").strip()
    area_type  = body.get("area_type", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    valid_types = {"apartment", "hostel", "school", "office", "colony"}
    if area_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"area_type must be one of {valid_types}")
    from kirana.repository import KiranaRepository
    result = KiranaRepository(request.app.state.engine).add_association(
        int(sid), name, area_type,
        body.get("estimated_households"),
        body.get("notes"),
    )
    return result


@router.patch("/associations/{association_id}")
async def update_association(association_id: int, request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    body = await request.json()
    from kirana.repository import KiranaRepository
    result = KiranaRepository(request.app.state.engine).update_association(
        association_id, int(sid), **body
    )
    if not result:
        raise HTTPException(status_code=404, detail="Association not found")
    return result


@router.delete("/associations/{association_id}")
async def delete_association(association_id: int, request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    from kirana.repository import KiranaRepository
    ok = KiranaRepository(request.app.state.engine).delete_association(association_id, int(sid))
    if not ok:
        raise HTTPException(status_code=404, detail="Association not found")
    return {"deleted": True}


@router.get("/associations/heatmap")
async def get_association_heatmap(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    from kirana.repository import KiranaRepository
    return {"heatmap": KiranaRepository(request.app.state.engine).get_association_heatmap(int(sid))}


# ── Basket Campaigns ──────────────────────────────────────────────────────────

@router.get("/campaigns/recommended")
async def get_recommended_campaigns(
    request: Request,
    store_id: int = 0,
    limit: int = 3,
    user: dict = Depends(_auth),
):
    """Returns top campaigns: general time-based + area-specific from associations."""
    sid = store_id or user.get("store_id") or 0
    if not sid:
        raise HTTPException(status_code=400, detail="store_id required")
    from kirana.campaigns import get_recommended_campaigns as _recommend, get_area_campaigns
    from kirana.repository import KiranaRepository

    # General time/season campaigns
    general = _recommend(request.app.state.engine, int(sid), limit=min(limit, 5))

    # Area-specific campaigns from this store's associations
    associations = KiranaRepository(request.app.state.engine).list_associations(int(sid))
    active_types = list({a["area_type"] for a in associations if a.get("is_active")})
    area = get_area_campaigns(request.app.state.engine, int(sid), active_types) if active_types else []

    # Merge: area campaigns first (they're more targeted), then general; deduplicate by campaign_id
    seen: set[str] = set()
    merged = []
    for c in [*area, *general]:
        if c["campaign_id"] not in seen:
            seen.add(c["campaign_id"])
            merged.append(c)

    return {"campaigns": merged[:min(limit + 2, 8)]}


# ── Payments ──────────────────────────────────────────────────────────────────

@router.post("/payment/create-order")
async def create_payment_order(request: Request, body: PaymentOrderRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    s = request.app.state.settings
    s = request.app.state.settings
    prices = {"basic": s.basic_price_inr, "pro": s.pro_price_inr}
    if body.tier not in prices:
        raise HTTPException(status_code=400, detail="Invalid tier")
    # If Razorpay keys not configured, return test-mode placeholder
    if not s.razorpay_key_id or not s.razorpay_key_secret:
        return {
            "mode": "test",
            "order_id": f"test_order_{body.tier}",
            "amount": prices[body.tier] * 100,
            "currency": "INR",
            "key_id": "",
            "tier": body.tier,
        }
    try:
        return {**_svc(request).create_razorpay_order(int(sid), body.tier), "mode": "live"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/payment/mock-confirm")
async def mock_confirm_payment(request: Request, body: PaymentOrderRequest, user: dict = Depends(_auth)):
    """Directly upgrades subscription — only for test/dev mode. Blocked in production."""
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    s = request.app.state.settings
    # Block mock-confirm when Google Play credentials are configured (live mode)
    if s.google_play_credentials_json and s.google_play_package_name:
        raise HTTPException(status_code=403, detail="Mock payments disabled in live mode")
    try:
        result = _svc(request).upgrade_subscription(int(sid), body.tier)
        user_id = user.get("user_id")
        if user_id:
            tier_name = "Pro" if body.tier == "pro" else "Basic"
            _svc(request).send_fcm_to_user(
                user_id, f"Welcome to Kirana AI {tier_name}!",
                f"Your {tier_name} plan is now active. Enjoy!",
                data={"action": "open_subscription"},
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/payment/verify-iap")
async def verify_iap_payment(request: Request, user: dict = Depends(_auth)):
    """Verify a Google Play IAP purchase and activate the subscription.

    Optional server-side verification with Google Play Developer API when
    GOOGLE_PLAY_CREDENTIALS_JSON is set in .env. Without credentials, the
    purchase token is trusted (acceptable for testing; add credentials before
    going live).
    """
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")

    body = await request.json()
    tier           = body.get("tier", "")
    product_id     = body.get("product_id", "")
    purchase_token = body.get("purchase_token", "")

    if tier not in ("basic", "pro"):
        raise HTTPException(status_code=400, detail="Invalid tier")
    if not purchase_token:
        raise HTTPException(status_code=400, detail="purchase_token required")

    s = request.app.state.settings

    # Optional: verify with Google Play Developer API
    if s.google_play_credentials_json and s.google_play_package_name:
        try:
            import json as _json
            from google.oauth2 import service_account as _sa
            from googleapiclient.discovery import build as _build

            creds_path = s.google_play_credentials_json
            if not creds_path.startswith("{"):
                import os as _os
                with open(creds_path) as f:
                    creds_data = _json.load(f)
            else:
                creds_data = _json.loads(creds_path)

            creds = _sa.Credentials.from_service_account_info(
                creds_data,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )
            service = _build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
            result  = service.purchases().subscriptions().get(
                packageName=s.google_play_package_name,
                subscriptionId=product_id,
                token=purchase_token,
            ).execute()

            # paymentState: 1=received, 2=free trial, 0=pending
            if result.get("paymentState", 0) not in (1, 2):
                raise HTTPException(status_code=402, detail="Payment not yet confirmed by Google Play")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Play verification error: {exc}") from exc

    # Activate subscription
    result = _svc(request).upgrade_subscription(int(sid), tier)

    user_id = user.get("user_id")
    if user_id:
        tier_name = "Pro" if tier == "pro" else "Basic"
        try:
            _svc(request).send_fcm_to_user(
                user_id,
                f"Welcome to Kirana AI {tier_name}!",
                f"Your {tier_name} plan is now active. Enjoy all the features!",
                data={"action": "open_subscription"},
            )
        except Exception:
            pass

    return result


@router.post("/payment/verify")
async def verify_payment(request: Request, body: PaymentVerifyRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    s = request.app.state.settings
    if not s.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")
    try:
        result = _svc(request).verify_razorpay_payment(
            int(sid), body.tier,
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature,
        )
        # Send FCM confirmation
        user_id = user.get("user_id")
        if user_id:
            tier_name = "Pro" if body.tier == "pro" else "Basic"
            _svc(request).send_fcm_to_user(
                user_id,
                f"Welcome to Kirana AI {tier_name}!",
                f"Your {tier_name} subscription is now active. Enjoy all features!",
                data={"action": "open_subscription"},
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/customers")
async def list_customers_segments(request: Request, store_id: int, user: dict = Depends(_auth)):
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"customers": _svc(request).list_customers_with_segments(store_id)}


@router.post("/finance/customers/sync")
async def sync_customers(request: Request, body: List[CustomerSyncItem] | CustomerSyncRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    
    # Handle both bare list and wrapped object for backward compatibility
    contacts = body.contacts if isinstance(body, CustomerSyncRequest) else body
    count = _svc(request).sync_customers(int(sid), [c.model_dump() for c in contacts])
    return {"synced": count}


@router.post("/finance/udhaar/remind")
async def remind_udhaar(request: Request, body: UdhaarRemindRequest, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    wa_client = request.app.state.wa_client
    return _svc(request).send_udhaar_reminder(int(sid), body.khata_id, wa_client)


# ── Cashflow Support ──────────────────────────────────────────────────────────

@router.post("/cashflow/request")
async def create_cashflow_request(
    request: Request,
    body: CashflowRequestCreate,
    user: dict = Depends(_auth),
):
    user_id = user.get("user_id")
    store_id = body.store_id
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    result = _svc(request).create_cashflow_request(
        store_id=store_id,
        user_id=user_id,
        amount=body.amount_requested,
        selected_bank=body.selected_bank,
    )
    return {
        "request_id": result["request_id"],
        "status": result["status"],
        "message": "We've received your request! Our team will contact you within 2 business days.",
    }


@router.get("/cashflow/status")
async def get_cashflow_status(
    request: Request,
    store_id: int,
    user: dict = Depends(_auth),
):
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return _svc(request).get_cashflow_status(store_id)


# ── Referral Marketing ────────────────────────────────────────────────────────

@router.post("/referral/campaigns")
async def create_campaign(request: Request, body: ReferralCampaignCreate, user: dict = Depends(_auth)):
    if user.get("role") != "admin" and user.get("store_id") != body.store_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _svc(request).create_referral_campaign(
        body.store_id, body.name, body.referral_discount_pct,
        body.milestone_every_n, body.milestone_reward_pct,
        body.max_referrals_per_referrer)

@router.get("/referral/campaigns")
async def list_campaigns(request: Request, store_id: int, user: dict = Depends(_auth)):
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"campaigns": _svc(request).list_referral_campaigns(store_id)}

@router.patch("/referral/campaigns/{campaign_id}/toggle")
async def toggle_campaign(campaign_id: int, is_active: bool, request: Request, user: dict = Depends(_auth)):
    return _svc(request).toggle_referral_campaign(campaign_id, is_active)

@router.post("/referral/token")
async def get_referral_token(request: Request, body: ReferralTokenRequest, user: dict = Depends(_auth)):
    result = _svc(request).get_or_create_referral_token(body.store_id, body.customer_id, body.campaign_id)
    return result

@router.get("/referral/token-info")
async def token_info(request: Request, token: str, user: dict = Depends(_auth)):
    info = _svc(request).get_token_info(token)
    if not info:
        raise HTTPException(status_code=404, detail="Token not found")
    return info

@router.post("/referral/scan")
async def process_referral(request: Request, body: ReferralScanRequest, user: dict = Depends(_auth)):
    try:
        return _svc(request).process_referral(body.token_hash, body.new_customer_phone, body.new_customer_name, body.order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/referral/vouchers")
async def get_vouchers(request: Request, customer_id: int, store_id: int, user: dict = Depends(_auth)):
    return {"vouchers": _svc(request).get_pending_vouchers(customer_id, store_id)}

@router.post("/referral/vouchers/use")
async def use_voucher(request: Request, body: VoucherUseRequest, user: dict = Depends(_auth)):
    ok = _svc(request).use_voucher(body.voucher_id, body.order_id)
    return {"success": ok}


# ── Intelligence layer ────────────────────────────────────────────────────────

class CartPingRequest(BaseModel):
    item_count: int = 0
    items: list = []
    converted: bool = False   # True when an order was just completed


class NotificationOpenedRequest(BaseModel):
    log_id: int


@router.post("/intelligence/cart-ping")
async def cart_ping(request: Request, body: CartPingRequest, user: dict = Depends(_auth)):
    """Flutter calls this every time the cart changes (debounced)."""
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    store_id = int(sid)

    from kirana.intelligence.repository import IntelligenceRepository
    repo = IntelligenceRepository(request.app.state.engine)

    if body.converted:
        repo.mark_cart_converted(store_id)
    else:
        repo.upsert_cart_session(store_id, body.item_count, body.items)
    return {"ok": True}


@router.post("/intelligence/notification-opened")
async def notification_opened(request: Request, body: NotificationOpenedRequest, user: dict = Depends(_auth)):
    """Flutter calls this when the user taps a push notification."""
    from kirana.intelligence.repository import IntelligenceRepository
    repo = IntelligenceRepository(request.app.state.engine)
    repo.mark_opened(body.log_id)
    return {"ok": True}


@router.get("/intelligence/logs")
async def intelligence_logs(request: Request, limit: int = 50, user: dict = Depends(_auth)):
    """Returns recent intelligence notifications for this store (or all stores for admin)."""
    from kirana.intelligence.repository import IntelligenceRepository
    repo = IntelligenceRepository(request.app.state.engine)

    if user.get("role") == "admin":
        logs = repo.list_all_logs(limit=min(limit, 500))
    else:
        sid = user.get("store_id")
        if not sid:
            raise HTTPException(status_code=403, detail="Store owner login required")
        logs = repo.list_logs(int(sid), limit=min(limit, 100))

    return {"logs": logs, "count": len(logs)}


@router.post("/admin/intelligence/fire/{trigger_name}")
async def fire_trigger(trigger_name: str, request: Request, user: dict = Depends(_auth)):
    """Manually fire an intelligence trigger immediately. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    engine: "IntelligenceEngine" = request.app.state.intelligence
    result = await engine.fire(trigger_name)
    return result
