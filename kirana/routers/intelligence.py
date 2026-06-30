import logging
from typing import Optional, TYPE_CHECKING
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Default log path = the same file the logger writes to (main.py:
# <repo-root>/logs/master.log). In the container the repo root IS /app, so this
# still resolves to /app/logs/master.log; locally it resolves to the real path
# instead of the hardcoded container path. LOG_FILE env still overrides.
from log_config import log_memory as _log_memory

if TYPE_CHECKING:
    from kirana.intelligence.engine import IntelligenceEngine

from kirana.schemas import (
    AgentQueryRequest,
    ExplainRequest,
    RecommendationQueryRequest,
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


# ── Recommendations ───────────────────────────────────────────────────────────


@router.get("/recommendations")
async def query_recommendations(
    request: Request,
    store_id: Optional[int] = None,
    sku_ids: Optional[str] = None,
    top_n: int = 5,
    only_reorder: bool = False,
    only_high_priority: bool = False,
    recommendation_type: Optional[str] = None,
    sort_by: str = "expected_profit",
    user: dict = Depends(_auth),
):
    # Enforce store scoping
    sid = store_id or user.get("store_id")
    if sid:
        _require_store(sid, user)

    q = RecommendationQueryRequest(
        store_id=sid,
        sku_ids=[int(x) for x in sku_ids.split(",")] if sku_ids else None,
        top_n=top_n,
        only_reorder=only_reorder,
        only_high_priority=only_high_priority,
        recommendation_type=recommendation_type,
        sort_by=sort_by,
    )
    return _svc(request).query_recommendations(q)


@router.get("/stores/{store_id}/recommendations")
async def store_recommendations(
    store_id: int, request: Request, user: dict = Depends(_auth)
):
    _require_store(store_id, user)
    return _svc(request).store_recommendations(store_id)


# ── AI Agents ─────────────────────────────────────────────────────────────────


@router.post("/explain")
async def explain(request: Request, body: ExplainRequest, user: dict = Depends(_auth)):
    if body.store_id:
        _require_store(body.store_id, user)
    return _svc(request).explain(body)


@router.post("/query")
async def agent_query(
    request: Request, body: AgentQueryRequest, user: dict = Depends(_auth)
):
    if body.store_id:
        _require_store(body.store_id, user)
    return _svc(request).agent_query(body)


# ── Intelligence layer ────────────────────────────────────────────────────────


class CartPingRequest(BaseModel):
    item_count: int = 0
    items: list = []
    converted: bool = False  # True when an order was just completed


class NotificationOpenedRequest(BaseModel):
    log_id: int


@router.post("/intelligence/cart-ping")
async def cart_ping(
    request: Request, body: CartPingRequest, user: dict = Depends(_auth)
):
    """Flutter calls this every time the cart changes (debounced)."""
    sid = user.get("store_id")
    if not sid:
        raise HTTPException(status_code=403, detail="Store owner login required")
    store_id = int(sid)

    from kirana.intelligence.repository import IntelligenceRepository

    repo = IntelligenceRepository(request.app.state.engine)

    if body.converted:
        repo.mark_cart_converted(store_id)
    else:
        repo.upsert_cart_session(store_id, body.item_count, body.items)
    return {"ok": True}


@router.post("/intelligence/notification-opened")
async def notification_opened(
    request: Request, body: NotificationOpenedRequest, user: dict = Depends(_auth)
):
    """Flutter calls this when the user taps a push notification."""
    from kirana.intelligence.repository import IntelligenceRepository

    repo = IntelligenceRepository(request.app.state.engine)
    repo.mark_opened(body.log_id)
    return {"ok": True}


@router.get("/intelligence/logs")
async def intelligence_logs(
    request: Request, limit: int = 50, user: dict = Depends(_auth)
):
    """Returns recent intelligence notifications for this store (or all stores for admin)."""
    from kirana.intelligence.repository import IntelligenceRepository

    repo = IntelligenceRepository(request.app.state.engine)

    if user.get("role") == "admin":
        logs = repo.list_all_logs(limit=min(limit, 500))
    else:
        sid = user.get("store_id")
        if not sid:
            raise HTTPException(status_code=403, detail="Store owner login required")
        logs = repo.list_logs(int(sid), limit=min(limit, 100))

    return {"logs": logs, "count": len(logs)}


@router.get("/admin/logs")
async def admin_logs(
    request: Request,
    lines: int = 200,
    level: str = "",
    user: dict = Depends(_auth),
):
    """Return last N lines from the in-process log buffer. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    lines = max(1, min(lines, 2000))
    result = _log_memory.tail(lines, level)
    # NOTE: this buffer is per-process. With uvicorn workers>1 or multiple Azure
    # replicas it only reflects the worker that served this request — Azure Log
    # Analytics (stdout JSON) is the complete, cross-replica system of record.
    return {"lines": result, "total": len(result), "source": "this_worker_only"}


@router.get("/admin/logs/stream")
async def stream_logs(
    request: Request,
    tail: int = 100,
    user: dict = Depends(_auth),
):
    """SSE live tail from the in-process log buffer. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import asyncio
    import json as _json

    tail = max(10, min(tail, 500))

    async def _gen():
        # Seed with the buffer tail and capture the cursor atomically so we
        # stream from exactly where the seed ended (no gaps, no duplicates).
        seed, cursor = _log_memory.seed(tail)
        for entry in seed:
            yield f"data: {_json.dumps(entry)}\n\n"

        try:
            ka = 0
            while True:
                if await request.is_disconnected():
                    break
                new, cursor = _log_memory.read_since(cursor)
                if new:
                    ka = 0
                    for entry in new:
                        yield f"data: {_json.dumps(entry)}\n\n"
                else:
                    ka += 1
                    if ka >= 10:     # keepalive every ~10 s at 1 s poll
                        yield ": ka\n\n"
                        ka = 0
                await asyncio.sleep(1)
        except (asyncio.CancelledError, GeneratorExit):
            pass

    from starlette.responses import StreamingResponse as _SR

    return _SR(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/admin/intelligence/fire/{trigger_name}")
async def fire_trigger(
    trigger_name: str, request: Request, user: dict = Depends(_auth)
):
    """Manually fire an intelligence trigger immediately. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    engine: "IntelligenceEngine" = request.app.state.intelligence
    result = await engine.fire(trigger_name)
    return result
