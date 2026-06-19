import logging
from typing import TYPE_CHECKING
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

from kirana.schemas import (
    UserPrefsUpdate,
)
from kirana.service import KiranaService

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


def _require_admin(user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _require_store(store_id: int, user: dict = Depends(_auth)):
    if user.get("role") == "admin":
        return user
    if user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return user


# ── Users ─────────────────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(request: Request, admin: dict = Depends(_require_admin)):
    return _svc(request).list_users()


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int, request: Request, admin: dict = Depends(_require_admin)
):
    ok = _svc(request).delete_user(user_id)
    return {"deleted": ok}


# ── Preferences ───────────────────────────────────────────────────────────────


@router.get("/preferences")
async def get_prefs(request: Request, user: dict = Depends(_auth)):
    return _svc(request).get_user_prefs(user["user_id"])


@router.patch("/preferences")
async def update_prefs(
    request: Request, body: UserPrefsUpdate, user: dict = Depends(_auth)
):
    return _svc(request).update_user_prefs(user["user_id"], body)
