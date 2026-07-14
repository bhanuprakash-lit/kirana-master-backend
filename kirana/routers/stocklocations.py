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
    if not b.get("rack") and not b.get("rack_id"):
        raise HTTPException(status_code=400, detail="rack or rack_id required")
    repo = _repo(request)
    # Guard the FK up-front: a bad product_id would otherwise raise an IntegrityError
    # (now caught globally, but a 404 here is precise for the client).
    if not repo.product_exists(product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    row = repo.upsert_location(
        _sid(user), product_id, b.get("rack"), b.get("quantity") or 0,
        b.get("variant_id"), b.get("rack_id"))
    if row is None:
        raise HTTPException(status_code=404, detail="Rack not found")
    return row


@router.delete("/locations/{location_id}")
async def delete_location(location_id: int, request: Request, user: dict = Depends(_auth)):
    if not _repo(request).delete_location(location_id, _sid(user)):
        raise HTTPException(status_code=404, detail="Location not found")
    return {"deleted": True}


@router.get("/racks")
async def find_by_rack(request: Request, q: str = "", user: dict = Depends(_auth)):
    return {"items": _repo(request).find_by_rack(_sid(user), q)}


@router.get("/racks/all")
async def list_all_racks(request: Request, user: dict = Depends(_auth)):
    return {"items": _repo(request).list_all_locations(_sid(user))}


@router.get("/racks/list")
async def list_racks(request: Request, user: dict = Depends(_auth)):
    """First-class racks (including empty ones) with placement counts."""
    return {"racks": _repo(request).list_racks(_sid(user))}


@router.post("/racks")
async def create_rack(request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    rack = _repo(request).create_rack(_sid(user), (b.get("label") or ""))
    if rack is None:
        raise HTTPException(status_code=400, detail="label required")
    # created=False means the normalized label already existed — the existing
    # rack is returned so the client can just use it.
    return rack


@router.patch("/racks/{rack_id}")
async def rename_rack(rack_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    res = _repo(request).rename_rack(_sid(user), rack_id, (b.get("label") or ""))
    if res == "conflict":
        raise HTTPException(status_code=409, detail="rack_exists")
    if res is None:
        raise HTTPException(status_code=404, detail="Rack not found")
    return res


@router.delete("/racks/{rack_id}")
async def delete_rack(rack_id: int, request: Request, user: dict = Depends(_auth)):
    res = _repo(request).delete_rack(_sid(user), rack_id)
    if res == "not_found":
        raise HTTPException(status_code=404, detail="Rack not found")
    if res == "not_empty":
        raise HTTPException(status_code=409, detail="rack_not_empty")
    return {"deleted": True}


@router.post("/racks/{rack_id}/merge")
async def merge_racks(rack_id: int, request: Request, user: dict = Depends(_auth)):
    """Merge this rack's placements into target_rack_id and delete this rack."""
    b = await request.json()
    target = b.get("target_rack_id")
    if not target:
        raise HTTPException(status_code=400, detail="target_rack_id required")
    res = _repo(request).merge_racks(_sid(user), rack_id, int(target))
    if res is None:
        raise HTTPException(status_code=404, detail="Rack not found")
    return res
