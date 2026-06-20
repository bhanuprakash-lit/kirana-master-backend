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


@router.get("/serials")
async def list_serials(request: Request, user: dict = Depends(_auth)):
    qp = request.query_params
    pid = qp.get("product_id")
    return {"serials": _repo(request).list_serials(
        _sid(user), int(pid) if pid else None, qp.get("status"))}


@router.post("/serials")
async def add_serial(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not b.get("product_id") or not b.get("serial_no"):
        raise HTTPException(status_code=400, detail="product_id and serial_no required")
    return _repo(request).add_serial(
        _sid(user), int(b["product_id"]), b["serial_no"],
        variant_id=b.get("variant_id"), warranty_until=b.get("warranty_until"))


@router.post("/serials/sold")
async def mark_sold(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not b.get("serial_no"):
        raise HTTPException(status_code=400, detail="serial_no required")
    ok = _repo(request).mark_serial_sold(
        _sid(user), b["serial_no"], b.get("order_id"), b.get("customer_id"))
    if not ok:
        raise HTTPException(status_code=404, detail="Serial not found")
    return {"updated": True}


@router.get("/warranty-claims")
async def list_claims(request: Request, user: dict = Depends(_auth)):
    return {"claims": _repo(request).list_claims(_sid(user))}


@router.post("/warranty-claims")
async def create_claim(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    return _repo(request).create_claim(
        _sid(user), product_id=b.get("product_id"), serial_id=b.get("serial_id"),
        customer_id=b.get("customer_id"), issue=b.get("issue"))


@router.patch("/warranty-claims/{claim_id}")
async def set_claim(claim_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    status = b.get("status")
    if status not in ("open", "resolved", "rejected"):
        raise HTTPException(status_code=400, detail="invalid status")
    if not _repo(request).set_claim_status(claim_id, _sid(user), status):
        raise HTTPException(status_code=404, detail="Claim not found")
    return {"updated": True}
