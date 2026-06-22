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


@router.post("/products/{product_id}/tax")
async def set_product_tax(product_id: int, request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    return _repo(request).set_product_tax(
        product_id, body.get("hsn_code"), body.get("gst_rate")
    )


@router.get("/tax/gst-summary")
async def gst_summary(request: Request, date_from: str, date_to: str,
                      user: dict = Depends(_auth)):
    """GSTR-style GST summary for a period (per-rate slab breakup + totals)."""
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _repo(request).gst_summary(int(sid), date_from, date_to)


@router.get("/tax-rules")
async def list_tax_rules(request: Request, user: dict = Depends(_auth)):
    return {"rules": _repo(request).list_tax_rules(user.get("store_id") or 0)}


@router.post("/tax-rules")
async def create_tax_rule(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    body = await request.json()
    if body.get("gst_rate") is None:
        raise HTTPException(status_code=400, detail="gst_rate is required")
    return _repo(request).create_tax_rule(
        int(sid),
        gst_rate=body["gst_rate"],
        category_id=body.get("category_id"),
        hsn_code=body.get("hsn_code"),
        min_price=body.get("min_price"),
        max_price=body.get("max_price"),
    )


@router.delete("/tax-rules/{rule_id}")
async def delete_tax_rule(rule_id: int, request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    if not _repo(request).delete_tax_rule(rule_id, int(sid)):
        raise HTTPException(status_code=404, detail="Tax rule not found")
    return {"deleted": True}
