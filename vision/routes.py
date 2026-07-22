"""Vision shelf-inventory endpoints.

POST /kirana/vision/shelf/analyze?session_type=morning|evening  — upload photo (async)
GET  /kirana/vision/sessions?date=                              — today's sessions
GET  /kirana/vision/session/{id}/items                          — detected items
GET  /kirana/vision/sales?date=                                 — morning-evening delta
POST /kirana/vision/correct/{item_id}                           — owner correction
GET  /kirana/vision/analytics?days=                             — usage/accuracy analytics
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
from fastapi.responses import FileResponse, Response

from . import counter_repository as counter_repo
from . import detector
from . import onboarding_repository as onboarding_repo
from . import onboarding_storage
from . import repository as repo
from . import storage
from .analyzer import GEMINI_MODEL, SHELF_PROMPT, parse_detections
from .matcher import get_matcher, match_detections
from .schemas import (CorrectionInput, CounterHistoryResponse,
                      CounterResolveInput, CounterResolveItem,
                      CounterResolveResponse, CounterSummaryResponse,
                      CounterSyncInput, CounterSyncResponse,
                      OnboardingCommitInput, OnboardingCommitResponse,
                      SalesResponse, SessionAccepted, SessionSummary,
                      VisionAnalyticsResponse, VisionItemOut)

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
# Cap how many images we analyze at once. A full 10-photo batch firing 10 concurrent
# Gemini calls + 10 CPU-bound YOLO inferences saturates cores and risks Gemini
# rate-limit failures (which would silently drop those images). Bound it so batches
# stay reliable; override via env VISION_ANALYZE_CONCURRENCY.
_ANALYZE_CONCURRENCY = max(1, int(os.getenv("VISION_ANALYZE_CONCURRENCY", "4")))


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

def _sniff_image_mime(data: bytes, declared: Optional[str]) -> str:
    """Return a Gemini-supported image mime for an upload. Android's image picker
    frequently reports 'application/octet-stream' (or nothing), which Gemini rejects
    with a 400 'Unsupported MIME type' — so we sniff the magic bytes and only trust a
    declared type when it's already a real image/* type."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:12].endswith(b"ftypheic") or data[4:12] in (b"ftypheic", b"ftypheif"):
        return "image/heic"
    if declared and declared.startswith("image/") and declared != "image/octet-stream":
        return declared
    return "image/jpeg"  # safe default — most phone captures are JPEG


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


async def _detect_one(engine, api_key: str, image: dict, image_index: int = 0) -> list:
    """Detect products in one image using the custom YOLO as PRIMARY detector and
    Gemini as the FALLBACK that fills what YOLO doesn't know. Both run concurrently
    (YOLO offloaded to a thread — it's CPU-bound numpy); results are catalog-matched
    then merged. If YOLO is disabled/absent this is exactly the old Gemini-only path.

    Gemini is the FALLBACK, so its failure (quota/mime/timeout) must NOT discard a
    good YOLO result — that's the whole point of running on-device detection. We only
    surface a per-image failure when we're left with nothing to show (YOLO absent or
    empty AND Gemini errored), so that image counts as failed → owner retakes."""
    b64, mime = image["b64"], image["mime"]
    yolo_ran = detector.is_available()
    if yolo_ran:
        img_bytes = base64.b64decode(b64)
        yolo_res, gemini_res = await asyncio.gather(
            asyncio.to_thread(detector.detect, img_bytes),
            _gemini_one(api_key, b64, mime),
            return_exceptions=True,
        )
    else:
        yolo_res = []
        gemini_res = (await asyncio.gather(
            _gemini_one(api_key, b64, mime), return_exceptions=True))[0]

    if isinstance(yolo_res, Exception):
        logger.warning("vision.detector failed on one image: %s", yolo_res)
        yolo_dets = []
    else:
        yolo_dets = list(yolo_res)

    if isinstance(gemini_res, Exception):
        # No YOLO detections to fall back on → let this image count as failed.
        if not yolo_dets:
            raise gemini_res
        logger.warning("vision: Gemini fallback failed, using YOLO only: %s", gemini_res)
        gemini_dets = []
    else:
        gemini_dets = list(gemini_res)

    # Match separately: YOLO labels are terse and collide with generic catalog
    # names on shared words, so they need a stricter cutoff than Gemini's rich names.
    # Weak YOLO matches fall through to 'unknown' → owner review (never wrong stock).
    match_detections(yolo_dets, engine, min_score=_YOLO_MATCH_MIN_SCORE)
    match_detections(gemini_dets, engine)
    merged = _merge_image_detections(yolo_dets + gemini_dets)
    for d in merged:  # stamp origin photo so the review UI can crop it later
        d.image_index = image_index
    return merged


