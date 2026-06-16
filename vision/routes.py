"""Vision shelf-inventory endpoints.

POST /kirana/vision/shelf/analyze?session_type=morning|evening  — upload photo (async)
GET  /kirana/vision/sessions?date=                              — today's sessions
GET  /kirana/vision/session/{id}/items                          — detected items
GET  /kirana/vision/sales?date=                                 — morning-evening delta
POST /kirana/vision/correct/{item_id}                           — owner correction
GET  /kirana/vision/image/{path}                                — stored shelf image

Analysis runs in a FastAPI background task: the upload returns 202 immediately with a
pending session; when Gemini + catalog matching finish, the session is finalized and an
FCM push (channel 'vision') is sent so the app can refresh the Results view.

Auth mirrors ai/routes (Bearer token or admin X-API-Key). store_id comes from the token.
"""
from __future__ import annotations

import base64
import asyncio
import json
import logging
from datetime import date as _date
from typing import Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Query, Request, UploadFile)
from fastapi.responses import FileResponse

from . import repository as repo
from . import storage
from .analyzer import GEMINI_MODEL, SHELF_PROMPT, parse_detections
from .matcher import match_detections
from .schemas import (CorrectionInput, SalesResponse, SessionAccepted,
                      SessionSummary, VisionItemOut)

logger = logging.getLogger("vision")

router = APIRouter(prefix="/kirana/vision", tags=["Vision"])

_VALID_TYPES = {"morning", "evening"}
_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB guard per image
_MIN_IMAGES = 3
_MAX_IMAGES = 10


# ── Auth (same pattern as ai/routes) ─────────────────────────────────────────

def _auth(request: Request) -> dict:
    s = request.app.state.settings
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer "):] if auth_hdr.startswith("Bearer ") else ""
    api_key = request.headers.get("X-API-Key", "")

    if api_key and api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = request.app.state.kirana_service.user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _require_store(user: dict) -> int:
    store_id = user.get("store_id")
    if store_id is None:
        raise HTTPException(status_code=400, detail="No store context on this account")
    return int(store_id)


def _gemini_api_key(request: Request) -> str:
    import os
    key = getattr(request.app.state.settings, "gemini_api_key", "") or os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="Vision AI not configured")
    return key


# ── Background analysis worker ────────────────────────────────────────────────

async def _gemini_one(api_key: str, b64: str, mime: str) -> list:
    """Analyze a single image. Returns its parsed detections (raises on API error)."""
    from ai.routes import call_gemini
    parts = [
        {"inline_data": {"mime_type": mime, "data": b64}},
        {"text": SHELF_PROMPT},
    ]
    raw = await call_gemini(GEMINI_MODEL, parts, api_key)
    return parse_detections(raw)


async def _process_shelf(
    engine, kirana_service, api_key: str, session_id: int, store_id: int,
    user_id: Optional[int], session_type: str, images: list[dict],
) -> None:
    """Runs after the response is sent: Gemini per image (concurrently) → parse →
    catalog match → persist → finalize → FCM. Catches everything so a failure marks
    the session, never crashes.

    Counts are SUMMED across images: the owner photographs different sections of the
    store, so each photo contributes its own facings. (Overlapping photos would
    double-count — instruct owners to cover distinct shelves.)
    """
    try:
        results = await asyncio.gather(
            *[_gemini_one(api_key, im["b64"], im["mime"]) for im in images],
            return_exceptions=True,
        )
        detections = []
        ok = 0
        for r in results:
            if isinstance(r, Exception):
                logger.warning("vision: one image failed in session %s: %s", session_id, r)
                continue
            ok += 1
            detections.extend(r)
        if ok == 0:
            raise RuntimeError("All images failed to analyze")

        match_detections(detections, engine)
        repo.save_items(engine, session_id, detections)

        total_units = sum(d.count for d in detections)
        total_skus = len({d.product_id for d in detections if d.product_id is not None})
        unknown = sum(1 for d in detections if d.is_unknown)
        repo.finalize_session(engine, session_id, total_skus, total_units, unknown, "done")

        if user_id is not None:
            label = "Morning" if session_type == "morning" else "Evening"
            kirana_service.send_fcm_to_user(
                user_id,
                title=f"{label} shelf scanned",
                body=f"{total_skus} products, {total_units} units counted"
                     + (f" · {unknown} need review" if unknown else ""),
                data={"action": "open_vision", "channel": "vision",
                      "tab": "vision", "subtab": "1", "session_id": str(session_id)},
            )
        logger.info("vision: session %s done (skus=%d units=%d unknown=%d)",
                    session_id, total_skus, total_units, unknown)
    except Exception as exc:  # noqa: BLE001 — background task must never propagate
        logger.exception("vision: session %s failed: %s", session_id, exc)
        try:
            repo.fail_session(engine, session_id, str(exc))
            if user_id is not None:
                kirana_service.send_fcm_to_user(
                    user_id, title="Shelf scan failed",
                    body="Could not analyze the photo. Please try again.",
                    data={"action": "open_vision", "channel": "vision",
                          "tab": "vision", "session_id": str(session_id)},
                )
        except Exception:  # noqa: BLE001
            logger.exception("vision: failed to mark session %s failed", session_id)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/shelf/analyze", response_model=SessionAccepted, status_code=202)
