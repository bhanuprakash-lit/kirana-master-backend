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
import os
from datetime import date as _date
from typing import Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Query, Request, UploadFile)
from fastapi.responses import FileResponse

from . import counter_repository as counter_repo
from . import detector
from . import onboarding_repository as onboarding_repo
from . import onboarding_storage
from . import repository as repo
from . import storage
from .analyzer import GEMINI_MODEL, SHELF_PROMPT, parse_detections
from .matcher import get_matcher, match_detections
from .schemas import (CorrectionInput, CounterSummaryResponse, CounterSyncInput,
                      CounterSyncResponse, OnboardingCommitInput,
                      OnboardingCommitResponse, SalesResponse, SessionAccepted,
                      SessionSummary, VisionItemOut)

logger = logging.getLogger("vision")

router = APIRouter(prefix="/kirana/vision", tags=["Vision"])

_VALID_TYPES = {"morning", "evening"}
_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB guard per image
_MIN_IMAGES = 3
_MAX_IMAGES = 10
# YOLO class labels are terse ('red label tea powder') and fuzzy-match generic
# catalog names on shared words; require a high score before auto-mapping so a weak
# match becomes 'unknown' (owner reviews) instead of wrong stock.
_YOLO_MATCH_MIN_SCORE = 0.82
# Bulk stock-in is ungated (new stores onboard before Pro) but rate-limited to bound
# per-photo detection cost. Override via env ONBOARDING_DAILY_LIMIT.
_ONBOARDING_DAILY_LIMIT = int(os.getenv("ONBOARDING_DAILY_LIMIT", "5"))


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


def _merge_image_detections(dets: list) -> list:
    """Combine one image's YOLO + Gemini detections without double-counting.

    Matched products (same product_id from either detector) collapse to a single
    entry keeping the higher facings count. Unmatched detections (product_id None)
    are all kept — those are the coverage/growth items the owner reviews and that
    become the next YOLO training labels.
    """
    by_pid: dict = {}
    unknowns: list = []
    for d in dets:
        if d.product_id is None:
            unknowns.append(d)
            continue
        cur = by_pid.get(d.product_id)
        if cur is None or d.count > cur.count:
            by_pid[d.product_id] = d
    return list(by_pid.values()) + unknowns


async def _detect_one(engine, api_key: str, image: dict) -> list:
    """Detect products in one image using the custom YOLO as PRIMARY detector and
    Gemini as the FALLBACK that fills what YOLO doesn't know. Both run concurrently
    (YOLO offloaded to a thread — it's CPU-bound numpy); results are catalog-matched
    then merged. If YOLO is disabled/absent this is exactly the old Gemini-only path."""
    b64, mime = image["b64"], image["mime"]
    gemini_task = _gemini_one(api_key, b64, mime)
    if detector.is_available():
        img_bytes = base64.b64decode(b64)
        yolo_task = asyncio.to_thread(detector.detect, img_bytes)
        yolo_dets, gemini_dets = await asyncio.gather(yolo_task, gemini_task)
    else:
        yolo_dets, gemini_dets = [], await gemini_task

    # Match separately: YOLO labels are terse and collide with generic catalog
    # names on shared words, so they need a stricter cutoff than Gemini's rich names.
    # Weak YOLO matches fall through to 'unknown' → owner review (never wrong stock).
    match_detections(list(yolo_dets), engine, min_score=_YOLO_MATCH_MIN_SCORE)
    match_detections(list(gemini_dets), engine)
    return _merge_image_detections(list(yolo_dets) + list(gemini_dets))


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
            *[_detect_one(engine, api_key, im) for im in images],
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


# ── Bulk stock-in / onboarding ────────────────────────────────────────────────

async def _process_onboarding(
    engine, kirana_service, api_key: str, session_id: int, store_id: int,
    user_id: Optional[int], images: list[dict],
) -> None:
    """Background worker for bulk stock-in: Gemini per image → parse → catalog match
    → persist as vision_item → finalize → FCM to open the review screen. Mirrors
    _process_shelf but the notification deep-links to the onboarding review, and the
    detected count becomes the SUGGESTED opening quantity (owner edits before commit)."""
    try:
        results = await asyncio.gather(
            *[_detect_one(engine, api_key, im) for im in images],
            return_exceptions=True,
        )
        detections = []
        ok = 0
        for r in results:
            if isinstance(r, Exception):
                logger.warning("onboarding: image failed in session %s: %s", session_id, r)
                continue
            ok += 1
            detections.extend(r)
        if ok == 0:
            raise RuntimeError("All images failed to analyze")

        repo.save_items(engine, session_id, detections)

        total_units = sum(d.count for d in detections)
        total_skus = len({d.product_id for d in detections if d.product_id is not None})
        unknown = sum(1 for d in detections if d.is_unknown)
        repo.finalize_session(engine, session_id, total_skus, total_units, unknown, "done")

        if user_id is not None:
            kirana_service.send_fcm_to_user(
                user_id,
                title="Your shelves are ready to review",
                body=f"We found {total_skus} products. Set the quantity of each to add them to your stock."
                     + (f" · {unknown} need a quick check" if unknown else ""),
                data={"action": "open_onboarding_review", "channel": "vision",
                      "session_id": str(session_id)},
            )
        logger.info("onboarding: session %s done (skus=%d units=%d unknown=%d)",
                    session_id, total_skus, total_units, unknown)
    except Exception as exc:  # noqa: BLE001 — background task must never propagate
        logger.exception("onboarding: session %s failed: %s", session_id, exc)
        try:
            repo.fail_session(engine, session_id, str(exc))
            if user_id is not None:
                kirana_service.send_fcm_to_user(
                    user_id, title="Couldn't read your shelf photos",
                    body="Please retake the photos in good light and try again.",
                    data={"action": "open_onboarding_review", "channel": "vision",
                          "session_id": str(session_id)},
                )
        except Exception:  # noqa: BLE001
            logger.exception("onboarding: failed to mark session %s failed", session_id)