async def _detect_all(engine, api_key: str, images: list[dict]) -> list:
    """Detect across a whole photo batch with bounded concurrency. Returns one
    result (list of detections) or an Exception per image, positionally — callers
    skip the failures and keep the rest, so a couple of bad photos never sink the
    whole scan."""
    sem = asyncio.Semaphore(_ANALYZE_CONCURRENCY)

    async def _one(idx: int, im: dict):
        async with sem:
            return await _detect_one(engine, api_key, im, image_index=idx)

    return await asyncio.gather(*[_one(i, im) for i, im in enumerate(images)],
                                return_exceptions=True)


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
        results = await _detect_all(engine, api_key, images)
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
    # Durable Azure Blob in production so the review-screen crop thumbnails survive
    # container restarts; fall back to the local-disk seam so a dev backend without
    # Azure configured still works end-to-end (same pattern as onboarding).
    use_blob = onboarding_storage.is_configured()

    refs: list[str] = []
    images: list[dict] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(status_code=400, detail="One of the images is empty")
        if len(data) > _MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="An image is too large (max 12 MB each)")
        mime = _sniff_image_mime(data, f.content_type)
        if use_blob:
            refs.append("blob:" + onboarding_storage.upload_shelf_image(store_id, data, mime))
        else:
            _, url = storage.save_image(store_id, session_type, data, mime)
            refs.append(url)
        images.append({"b64": base64.b64encode(data).decode("ascii"), "mime": mime})

    # image_url column holds a JSON array of all photo refs for this session
    # (local '/kirana/vision/image/...' urls or durable 'blob:<name>' refs).
    session_id = repo.create_session(
        request.app.state.engine, store_id, session_type, json.dumps(refs))

    background.add_task(
        _process_shelf,
        request.app.state.engine, request.app.state.kirana_service, api_key,
        session_id, store_id, user.get("user_id"), session_type, images,
    )
    return SessionAccepted(session_id=session_id, store_id=store_id,
                           session_type=session_type, status="pending")


def _photo_count(image_url) -> int:
    """image_url holds a JSON array of photo refs (or a single legacy ref)."""
    if not image_url:
        return 0
    try:
        refs = json.loads(image_url)
        return len(refs) if isinstance(refs, list) else 1
    except (json.JSONDecodeError, TypeError):
        return 1


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(
    request: Request,
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, default today"),
    days: Optional[int] = Query(default=None, ge=1, le=90,
                                description="History window: sessions of the last N days, newest first"),
    user: dict = Depends(_auth),
):
    store_id = _require_store(user)
    if days is not None:
        rows = repo.get_recent_sessions(request.app.state.engine, store_id, days)
    else:
        rows = repo.get_sessions(request.app.state.engine, store_id, date)
    return [
        SessionSummary(
            session_id=r["session_id"], session_type=r["session_type"],
            session_date=str(r["session_date"]), status=r["status"],
            total_skus=r["total_skus"], total_units=r["total_units"],
            unknown_count=r["unknown_count"],
            created_at=str(r["created_at"]) if r.get("created_at") else None,
            photo_count=_photo_count(r.get("image_url")),
        ) for r in rows
    ]


