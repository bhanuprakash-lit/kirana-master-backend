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


def _store_id(user: dict) -> int:
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return int(sid)


# ── Loyalty config ────────────────────────────────────────────────────────────


@router.get("/loyalty/config")
async def get_loyalty_config(request: Request, user: dict = Depends(_auth)):
    return _repo(request).get_loyalty_config(_store_id(user))


@router.put("/loyalty/config")
async def save_loyalty_config(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    return _repo(request).upsert_loyalty_config(
        _store_id(user),
        is_active=body.get("is_active"),
        points_per_100=body.get("points_per_100"),
        redeem_paise_per_point=body.get("redeem_paise_per_point"),
        silver_threshold=body.get("silver_threshold"),
        gold_threshold=body.get("gold_threshold"),
    )


# ── Points ────────────────────────────────────────────────────────────────────


@router.get("/customers/{customer_id}/loyalty")
async def customer_loyalty(customer_id: int, request: Request, user: dict = Depends(_auth)):
    return _repo(request).get_customer_loyalty(_store_id(user), customer_id)


@router.post("/loyalty/redeem")
async def redeem_points(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    cid = body.get("customer_id")
    points = body.get("points")
    if not cid or not points:
        raise HTTPException(status_code=400, detail="customer_id and points required")
    try:
        return _repo(request).redeem_points(
            _store_id(user), int(cid), float(points), body.get("order_id")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Coupons ───────────────────────────────────────────────────────────────────


@router.get("/coupons")
async def list_coupons(request: Request, user: dict = Depends(_auth)):
    return {"coupons": _repo(request).list_coupons(_store_id(user))}


@router.post("/coupons")
async def create_coupon(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    if not body.get("code") or body.get("value") is None:
        raise HTTPException(status_code=400, detail="code and value required")
    if body.get("discount_type") not in ("percent", "flat"):
        raise HTTPException(status_code=400, detail="discount_type must be percent or flat")
    return _repo(request).create_coupon(
        _store_id(user),
        code=body["code"],
        discount_type=body["discount_type"],
        value=body["value"],
        min_order=body.get("min_order") or 0,
        max_discount=body.get("max_discount"),
        valid_from=body.get("valid_from"),
        valid_to=body.get("valid_to"),
        usage_limit=body.get("usage_limit"),
    )


@router.patch("/coupons/{coupon_id}")
async def update_coupon(coupon_id: int, request: Request, user: dict = Depends(_auth)):
    """Edit a coupon, or just flip its active switch.

    Historically this only accepted `is_active`; PAI-17 added full editing.
    A body carrying only `is_active` keeps the old cheap path so existing
    clients behave exactly as before.
    """
    body = await request.json()
    store_id = _store_id(user)
    fields = {k: body[k] for k in (
        "code", "discount_type", "value", "min_order", "max_discount",
        "valid_from", "valid_to", "usage_limit", "is_active",
    ) if k in body}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if fields.get("discount_type") not in (None, "percent", "flat"):
        raise HTTPException(status_code=400, detail="discount_type must be percent or flat")
    if set(fields) == {"is_active"}:
        ok = _repo(request).set_coupon_active(
            coupon_id, store_id, bool(fields["is_active"])
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Coupon not found")
        return {"updated": True}
    row = _repo(request).update_coupon(coupon_id, store_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return row


@router.get("/coupons/applicable")
async def applicable_coupons(request: Request, amount: float = 0,
                             user: dict = Depends(_auth)):
    """Coupons the current bill already qualifies for (PAI-16)."""
    return {"coupons": _repo(request).applicable_coupons(_store_id(user), float(amount or 0))}


@router.post("/coupons/validate")
async def validate_coupon(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="code required")
    return _repo(request).validate_coupon(
        _store_id(user), code, float(body.get("order_amount") or 0)
    )


# ── Admin overview ────────────────────────────────────────────────────────────


@router.get("/admin/loyalty/overview")
async def admin_loyalty_overview(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"stores": _repo(request).loyalty_admin_overview()}


# ── Occasions ─────────────────────────────────────────────────────────────────


@router.get("/loyalty/offers-due")
async def offers_due(request: Request, days: int = 7, user: dict = Depends(_auth)):
    return {"customers": _repo(request).offers_due(_store_id(user), days)}