@router.post("/onboarding/analyze", response_model=SessionAccepted, status_code=202)
async def onboarding_analyze(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(..., description="3–10 in-app shelf photos"),
    user: dict = Depends(_auth),
):
    """Bulk stock-in: upload shelf photos captured in-app → durable Azure Blob →
    async detection. Returns 202 with a pending onboarding session; an FCM fires when
    detection finishes so the app can open the review screen."""
    if not _MIN_IMAGES <= len(files) <= _MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload between {_MIN_IMAGES} and {_MAX_IMAGES} photos (got {len(files)})",
        )
    store_id = _require_store(user)
    # Ungated but rate-limited: bulk stock-in triggers Gemini+YOLO per photo, so cap
    # scans/store/day to bound cost without blocking new-store onboarding.
    if user.get("role") != "admin":
        used = onboarding_repo.count_today_onboarding_sessions(request.app.state.engine, store_id)
        if used >= _ONBOARDING_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"You've reached today's limit of {_ONBOARDING_DAILY_LIMIT} shelf scans. "
                       "Please continue tomorrow or add remaining items manually.",
            )
    api_key = _gemini_api_key(request)
    use_blob = onboarding_storage.is_configured()

    refs: list[str] = []
    images: list[dict] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(status_code=400, detail="One of the images is empty")
        if len(data) > _MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="An image is too large (max 12 MB each)")
        mime = f.content_type or "image/jpeg"
        # Durable Azure Blob in production; fall back to the local-disk seam so a
        # dev backend without Azure configured still works end-to-end.
        if use_blob:
            refs.append("blob:" + onboarding_storage.upload_shelf_image(store_id, data, mime))
        else:
            _, url = storage.save_image(store_id, "onboarding", data, mime)
            refs.append(url)
        images.append({"b64": base64.b64encode(data).decode("ascii"), "mime": mime})

    session_id = repo.create_session(
        request.app.state.engine, store_id, "onboarding", json.dumps(refs))

    background.add_task(
        _process_onboarding,
        request.app.state.engine, request.app.state.kirana_service, api_key,
        session_id, store_id, user.get("user_id"), images,
    )
    return SessionAccepted(session_id=session_id, store_id=store_id,
                           session_type="onboarding", status="pending")


@router.post("/onboarding/commit/{session_id}", response_model=OnboardingCommitResponse)
def onboarding_commit(
    session_id: int, body: OnboardingCommitInput, request: Request,
    user: dict = Depends(_auth),
):
    """Write the owner-reviewed quantities into store inventory and mark the session
    committed. Idempotent: quantities are SET, so a re-commit is safe."""
    store_id = _require_store(user)
    engine = request.app.state.engine
    sess = onboarding_repo.get_onboarding_session(engine, store_id, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Onboarding session not found")
    if sess["session_type"] != "onboarding":
        raise HTTPException(status_code=400, detail="Not an onboarding session")

    result = onboarding_repo.commit_to_inventory(
        engine, store_id, session_id,
        [{"product_id": i.product_id, "quantity": i.quantity} for i in body.items],
        add_to_existing=body.add_to_existing,
    )
    return OnboardingCommitResponse(session_id=session_id, **result)


# ── Sale-area counter (on-device) ─────────────────────────────────────────────

@router.post("/counter/sync", response_model=CounterSyncResponse)
def counter_sync(body: CounterSyncInput, request: Request, user: dict = Depends(_auth)):
    """Sync a finalized on-device counter session. Detection + line-crossing counting
    happened on the phone; here we resolve each class_name → product_id via the shared
    catalog matcher and persist the tally. Idempotent by (store_id, client_uid)."""
    store_id = _require_store(user)
    engine = request.app.state.engine
    matcher = get_matcher(engine)

    items = []
    for it in body.items:
        cls = (it.class_name or "").strip()
        if not cls or it.qty <= 0:
            continue
        res = matcher.match(cls)
        matched = res is not None and not res.is_unknown
        items.append({
            "class_name": cls,
            "qty": int(it.qty),
            "product_id": res.product_id if matched else None,
            "display_name": res.display_name if matched else None,
            "match_score": res.score if res else 0.0,
            "is_unknown": not matched,
            "avg_confidence": it.avg_confidence,
        })

    saved = counter_repo.upsert_session(
        engine, store_id, body.client_uid, body.session_date, body.device_label,
        body.started_at, body.ended_at, items,
    )
    return CounterSyncResponse(**saved)


@router.get("/counter/summary", response_model=CounterSummaryResponse)
def counter_summary(
    request: Request,
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, default today"),
    user: dict = Depends(_auth),
):
    """Aggregated per-product tally for the day across all counter sessions."""
    store_id = _require_store(user)
    summary = counter_repo.get_summary(request.app.state.engine, store_id, date)
    return CounterSummaryResponse(**summary)


@router.get("/image/{path:path}")
def get_image(path: str, request: Request, user: dict = Depends(_auth)):
    abs_path = storage.resolve_url(f"/kirana/vision/image/{path}")
    if not abs_path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(abs_path)
