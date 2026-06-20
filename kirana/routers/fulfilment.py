import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from kirana.service import KiranaService

logger = logging.getLogger(__name__)
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


def _repo(request: Request):
    from kirana.repositories.main import KiranaRepository
    return KiranaRepository(request.app.state.engine)


def _sid(user: dict) -> int:
    if not user.get("store_id"):
        raise HTTPException(status_code=403, detail="Store owner login required")
    return int(user["store_id"])


# ── Estimates ─────────────────────────────────────────────────────────────────
@router.get("/estimates")
async def list_estimates(request: Request, user: dict = Depends(_auth)):
    return {"estimates": _repo(request).list_estimates(_sid(user))}


@router.get("/estimates/{estimate_id}")
async def get_estimate(estimate_id: int, request: Request, user: dict = Depends(_auth)):
    res = _repo(request).get_estimate(estimate_id, _sid(user))
    if not res:
        raise HTTPException(status_code=404, detail="Estimate not found")
    return res


@router.post("/estimates")
async def create_estimate(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    items = b.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="items required")
    return _repo(request).create_estimate(
        _sid(user), items, customer_id=b.get("customer_id"),
        customer_name=b.get("customer_name"), valid_until=b.get("valid_until"))


@router.patch("/estimates/{estimate_id}")
async def set_estimate_status(estimate_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not _repo(request).set_estimate_status(
            estimate_id, _sid(user), b.get("status") or "sent", b.get("order_id")):
        raise HTTPException(status_code=404, detail="Estimate not found")
    return {"updated": True}


# ── Customer returns / exchanges ─────────────────────────────────────────────
@router.get("/sales-returns")
async def list_returns(request: Request, days: int = 90, user: dict = Depends(_auth)):
    return {"returns": _repo(request).list_sales_returns(_sid(user), days)}


@router.post("/sales-returns")
async def create_return(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    return _repo(request).create_sales_return(
        _sid(user), order_id=b.get("order_id"), customer_id=b.get("customer_id"),
        reason=b.get("reason"), refund_amount=b.get("refund_amount") or 0,
        is_exchange=bool(b.get("is_exchange")), notes=b.get("notes"))


# ── Delivery ──────────────────────────────────────────────────────────────────
@router.patch("/orders/{order_id}/delivery")
async def set_delivery(order_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not _repo(request).set_delivery_status(order_id, _sid(user), b.get("status") or "pending"):
        raise HTTPException(status_code=404, detail="Order not found")
    return {"updated": True}
