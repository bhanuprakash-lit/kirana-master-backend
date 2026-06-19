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
    pass

from kirana.schemas import (
    ReferralCampaignCreate,
    ReferralTokenRequest,
    ReferralScanRequest,
    VoucherUseRequest,
)
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


# ── Referral Marketing ────────────────────────────────────────────────────────


@router.post("/referral/campaigns")
async def create_campaign(
    request: Request, body: ReferralCampaignCreate, user: dict = Depends(_auth)
):
    if user.get("role") != "admin" and user.get("store_id") != body.store_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _svc(request).create_referral_campaign(
        body.store_id,
        body.name,
        body.referral_discount_pct,
        body.milestone_every_n,
        body.milestone_reward_pct,
        body.max_referrals_per_referrer,
    )


@router.get("/referral/campaigns")
async def list_campaigns(request: Request, store_id: int, user: dict = Depends(_auth)):
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"campaigns": _svc(request).list_referral_campaigns(store_id)}


@router.patch("/referral/campaigns/{campaign_id}/toggle")
async def toggle_campaign(
    campaign_id: int, is_active: bool, request: Request, user: dict = Depends(_auth)
):
    return _svc(request).toggle_referral_campaign(campaign_id, is_active)


@router.post("/referral/token")
async def get_referral_token(
    request: Request, body: ReferralTokenRequest, user: dict = Depends(_auth)
):
    result = _svc(request).get_or_create_referral_token(
        body.store_id, body.customer_id, body.campaign_id
    )
    return result


@router.get("/referral/token-info")
async def token_info(request: Request, token: str, user: dict = Depends(_auth)):
    info = _svc(request).get_token_info(token)
    if not info:
        raise HTTPException(status_code=404, detail="Token not found")
    return info


@router.post("/referral/scan")
async def process_referral(
    request: Request, body: ReferralScanRequest, user: dict = Depends(_auth)
):
    try:
        return _svc(request).process_referral(
            body.token_hash,
            body.new_customer_phone,
            body.new_customer_name,
            body.order_id,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid referral token")


@router.get("/referral/vouchers")
async def get_vouchers(
    request: Request, customer_id: int, store_id: int, user: dict = Depends(_auth)
):
    return {"vouchers": _svc(request).get_pending_vouchers(customer_id, store_id)}


@router.post("/referral/vouchers/use")
async def use_voucher(
    request: Request, body: VoucherUseRequest, user: dict = Depends(_auth)
):
    ok = _svc(request).use_voucher(body.voucher_id, body.order_id)
    return {"success": ok}
