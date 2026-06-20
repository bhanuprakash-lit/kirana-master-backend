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


@router.get("/customers/{customer_id}/wishlist")
async def list_wishlist(customer_id: int, request: Request, user: dict = Depends(_auth)):
    return {"wishlist": _repo(request).list_wishlist(_sid(user), customer_id)}


@router.post("/customers/{customer_id}/wishlist")
async def add_wishlist(customer_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    return _repo(request).add_wishlist(
        _sid(user), customer_id, product_id=b.get("product_id"), note=b.get("note"))


@router.delete("/wishlist/{item_id}")
async def remove_wishlist(item_id: int, request: Request, user: dict = Depends(_auth)):
    if not _repo(request).remove_wishlist(item_id, _sid(user)):
        raise HTTPException(status_code=404, detail="Wishlist item not found")
    return {"deleted": True}


@router.get("/customers/{customer_id}/profile")
async def get_profile(customer_id: int, request: Request, user: dict = Depends(_auth)):
    return _repo(request).get_customer_profile(_sid(user), customer_id)


@router.patch("/customers/{customer_id}/profile")
async def update_profile(customer_id: int, request: Request, user: dict = Depends(_auth)):
    b = await request.json()
    return _repo(request).update_customer_profile(
        _sid(user), customer_id,
        prescription=b.get("prescription"),
        style_profile=b.get("style_profile"),
        size_profile=b.get("size_profile"))
