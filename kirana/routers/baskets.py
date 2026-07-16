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


# ── Baskets ───────────────────────────────────────────────────────────────────


@router.get("/baskets")
async def list_baskets(
    request: Request, include_archived: bool = False, user: dict = Depends(_auth)
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    return {"baskets": repo.get_baskets(int(sid), include_archived=include_archived)}


@router.post("/baskets")
async def create_basket(request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository
    from kirana.schemas import BasketCreate

    body = BasketCreate(**(await request.json()))
    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    basket = repo.create_basket(int(sid), body.model_dump())
    return basket


@router.put("/baskets/{basket_id}")
async def update_basket(basket_id: int, request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository
    from kirana.schemas import BasketCreate

    body = BasketCreate(**(await request.json()))
    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    basket = repo.update_basket(int(sid), basket_id, body.model_dump())
    if not basket:
        raise HTTPException(status_code=404, detail="Basket not found")
    return basket


@router.delete("/baskets/{basket_id}")
async def delete_basket(basket_id: int, request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    repo.delete_basket(int(sid), basket_id)
    return {"deleted": True}


@router.post("/baskets/{basket_id}/archive")
async def archive_basket(basket_id: int, request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    if not repo.set_basket_archived(int(sid), basket_id, True):
        raise HTTPException(status_code=404, detail="Basket not found")
    return {"archived": True}


@router.post("/baskets/{basket_id}/restore")
async def restore_basket(basket_id: int, request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    if not repo.set_basket_archived(int(sid), basket_id, False):
        raise HTTPException(status_code=404, detail="Basket not found")
    return {"archived": False}


# ── Store Associations ────────────────────────────────────────────────────────


@router.get("/associations")
async def list_associations(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    return {
        "associations": KiranaRepository(request.app.state.engine).list_associations(
            int(sid)
        )
    }


@router.get("/associations/heatmap")
async def association_heatmap(request: Request, user: dict = Depends(_auth)):
    """Per-apartment/area growth metrics (customers, orders, revenue, last order)."""
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    return {
        "heatmap": KiranaRepository(request.app.state.engine).get_association_heatmap(
            int(sid)
        )
    }


@router.post("/associations")
async def add_association(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    body = await request.json()
    name = body.get("name", "").strip()
    area_type = body.get("area_type", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    valid_types = {"apartment", "hostel", "school", "office", "colony"}
    if area_type not in valid_types:
        raise HTTPException(
            status_code=400, detail=f"area_type must be one of {valid_types}"
        )
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    result = KiranaRepository(request.app.state.engine).add_association(
        int(sid),
        name,
        area_type,
        body.get("estimated_households"),
        body.get("notes"),
    )
    return result


@router.patch("/associations/{association_id}")
async def update_association(
    association_id: int, request: Request, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    body = await request.json()
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    result = KiranaRepository(request.app.state.engine).update_association(
        association_id, int(sid), **body
    )
    if not result:
        raise HTTPException(status_code=404, detail="Association not found")
    return result


@router.delete("/associations/{association_id}")
async def delete_association(
    association_id: int, request: Request, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    ok = KiranaRepository(request.app.state.engine).delete_association(
        association_id, int(sid)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Association not found")
    return {"deleted": True}


# ── Basket Campaigns ──────────────────────────────────────────────────────────


@router.get("/campaigns/recommended")
async def get_recommended_campaigns(
    request: Request,
    store_id: int = 0,
    limit: int = 3,
    user: dict = Depends(_auth),
):
    """Returns top campaigns: general time-based + area-specific from associations."""
    # IDOR guard: non-admins are pinned to their own store, so a passed
    # store_id can't be used to read a competitor's campaign strategy.
    if user.get("role") == "admin":
        sid = store_id or user.get("store_id") or 0
    else:
        sid = user.get("store_id") or 0
    if not sid:
        raise HTTPException(status_code=400, detail="store_id required")
    from kirana.campaigns import (
        get_recommended_campaigns as _recommend,
        get_area_campaigns,
    )

    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    # General time/season campaigns
    general = _recommend(request.app.state.engine, int(sid), limit=min(limit, 5))

    # Area-specific campaigns from this store's associations
    associations = KiranaRepository(request.app.state.engine).list_associations(
        int(sid)
    )
    active_types = list({a["area_type"] for a in associations if a.get("is_active")})
    area = (
        get_area_campaigns(request.app.state.engine, int(sid), active_types)
        if active_types
        else []
    )

    # Merge: area campaigns first (they're more targeted), then general; deduplicate by campaign_id
    seen: set[str] = set()
    merged = []
    for c in [*area, *general]:
        if c["campaign_id"] not in seen:
            seen.add(c["campaign_id"])
            merged.append(c)

    return {"campaigns": merged[: min(limit + 2, 8)]}