async def shelf_analyze(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(..., description="3–10 shelf photos covering the store"),
    session_type: str = Query(..., description="morning | evening"),
    user: dict = Depends(_auth),
):
    if session_type not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail="session_type must be 'morning' or 'evening'")
    if not _MIN_IMAGES <= len(files) <= _MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload between {_MIN_IMAGES} and {_MAX_IMAGES} photos (got {len(files)})",
        )
    store_id = _require_store(user)
    api_key = _gemini_api_key(request)

    urls: list[str] = []
    images: list[dict] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(status_code=400, detail="One of the images is empty")
        if len(data) > _MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="An image is too large (max 12 MB each)")
        mime = f.content_type or "image/jpeg"
        _, url = storage.save_image(store_id, session_type, data, mime)
        urls.append(url)
        images.append({"b64": base64.b64encode(data).decode("ascii"), "mime": mime})

    # image_url column holds a JSON array of all photo URLs for this session.
    session_id = repo.create_session(
        request.app.state.engine, store_id, session_type, json.dumps(urls))

    background.add_task(
        _process_shelf,
        request.app.state.engine, request.app.state.kirana_service, api_key,
        session_id, store_id, user.get("user_id"), session_type, images,
    )
    return SessionAccepted(session_id=session_id, store_id=store_id,
                           session_type=session_type, status="pending")


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(
    request: Request,
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, default today"),
    user: dict = Depends(_auth),
):
    store_id = _require_store(user)
    rows = repo.get_sessions(request.app.state.engine, store_id, date)
    return [
        SessionSummary(
            session_id=r["session_id"], session_type=r["session_type"],
            session_date=str(r["session_date"]), status=r["status"],
            total_skus=r["total_skus"], total_units=r["total_units"],
            unknown_count=r["unknown_count"],
            created_at=str(r["created_at"]) if r.get("created_at") else None,
        ) for r in rows
    ]


@router.get("/session/{session_id}/items", response_model=list[VisionItemOut])
def session_items(session_id: int, request: Request, user: dict = Depends(_auth)):
    store_id = _require_store(user)
    rows = repo.get_items(request.app.state.engine, store_id, session_id)
    return [VisionItemOut(**r) for r in rows]


@router.get("/sales", response_model=SalesResponse)
def sales(
    request: Request,
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, default today"),
    user: dict = Depends(_auth),
):
    store_id = _require_store(user)
    sd = date or _date.today().isoformat()
    deltas = repo.compute_sales_delta(request.app.state.engine, store_id, sd)
    if not deltas:
        raise HTTPException(
            status_code=404,
            detail="No completed morning + evening scans yet for this date.",
        )
    return SalesResponse(
        store_id=store_id, session_date=sd,
        items=[d for d in deltas],
        total_sold=sum(d["sold"] for d in deltas),
    )


@router.post("/correct/{item_id}")
def correct(item_id: int, body: CorrectionInput, request: Request, user: dict = Depends(_auth)):
    store_id = _require_store(user)
    ok = repo.correct_item(request.app.state.engine, store_id, item_id, body.corrected_product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found for this store")
    return {"status": "ok", "item_id": item_id, "corrected_product_id": body.corrected_product_id}


@router.get("/image/{path:path}")
def get_image(path: str, request: Request, user: dict = Depends(_auth)):
    abs_path = storage.resolve_url(f"/kirana/vision/image/{path}")
    if not abs_path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(abs_path)
