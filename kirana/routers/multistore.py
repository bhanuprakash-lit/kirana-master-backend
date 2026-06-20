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


# ── Owner rollup ──────────────────────────────────────────────────────────────


@router.get("/stores/rollup")
async def store_rollup(request: Request, days: int = 30, user: dict = Depends(_auth)):
    """Per-store + per-city/region comparison across the caller's store group.
    Single-store owners get a one-row rollup (is_multi_store = false)."""
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _repo(request).store_rollup(int(sid), days)


# ── Admin group management ────────────────────────────────────────────────────


@router.post("/admin/store-groups")
async def create_store_group(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name required")
    return _repo(request).create_store_group(
        body["name"], body.get("owner_user_id"), body.get("store_ids") or []
    )


@router.post("/admin/stores/{store_id}/group")
async def assign_store_group(store_id: int, request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    ok = _repo(request).assign_store_to_group(store_id, body.get("group_id"))
    if not ok:
        raise HTTPException(status_code=404, detail="Store not found")
    return {"updated": True}
