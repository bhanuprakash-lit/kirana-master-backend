from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request

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
)
from kirana.service import KiranaService

router = APIRouter(prefix="/kirana", tags=["Kirana AI"])

def _svc(request: Request) -> KiranaService:
    return request.app.state.kirana_service

def _auth(request: Request):
    token = request.headers.get("Authorization")
    if not token or not token.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = token[len("Bearer "):]
    user = _svc(request).user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


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
