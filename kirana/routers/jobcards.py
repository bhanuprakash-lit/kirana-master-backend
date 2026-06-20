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


@router.get("/job-cards")
async def list_job_cards(request: Request, user: dict = Depends(_auth)):
    qp = request.query_params
    return {"job_cards": _repo(request).list_job_cards(
        _sid(user), status=qp.get("status"), job_type=qp.get("type"))}


@router.post("/job-cards")
async def create_job_card(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    return _repo(request).create_job_card(
        _sid(user), job_type=b.get("job_type") or "repair",
        customer_id=b.get("customer_id"), customer_name=b.get("customer_name"),
        customer_phone=b.get("customer_phone"), item_desc=b.get("item_desc"),
        details=b.get("details"), charge=b.get("charge"),
        promised_date=b.get("promised_date"))


@router.patch("/job-cards/{job_id}")
async def set_job_status(job_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    status = b.get("status")
    if status not in ("received", "in_progress", "ready", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="invalid status")
    if not _repo(request).set_job_status(job_id, _sid(user), status):
        raise HTTPException(status_code=404, detail="Job card not found")
    return {"updated": True}
