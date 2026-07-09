"""Call-center endpoints.

Auth surfaces on this router:
  * Admin API key (X-API-Key)         → full manager access (existing admin panel key).
  * Executive bearer token            → the person who logged in; role decides reach.
      - role 'call_manager'           → manager access (same as admin key).
      - role 'call_executive'         → only their assigned stores.

Executive endpoints (queue/callbacks/stats/call-sheet) act on the logged-in
executive_id, so they need a real executive token (managers included; the shared
admin key has no personal identity and uses the manager-oversight endpoints).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import repository as repo
from .schemas import (AssignInput, CallLogInput, CallLogResponse, ExecutiveCreate,
                      ExecutiveOut, ExecutiveUpdate, LoginInput, LoginResponse,
                      MeResponse)

logger = logging.getLogger("callcenter")

router = APIRouter(prefix="/kirana/callcenter", tags=["Call Center"])


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _auth(request: Request) -> dict:
    """Resolve the caller to either the admin key or an executive session."""
    s = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    if api_key and api_key == s.kirana_api_key:
        return {"kind": "admin", "role": "admin", "executive_id": None, "full_name": "Admin"}
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer "):] if auth_hdr.startswith("Bearer ") else ""
    if bearer:
        ex = repo.executive_by_token(request.app.state.engine, bearer)
        if ex:
            return {"kind": "executive", **ex}
    raise HTTPException(status_code=401, detail="Unauthorized")


def _require_manager(user: dict = Depends(_auth)) -> dict:
    if user["kind"] == "admin" or user.get("role") == "call_manager":
        return user
    raise HTTPException(status_code=403, detail="Manager access required")


def _require_executive(user: dict = Depends(_auth)) -> dict:
    if user["kind"] == "executive":
        return user
    raise HTTPException(status_code=400, detail="No executive identity on this session")


def _require_store_access(user: dict, engine, store_id: int) -> None:
    """Executives may only touch their assigned stores; managers/admin any store."""
    if user.get("role") == "call_executive":
        if not repo.is_assigned(engine, user["executive_id"], store_id):
            raise HTTPException(status_code=403, detail="Store not assigned to you")


# ── Auth endpoints ────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(body: LoginInput, request: Request):
    engine = request.app.state.engine
    ex = repo.authenticate(engine, body.username.strip(), body.password)
    if not ex:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = repo.create_session(engine, ex["executive_id"])
    return LoginResponse(token=token, executive_id=ex["executive_id"],
                        full_name=ex["full_name"], role=ex["role"])


@router.get("/me", response_model=MeResponse)
def me(request: Request, user: dict = Depends(_require_executive)):
    return MeResponse(executive_id=user["executive_id"], username=user["username"],
                     full_name=user["full_name"], role=user["role"])


@router.post("/logout")
def logout(request: Request, user: dict = Depends(_auth)):
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        repo.revoke_session(request.app.state.engine, auth_hdr[len("Bearer "):])
    return {"status": "ok"}


# ── Executive: my work ────────────────────────────────────────────────────────

@router.get("/queue")
def queue(request: Request, limit: int = Query(default=100, ge=1, le=500),
          user: dict = Depends(_require_executive)):
    return {"items": repo.get_queue(request.app.state.engine, user["executive_id"], limit)}


@router.get("/callbacks")
def callbacks(request: Request, user: dict = Depends(_require_executive)):
    return {"items": repo.get_callbacks(request.app.state.engine, user["executive_id"])}


@router.get("/stats")
def stats(request: Request, user: dict = Depends(_require_executive)):
    return repo.get_stats(request.app.state.engine, user["executive_id"])


@router.get("/stores/{store_id}")
def call_sheet(store_id: int, request: Request, user: dict = Depends(_require_executive)):
    engine = request.app.state.engine
    _require_store_access(user, engine, store_id)
    sheet = repo.get_call_sheet(engine, user["executive_id"], store_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Store not found")
    return sheet


@router.post("/stores/{store_id}/calls", response_model=CallLogResponse)
def log_call(store_id: int, body: CallLogInput, request: Request,
             user: dict = Depends(_require_executive)):
    err = body.validation_error()
    if err:
        raise HTTPException(status_code=400, detail=err)
    engine = request.app.state.engine
    _require_store_access(user, engine, store_id)
    result = repo.log_call(engine, user["executive_id"], store_id, body.model_dump())
    return CallLogResponse(**result)


# ── Manager: executives ───────────────────────────────────────────────────────

@router.get("/executives", response_model=list[ExecutiveOut])
def list_executives(request: Request, user: dict = Depends(_require_manager)):
    rows = repo.list_executives(request.app.state.engine)
    return [ExecutiveOut(**{**r, "created_at": str(r["created_at"]) if r.get("created_at") else None})
            for r in rows]


@router.post("/executives", response_model=ExecutiveOut, status_code=201)
def create_executive(body: ExecutiveCreate, request: Request,
                     user: dict = Depends(_require_manager)):
    engine = request.app.state.engine
    try:
        row = repo.create_executive(engine, body.username.strip(), body.full_name.strip(),
                                    body.phone, body.email, body.normalized_role(), body.password)
    except Exception as exc:  # noqa: BLE001 — unique username etc.
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Username already taken")
        raise
    return ExecutiveOut(**{**row, "created_at": str(row["created_at"]),
                          "assigned_count": 0, "calls_today": 0})


@router.patch("/executives/{executive_id}", response_model=ExecutiveOut)
def update_executive(executive_id: int, body: ExecutiveUpdate, request: Request,
                    user: dict = Depends(_require_manager)):
    engine = request.app.state.engine
    if not repo.get_executive(engine, executive_id):
        raise HTTPException(status_code=404, detail="Executive not found")
    if body.is_active is not None:
        repo.set_executive_active(engine, executive_id, body.is_active)
    if body.password:
        repo.reset_password(engine, executive_id, body.password)
    row = next((r for r in repo.list_executives(engine) if r["executive_id"] == executive_id), None)
    return ExecutiveOut(**{**row, "created_at": str(row["created_at"]) if row.get("created_at") else None})


# ── Manager: assignments ──────────────────────────────────────────────────────

@router.post("/assignments")
def assign(body: AssignInput, request: Request, user: dict = Depends(_require_manager)):
    engine = request.app.state.engine
    if not repo.get_executive(engine, body.executive_id):
        raise HTTPException(status_code=404, detail="Executive not found")
    assigned_by = user.get("executive_id")  # None when acting as the admin key
    result = repo.assign_stores(engine, body.executive_id, body.store_ids, assigned_by)
    return result


@router.delete("/assignments/{store_id}")
def unassign(store_id: int, request: Request, user: dict = Depends(_require_manager)):
    ok = repo.unassign_store(request.app.state.engine, store_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No active assignment for this store")
    return {"status": "ok"}


@router.get("/load")
def load(request: Request, user: dict = Depends(_require_manager)):
    return {"items": repo.assignment_load(request.app.state.engine)}


@router.get("/assignable-stores")
def assignable_stores(request: Request, q: str | None = None,
                     unassigned_only: bool = False, user: dict = Depends(_require_manager)):
    return {"items": repo.list_stores_for_assignment(
        request.app.state.engine, q, unassigned_only)}


# ── Manager: feedback digest + store history ──────────────────────────────────

@router.get("/feedback")
def feedback(request: Request, days: int = Query(default=30, ge=1, le=365),
             tag: str | None = None, sentiment: str | None = None,
             user: dict = Depends(_require_manager)):
    return {"items": repo.list_feedback(request.app.state.engine, days, tag, sentiment)}


@router.get("/stores/{store_id}/history")
def store_history(store_id: int, request: Request, user: dict = Depends(_require_manager)):
    """Call history for a store — powers the StoreDetail 'Call History' tab (admin key)."""
    return {"items": repo.store_call_history(request.app.state.engine, store_id)}
