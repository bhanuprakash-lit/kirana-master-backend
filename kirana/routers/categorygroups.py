"""Per-store category groups (G7) — see repositories/category_groups.py.

Every route is scoped to the caller's own store. Group ids are never trusted
from the client: each mutation matches on `(group_id, store_id)`, so one store
cannot rename or delete another's group, and the shared per-vertical templates
(`store_id IS NULL`) are unreachable from here — they fork on first write.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from kirana.repositories import category_groups as repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kirana", tags=["Kirana AI"])


def _auth(request: Request):
    s = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer ") :] if auth_hdr.startswith("Bearer ") else ""
    if api_key and api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = request.app.state.kirana_service.user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _sid(user: dict) -> int:
    if not user.get("store_id"):
        raise HTTPException(status_code=403, detail="Store owner login required")
    return int(user["store_id"])


def _name(body: dict) -> str:
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")
    if len(name) > 120:
        raise HTTPException(status_code=400, detail="Group name is too long")
    return name


@router.get("/category-groups")
async def list_category_groups(request: Request, user: dict = Depends(_auth)):
    """Groups for this store, plus anything stocked that no group covers."""
    sid = _sid(user)
    with request.app.state.engine.connect() as conn:
        groups = repo.list_groups(conn, sid)
        ungrouped = repo.ungrouped_categories(conn, sid)
        customised = repo.has_own_groups(conn, sid)
    return {
        "groups": groups,
        "ungrouped": ungrouped,
        # False = still on the vertical defaults, so the app can offer "reset"
        # only when there is something to reset.
        "customised": customised,
    }


@router.post("/category-groups")
async def create_category_group(request: Request, user: dict = Depends(_auth)):
    sid = _sid(user)
    body = await request.json()
    name = _name(body)
    categories = body.get("category_ids") or []
    with request.app.state.engine.begin() as conn:
        gid = repo.create_group(conn, sid, name, categories)
    return {"group_id": gid, "name": name}


@router.patch("/category-groups/{group_id}")
async def update_category_group(
    group_id: int, request: Request, user: dict = Depends(_auth)
):
    """Rename a group and/or replace its categories."""
    sid = _sid(user)
    body = await request.json()
    if "name" not in body and "category_ids" not in body:
        raise HTTPException(status_code=400, detail="Nothing to update")

    with request.app.state.engine.begin() as conn:
        # Fork first, so a group_id read from the template resolves to this
        # store's copy of it rather than 404ing on the first edit.
        repo.fork_groups_for_store(conn, sid)
        ok = True
        if "name" in body:
            ok = repo.rename_group(conn, sid, group_id, _name(body))
        if ok and "category_ids" in body:
            ok = repo.set_members(
                conn, sid, group_id, body.get("category_ids") or []
            )
        if not ok:
            raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "ok"}


@router.delete("/category-groups/{group_id}")
async def delete_category_group(
    group_id: int, request: Request, user: dict = Depends(_auth)
):
    sid = _sid(user)
    with request.app.state.engine.begin() as conn:
        repo.fork_groups_for_store(conn, sid)
        if not repo.delete_group(conn, sid, group_id):
            raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "deleted"}


@router.post("/category-groups/reset")
async def reset_category_groups(request: Request, user: dict = Depends(_auth)):
    """Discard this store's groups and go back to the vertical defaults."""
    sid = _sid(user)
    with request.app.state.engine.begin() as conn:
        repo.reset_to_defaults(conn, sid)
    return {"status": "reset"}
