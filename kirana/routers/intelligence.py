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
import os as _os_mod
_DEFAULT_LOG_FILE = _os_mod.path.join(
    _os_mod.path.dirname(_os_mod.path.dirname(_os_mod.path.dirname(_os_mod.path.abspath(__file__)))),
    "logs", "master.log",
)

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
    """Stream last N lines from the server log file. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import os as _os

    log_path = _os.environ.get("LOG_FILE", _DEFAULT_LOG_FILE)

    if not _os.path.exists(log_path):
        return {
            "lines": [],
            "total": 0,
            "log_path": log_path,
            "error": "Log file not found",
        }

    lines = max(1, min(lines, 2000))
    level_filter = level.upper() if level else ""

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()

        tail = all_lines[-lines * 3 if level_filter else -lines :]

        parsed = []
        for raw in tail:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            lvl = "INFO"
            for candidate in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
                if candidate in raw:
                    lvl = candidate
                    break
            if level_filter and lvl != level_filter:
                continue
            parsed.append({"raw": raw, "level": lvl})

        result = parsed[-lines:]
        return {"lines": result, "total": len(result), "log_path": log_path}
    except Exception as exc:
        logger.exception("Failed to read log file %s", log_path)
        raise HTTPException(status_code=500, detail=f"Could not read logs: {exc}")


@router.get("/admin/logs/stream")
async def stream_logs(
    request: Request,
    tail: int = 100,
    user: dict = Depends(_auth),
):
    """SSE live tail of the server log file. Sends last `tail` lines on connect, then streams new lines."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import os as _os
    import asyncio
    import json as _json

    log_path = _os.environ.get("LOG_FILE", _DEFAULT_LOG_FILE)
    tail = max(10, min(tail, 500))

    def _level(line: str) -> str:
        for lvl in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
            if f"[{lvl}]" in line:
                return lvl
        return "INFO"

    async def _gen():
        # Always emit the {raw, level} shape the client renders; a bare {error}
        # object has no `raw` and crashes the log row (e.g. when LOG_FILE is unset
        # locally and points at a path that doesn't exist).
        if not _os.path.exists(log_path):
            yield f"data: {_json.dumps({'raw': f'Log file not found: {log_path}', 'level': 'ERROR'})}\n\n"
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
                end_pos = fh.tell()
        except OSError as exc:
            yield f"data: {_json.dumps({'raw': f'Could not read log file: {exc}', 'level': 'ERROR'})}\n\n"
            return

        for line in lines[-tail:]:
            line = line.rstrip()
            if line:
                yield f"data: {_json.dumps({'raw': line, 'level': _level(line)})}\n\n"

        ka = 0
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(end_pos)
                while True:
                    if await request.is_disconnected():
                        break
                    chunk = fh.read()
                    if chunk:
                        ka = 0
                        for line in chunk.splitlines():
                            line = line.strip()
                            if line:
                                yield f"data: {_json.dumps({'raw': line, 'level': _level(line)})}\n\n"
                    else:
                        ka += 1
                        if ka >= 5:
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
