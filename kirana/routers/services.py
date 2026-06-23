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


# ── Service catalogue ─────────────────────────────────────────────────────────


@router.get("/services")
async def list_services(request: Request, user: dict = Depends(_auth)):
    inc = request.query_params.get("include_inactive") == "true"
    return {"services": _repo(request).list_services(_store_id(user), include_inactive=inc)}


@router.post("/services")
async def create_service(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name required")
    return _repo(request).create_service(
        _store_id(user),
        name=body["name"],
        price=body.get("price") or 0,
        duration_min=body.get("duration_min") or 30,
        category=body.get("category"),
    )


@router.patch("/services/{service_id}")
async def update_service(service_id: int, request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    res = _repo(request).update_service(
        service_id, _store_id(user),
        name=body.get("name"), price=body.get("price"),
        duration_min=body.get("duration_min"), category=body.get("category"),
        is_active=body.get("is_active"),
    )
    if not res:
        raise HTTPException(status_code=404, detail="Service not found")
    return res


# ── Appointments ──────────────────────────────────────────────────────────────


@router.get("/appointments")
async def list_appointments(request: Request, user: dict = Depends(_auth)):
    qp = request.query_params
    return {"appointments": _repo(request).list_appointments(
        _store_id(user), day=qp.get("day"),
        date_from=qp.get("from"), date_to=qp.get("to"))}


@router.post("/appointments")
async def create_appointment(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    if not body.get("starts_at"):
        raise HTTPException(status_code=400, detail="starts_at required")
    return _repo(request).create_appointment(
        _store_id(user),
        body["starts_at"],
        service_id=body.get("service_id"),
        customer_id=body.get("customer_id"),
        customer_name=body.get("customer_name"),
        customer_phone=body.get("customer_phone"),
        staff_user_id=body.get("staff_user_id"),
        duration_min=body.get("duration_min"),
        price=body.get("price"),
        notes=body.get("notes"),
    )


@router.patch("/appointments/{appointment_id}")
async def update_appointment(appointment_id: int, request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    status = body.get("status")
    if status not in ("booked", "completed", "cancelled", "no_show"):
        raise HTTPException(status_code=400, detail="invalid status")
    res = _repo(request).update_appointment_status(
        appointment_id, _store_id(user), status, body.get("order_id"))
    if not res:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return res


@router.get("/appointments/utilisation")
async def appointment_utilisation(request: Request, days: int = 30, user: dict = Depends(_auth)):
    return _repo(request).appointment_utilisation(_store_id(user), days)


# ── Memberships ───────────────────────────────────────────────────────────────


@router.get("/memberships")
async def list_memberships(request: Request, user: dict = Depends(_auth)):
    cid = request.query_params.get("customer_id")
    return {"memberships": _repo(request).list_memberships(
        _store_id(user), int(cid) if cid else None)}


@router.post("/memberships")
async def create_membership(request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    if not body.get("customer_id") or not body.get("name"):
        raise HTTPException(status_code=400, detail="customer_id and name required")
    return _repo(request).create_membership(
        _store_id(user),
        customer_id=int(body["customer_id"]),
        name=body["name"],
        total_sessions=body.get("total_sessions") or 0,
        price=body.get("price") or 0,
        valid_until=body.get("valid_until"),
    )


@router.post("/memberships/{membership_id}/use")
async def use_membership(membership_id: int, request: Request, user: dict = Depends(_auth)):
    res = _repo(request).use_membership_session(membership_id, _store_id(user))
    if not res:
        raise HTTPException(status_code=400, detail="No sessions left or membership inactive")
    return res


@router.get("/services/revenue")
async def service_revenue(request: Request, days: int = 30, user: dict = Depends(_auth)):
    return _repo(request).service_revenue(_store_id(user), days)