@router.get("/session/{session_id}/photo/{index}")
def session_photo(
    session_id: int, index: int, request: Request,
    thumb: int = Query(default=0, description="1 = downscaled 512px JPEG"),
    user: dict = Depends(_auth),
):
    """Serve one of the photos the owner uploaded for a scan, so the history view
    can show exactly what was photographed. Store-scoped; 404 when the photo is
    gone (e.g. pre-Blob sessions on a restarted container)."""
    store_id = _require_store(user)
    sess = repo.get_session(request.app.state.engine, store_id, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    data = _load_source_image_bytes(sess.get("image_url"), index)
    if not data:
        raise HTTPException(status_code=404, detail="Photo unavailable")

    if thumb:
        from io import BytesIO
        from PIL import Image
        try:
            img = Image.open(BytesIO(data)).convert("RGB")
            img.thumbnail((512, 512), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            data, mime = buf.getvalue(), "image/jpeg"
        except Exception:  # noqa: BLE001 — unreadable ⇒ serve the original bytes
            mime = _sniff_image_mime(data, None)
    else:
        mime = _sniff_image_mime(data, None)
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/session/{session_id}/items", response_model=list[VisionItemOut])
def session_items(session_id: int, request: Request, user: dict = Depends(_auth)):
    store_id = _require_store(user)
    rows = repo.get_items(request.app.state.engine, store_id, session_id)
    return [VisionItemOut(**r) for r in rows]


def _load_source_image_bytes(image_url: Optional[str], image_index: int) -> Optional[bytes]:
    """Resolve one detection's source photo → raw bytes. The session image_url holds
    a JSON array of refs; local disk refs ('/kirana/vision/image/...') are read from
    disk, Azure Blob refs ('blob:<name>') are downloaded. Returns None if unavailable
    (e.g. ephemeral container disk lost the file after a restart)."""
    if not image_url:
        return None
    try:
        refs = json.loads(image_url)
    except (json.JSONDecodeError, TypeError):
        refs = [image_url]
    if not isinstance(refs, list) or not refs:
        return None
    ref = refs[image_index] if 0 <= image_index < len(refs) else refs[0]
    try:
        if isinstance(ref, str) and ref.startswith("blob:"):
            data, _ = onboarding_storage.download_shelf_image(ref[len("blob:"):])
            return data
        abs_path = storage.resolve_url(ref)
        if abs_path:
            with open(abs_path, "rb") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001 — best-effort; missing image ⇒ no thumbnail
        logger.warning("vision: could not load source image for crop: %s", exc)
    return None


@router.get("/item/{item_id}/crop")
def item_crop(item_id: int, request: Request, user: dict = Depends(_auth)):
    """Return a cropped JPEG of one detected item (its bbox out of its source photo),
    so the owner can visually recognise what each row is when reviewing/correcting.
    404 if the item/image is unavailable — the app just falls back to no thumbnail."""
    store_id = _require_store(user)
    src = repo.get_item_source(request.app.state.engine, store_id, item_id)
    if not src:
        raise HTTPException(status_code=404, detail="Item not found for this store")

    data = _load_source_image_bytes(src.get("image_url"), int(src.get("image_index") or 0))
    if not data:
        raise HTTPException(status_code=404, detail="Source image unavailable")

    from io import BytesIO
    from PIL import Image
    try:
        img = Image.open(BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Source image unreadable")
    w, h = img.size

    # bbox_json = normalized [x1, y1, x2, y2]; pad ~10% so the owner sees context, not
    # a tight crop. Missing/degenerate bbox ⇒ fall back to the whole photo.
    box = None
    try:
        if src.get("bbox_json"):
            x1, y1, x2, y2 = (float(v) for v in json.loads(src["bbox_json"])[:4])
            if x2 > x1 and y2 > y1:
                box = (x1, y1, x2, y2)
    except (json.JSONDecodeError, TypeError, ValueError):
        box = None

    if box:
        x1, y1, x2, y2 = box
        px, py = (x2 - x1) * 0.10, (y2 - y1) * 0.10
        left = int(max(0.0, x1 - px) * w)
        top = int(max(0.0, y1 - py) * h)
        right = int(min(1.0, x2 + px) * w)
        bottom = int(min(1.0, y2 + py) * h)
        if right > left and bottom > top:
            img = img.crop((left, top, right, bottom))

    img.thumbnail((480, 480), Image.LANCZOS)  # cap payload; thumbnails are small
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return Response(content=buf.getvalue(), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


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


@router.get("/analytics", response_model=VisionAnalyticsResponse)
def analytics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    user: dict = Depends(_auth),
):
    """Vision usage + accuracy analytics for the store: session volume and processing
    latency, unknown/correction rates (accuracy proxies), own-YOLO vs Gemini detector
    split, per-day trend series, and the most-seen unknown products (next labels to
    train). Derived entirely from vision_session / vision_item."""
    store_id = _require_store(user)
    data = repo.get_analytics(request.app.state.engine, store_id, days)
    return VisionAnalyticsResponse(store_id=store_id, **data)


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
        results = await _detect_all(engine, api_key, images)
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
        mime = _sniff_image_mime(data, f.content_type)
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


@router.get("/counter/sessions", response_model=CounterHistoryResponse)
def counter_sessions(
    request: Request,
    days: int = Query(default=14, ge=1, le=90, description="History window in days"),
    user: dict = Depends(_auth),
):
    """Counter scan history: recent sessions (newest first) with their per-product
    tallies and prices, so the owner can look back at any counting run."""
    store_id = _require_store(user)
    sessions = counter_repo.get_history(request.app.state.engine, store_id, days)
    return CounterHistoryResponse(store_id=store_id, sessions=sessions)


@router.post("/counter/resolve", response_model=CounterResolveResponse)
def counter_resolve(body: CounterResolveInput, request: Request, user: dict = Depends(_auth)):
    """Resolve on-device model class labels → catalog products + the store's selling
    price. The app calls this once per counter launch (and caches the map), so the
    LIVE tally can show prices/value while counting — even before any sync."""
    store_id = _require_store(user)
    engine = request.app.state.engine
    matcher = get_matcher(engine)

    items: list[dict] = []
    seen: set[str] = set()
    for raw in body.class_names[:1000]:
        cls = (raw or "").strip()
        if not cls or cls in seen:
            continue
        seen.add(cls)
        res = matcher.match(cls)
        matched = res is not None and not res.is_unknown
        items.append({
            "class_name": cls,
            "qty": 1,  # attach_prices needs a qty; line_value is ignored here
            "product_id": res.product_id if matched else None,
            "display_name": (res.display_name if matched
                             else counter_repo._prettify(cls)),
            "is_unknown": not matched,
        })
    counter_repo.attach_prices(engine, store_id, items)
    return CounterResolveResponse(store_id=store_id, items=[
        CounterResolveItem(class_name=i["class_name"], product_id=i["product_id"],
                           display_name=i["display_name"], price=i["price"],
                           is_unknown=i["is_unknown"])
        for i in items
    ])


@router.get("/image/{path:path}")
def get_image(path: str, request: Request, user: dict = Depends(_auth)):
    abs_path = storage.resolve_url(f"/kirana/vision/image/{path}")
    if not abs_path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(abs_path)


# ── Model delivery (PAI-15) ───────────────────────────────────────────────────
#
# The counter model is no longer bundled in the APK — see vision/model_storage.py
# for why encrypting the asset wouldn't have helped. The app fetches it once,
# authenticated, and caches it in app-private storage.


@router.get("/model/{model}/manifest")
def get_model_manifest(model: str, request: Request, user: dict = Depends(_auth)):
    """Version + checksum so the app knows whether its cached copy is current."""
    if model not in ("counter",):
        raise HTTPException(status_code=404, detail="Unknown model")
    from vision import model_storage

    if not model_storage.is_configured():
        raise HTTPException(status_code=503, detail="Model storage not configured")
    try:
        manifest = model_storage.get_manifest(model)
    except Exception:
        logger.exception("vision: model manifest fetch failed for %s", model)
        raise HTTPException(status_code=503, detail="Model manifest unavailable")
    return {
        "model": model,
        "version": manifest.get("version"),
        "sha256": manifest.get("sha256"),
        "size": manifest.get("size"),
    }


@router.get("/model/{model}/download")
def download_vision_model(
    model: str, request: Request, user: dict = Depends(_auth)
):
    """Stream the weights to an authenticated client, and record who took them.

    The version is resolved from the manifest rather than taken from the query
    string, so a caller can't probe the container for other blobs.
    """
    if model not in ("counter",):
        raise HTTPException(status_code=404, detail="Unknown model")
    from vision import model_storage

    if not model_storage.is_configured():
        raise HTTPException(status_code=503, detail="Model storage not configured")
    try:
        manifest = model_storage.get_manifest(model)
        version = manifest["version"]
        data = model_storage.download_model(model, version)
    except Exception:
        logger.exception("vision: model download failed for %s", model)
        raise HTTPException(status_code=503, detail="Model unavailable")

    # Attribution — this is the part that makes a leaked account traceable.
    # Never let a logging failure block the download.
    try:
        from sqlalchemy import text as _text

        with request.app.state.engine.begin() as conn:
            conn.execute(
                _text("""
                INSERT INTO kirana_oltp.vision_model_fetch
                    (user_id, store_id, model, version)
                VALUES (:uid, :sid, :model, :ver)
                """),
                {
                    "uid": user.get("user_id"),
                    "sid": user.get("store_id"),
                    "model": model,
                    "ver": version,
                },
            )
    except Exception:
        logger.warning("vision: could not record model fetch", exc_info=True)

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Model-Version": str(version),
            "X-Model-Sha256": str(manifest.get("sha256", "")),
        },
    )


@router.get("/model/{model}/labels")
def download_vision_labels(
    model: str, request: Request, user: dict = Depends(_auth)
):
    """Label list for the current model version (small, plain text)."""
    if model not in ("counter",):
        raise HTTPException(status_code=404, detail="Unknown model")
    from vision import model_storage

    if not model_storage.is_configured():
        raise HTTPException(status_code=503, detail="Model storage not configured")
    try:
        manifest = model_storage.get_manifest(model)
        data = model_storage.download_labels(model, manifest["version"])
    except Exception:
        logger.exception("vision: label fetch failed for %s", model)
        raise HTTPException(status_code=503, detail="Labels unavailable")
    if data is None:
        raise HTTPException(status_code=404, detail="No labels for this version")
    return Response(content=data, media_type="text/plain; charset=utf-8")
