"""Director analytics dashboard — routes.

- ``GET /director``            → serves the self-contained dashboard page.
- ``GET /director/api/*``      → read-only JSON aggregations (one per domain).

Everything under ``/director/api`` requires ``require_director`` (dedicated
DIRECTOR_TOKEN or admin key). All endpoints accept an optional ``store_id``
(omit = fleet-wide) and a ``days`` window (default 30, clamped 1..365).
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from vision import repository as vision_repo

from . import repository as repo
from .auth import require_director

router = APIRouter(tags=["Director Analytics"])

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


def _engine(request: Request):
    return request.app.state.engine


def _days(days: int) -> int:
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    return days


# ── dashboard page ───────────────────────────────────────────────────────────
@router.get("/director", include_in_schema=False, response_class=HTMLResponse)
async def director_page():
    path = os.path.join(_STATIC_DIR, "director.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


@router.get("/director/vendor/chart.js", include_in_schema=False)
async def director_chartjs():
    # Vendored Chart.js (no CDN dependency — robust behind corporate proxies).
    path = os.path.join(_STATIC_DIR, "vendor", "chart.umd.min.js")
    return FileResponse(path, media_type="application/javascript")


# ── JSON endpoints ───────────────────────────────────────────────────────────
@router.get("/director/api/stores")
async def api_stores(request: Request, _: dict = Depends(require_director)):
    return {"stores": repo.stores(_engine(request))}


@router.get("/director/api/overview")
async def api_overview(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.overview(_engine(request), store_id, _days(days))


@router.get("/director/api/sales")
async def api_sales(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.sales(_engine(request), store_id, _days(days))


@router.get("/director/api/customers")
async def api_customers(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.customers(_engine(request), store_id, _days(days))


@router.get("/director/api/baskets")
async def api_baskets(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.baskets(_engine(request), store_id, _days(days))


@router.get("/director/api/referrals")
async def api_referrals(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.referrals(_engine(request), store_id, _days(days))


@router.get("/director/api/ai")
async def api_ai(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.ai(_engine(request), store_id, _days(days))


@router.get("/director/api/subscriptions")
async def api_subscriptions(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.subscriptions(_engine(request), store_id, _days(days))


@router.get("/director/api/engagement")
async def api_engagement(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.engagement(_engine(request), store_id, _days(days))


@router.get("/director/api/footfall")
async def api_footfall(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    return repo.footfall(_engine(request), store_id, _days(days))


@router.get("/director/api/vision")
async def api_vision(
    request: Request,
    store_id: Optional[int] = Query(None),
    days: int = Query(30),
    _: dict = Depends(require_director),
):
    # Reuse the existing vision analytics aggregation. For the fleet view,
    # restrict to director-included stores (excludes dev/test stores).
    engine = _engine(request)
    allowed = None if store_id is not None else repo.included_store_ids(engine)
    return vision_repo.get_analytics(engine, store_id, _days(days), store_ids=allowed)
