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


@router.get("/products/{product_id}/locations")
async def list_locations(product_id: int, request: Request, user: dict = Depends(_auth)):
    return {"locations": _repo(request).list_locations(_sid(user), product_id)}


@router.post("/products/{product_id}/locations")
async def upsert_location(product_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    if not b.get("rack"):
        raise HTTPException(status_code=400, detail="rack required")
    return _repo(request).upsert_location(
        _sid(user), product_id, b["rack"], b.get("quantity") or 0, b.get("variant_id"))


@router.delete("/locations/{location_id}")
async def delete_location(location_id: int, request: Request, user: dict = Depends(_auth)):
    if not _repo(request).delete_location(location_id, _sid(user)):
        raise HTTPException(status_code=404, detail="Location not found")
    return {"deleted": True}


@router.get("/racks")
async def find_by_rack(request: Request, q: str = "", user: dict = Depends(_auth)):
    return {"items": _repo(request).find_by_rack(_sid(user), q)}
