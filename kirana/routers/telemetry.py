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
    IssueReportCreate,
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


# ── App activity tracking ─────────────────────────────────────────────────────


@router.post("/tracking/app-event")
async def track_app_event(request: Request, user: dict = Depends(_auth)):
    """Called by the Flutter app on foreground/background lifecycle transitions."""
    from sqlalchemy import text as _text

    body = await request.json()
    event = body.get("event", "foreground")  # 'foreground' or 'background'
    duration_sec = body.get("duration_sec")  # int seconds, sent on background
    uid = user.get("user_id")
    if not uid:
        return {"ok": False}
    with request.app.state.engine.connect() as conn:
        conn.execute(
            _text("""
            INSERT INTO kirana_oltp.app_activity(user_id, event, duration_sec)
            VALUES(:uid, :event, :dur)
        """),
            {"uid": uid, "event": event, "dur": duration_sec},
        )
        conn.commit()
    return {"ok": True}


# ── Support ───────────────────────────────────────────────────────────────────


@router.post("/support/report")
async def report_issue(
    request: Request, body: IssueReportCreate, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=400, detail="Store ID required for reporting")
    return _svc(request).report_issue(user["user_id"], sid, body)
