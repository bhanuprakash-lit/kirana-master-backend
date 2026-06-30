import logging
from typing import TYPE_CHECKING
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from pydantic import BaseModel

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

from kirana.schemas import (
    SubscriptionUpgradeRequest,
)
from kirana.service import KiranaService
from .stores import _get_store_owner_id

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


# ── Subscription ──────────────────────────────────────────────────────────────


@router.get("/subscription")
async def get_subscription(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    sub = _svc(request).get_active_subscription(int(sid))
    if sub is None:
        return {"has_active": False}
    return {"has_active": True, **sub}


class _TrialRequest(BaseModel):
    tier: str = "basic"  # "basic" or "pro"


@router.post("/subscription/request-trial")
async def request_trial(
    request: Request, body: _TrialRequest = _TrialRequest(), user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    tier = body.tier if body.tier in ("basic", "pro") else "basic"
    svc = _svc(request)
    result = svc.request_trial(int(sid), tier)

    # Auto-approve if the admin setting is enabled
    auto = svc.get_admin_setting("auto_approve_trial", "false").lower() == "true"
    if auto:
        trial_days = request.app.state.settings.trial_days
        try:
            result = svc.approve_trial(int(sid), trial_days)
            result["auto_approved"] = True
        except Exception:
            pass  # leave as pending_trial if approve fails

    return result


@router.post("/subscription/cancel")
async def cancel_subscription(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    try:
        return _svc(request).cancel_subscription(int(sid))
    except ValueError:
        raise HTTPException(status_code=400, detail="Cannot cancel subscription")


@router.post("/subscription/upgrade")
async def upgrade_subscription(
    request: Request, body: SubscriptionUpgradeRequest, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    try:
        return _svc(request).upgrade_subscription(int(sid), body.tier)
    except ValueError:
        raise HTTPException(status_code=400, detail="Cannot upgrade subscription")


@router.post("/subscription/send-reminder")
async def send_subscription_reminder(
    request: Request,
    user: dict = Depends(_auth),
):
    user_id = user.get("user_id")
    if not user_id:
        return {"sent": False}
    # The app sends days_left/message in the JSON body — read them from there.
    # (Bare scalar params would be parsed as query params and silently stay at
    # their defaults, making every reminder say "Trial Expired".)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Source of truth = the live subscription, NOT the client. A stale/buggy app
    # (or a body the server can't read) must never be able to turn a perfectly
    # valid trial into a "Trial Expired" push. We only fall back to the client
    # value when there's no subscription row to read.
    sid = user.get("store_id")
    days_left = None
    expired = None
    if sid is not None:
        try:
            sub = _svc(request).get_active_subscription(int(sid))
        except Exception:
            sub = None
        if sub:
            days_left = sub.get("days_remaining")
            expired = bool(sub.get("is_expired"))
    if days_left is None:
        days_left = int(payload.get("days_left") or 0)
    if expired is None:
        expired = days_left <= 0

    message = (payload.get("message") or "").strip()
    # Title is localized by the app and passed in; fall back to English.
    title_in = (payload.get("title") or "").strip()
    title = title_in or (
        "Kirana AI Trial Expiring" if not expired else "Kirana AI Trial Expired"
    )
    body = message or (
        f"Your trial ends in {days_left} day{'s' if days_left != 1 else ''}. Upgrade to continue."
        if not expired
        else "Your free trial has ended. Upgrade to keep your store running smoothly."
    )
    sent = _svc(request).send_fcm_to_user(
        user_id,
        title,
        body,
        data={
            "action": "open_subscription",
            "days_left": str(days_left),
            "channel": "kirana_account",
        },
    )
    return {"sent": sent}


# ── Admin — subscription approval ─────────────────────────────────────────────


@router.post("/admin/approve-trial/{store_id}")
async def approve_trial(store_id: int, request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    trial_days = request.app.state.settings.trial_days
    try:
        result = _svc(request).approve_trial(store_id, trial_days)
        # Send FCM to notify the user
        from sqlalchemy import text as _text

        with request.app.state.engine.connect() as conn:
            row = (
                conn.execute(
                    _text(
                        "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND role = 'store_owner' LIMIT 1"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .first()
            )
        if row:
            trial_tier = result.get("trial_tier", "basic")
            tier_label = "Pro" if trial_tier == "pro" else "Basic"
            sent = _svc(request).send_fcm_to_user(
                row["user_id"],
                f"Your Kirana AI {tier_label} Trial is Active!",
                f"Your {tier_label} trial has been activated. You have {trial_days} days to explore {tier_label} features.",
                data={"action": "open_subscription", "channel": "kirana_account"},
            )
            logger.info(
                "approve_trial: FCM to user_id=%s sent=%s", row["user_id"], sent
            )
        else:
            logger.warning(
                "approve_trial: no store_owner found for store_id=%s — FCM skipped",
                store_id,
            )
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Cannot approve trial")


class _ExtendTrialRequest(BaseModel):
    days: int = 7


@router.post("/admin/extend-trial/{store_id}")
async def extend_trial(
    store_id: int,
    request: Request,
    body: _ExtendTrialRequest = _ExtendTrialRequest(),
    user: dict = Depends(_auth),
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        result = _svc(request).extend_trial(store_id, body.days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Notify the store owner that their trial got more time.
    owner_id = _get_store_owner_id(request, store_id)
    if owner_id:
        days_left = result.get("days_remaining", body.days)
        _svc(request).send_fcm_to_user(
            owner_id,
            "Your Kirana AI Trial Was Extended",
            f"Good news! Your trial has been extended — you now have {days_left} "
            f"day{'s' if days_left != 1 else ''} remaining.",
            data={"action": "open_subscription", "channel": "kirana_account"},
        )
    return result


@router.get("/admin/settings")
async def get_admin_settings(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    svc = _svc(request)
    return {
        "auto_approve_trial": svc.get_admin_setting("auto_approve_trial", "false").lower() == "true",
    }


@router.post("/admin/settings")
async def update_admin_settings(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    svc = _svc(request)
    if "auto_approve_trial" in body:
        svc.set_admin_setting("auto_approve_trial", "true" if body["auto_approve_trial"] else "false")
    return {
        "auto_approve_trial": svc.get_admin_setting("auto_approve_trial", "false").lower() == "true",
    }


@router.get("/admin/pending-trials")
async def list_pending_trials(request: Request, user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _text("""
            SELECT s.store_id, s.started_at, st.name AS store_name,
                   COALESCE(s.requested_tier, 'basic') AS requested_tier
            FROM kirana_oltp.subscription s
            JOIN kirana_oltp.store st ON st.store_id = s.store_id
            WHERE s.tier = 'pending_trial'
            ORDER BY s.started_at DESC
        """)
            )
            .mappings()
            .all()
        )
    return {"pending": [dict(r) for r in rows]}


@router.get("/kpis/tiers")
async def get_kpi_tiers(request: Request, user: dict = Depends(_auth)):
    """Returns {kpi_id: 'basic'|'pro'} for every KPI in the registry.
    DB config wins; missing entries fall back to the default rule:
    'Core Insight' category → pro, first 3 per other category → basic, rest → pro.
    """
    from kpis import registry as kpi_registry

    db_config = _get_kpi_tier_config(request)
    all_kpis = kpi_registry.all_kpis()
    category_counts: dict[str, int] = {}
    tiers: dict[str, str] = {}
    for kpi in all_kpis:
        if kpi.kpi_id in db_config:
            tiers[kpi.kpi_id] = db_config[kpi.kpi_id]
            continue
        cat = kpi.category
        if cat.lower() in ("core insight", "common"):
            tiers[kpi.kpi_id] = "pro"
        else:
            idx = category_counts.get(cat, 0)
            tiers[kpi.kpi_id] = "basic" if idx < 3 else "pro"
            category_counts[cat] = idx + 1
    return {"tiers": tiers}


@router.get("/admin/kpi-tiers")
async def admin_get_kpi_tiers(request: Request, user: dict = Depends(_auth)):
    """Admin view: all KPIs with their current tier assignment."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from kpis import registry as kpi_registry

    db_config = _get_kpi_tier_config(request)
    all_kpis = kpi_registry.all_kpis()
    category_counts: dict[str, int] = {}
    result = []
    for kpi in all_kpis:
        if kpi.kpi_id in db_config:
            tier = db_config[kpi.kpi_id]
        else:
            cat = kpi.category
            if cat.lower() in ("core insight", "common"):
                tier = "pro"
            else:
                idx = category_counts.get(cat, 0)
                tier = "basic" if idx < 3 else "pro"
                category_counts[cat] = idx + 1
        result.append(
            {
                "kpi_id": kpi.kpi_id,
                "name": kpi.name,
                "category": kpi.category,
                "tier": tier,
                "is_custom": kpi.kpi_id in db_config,
            }
        )
    return {"kpis": result}


@router.put("/admin/kpi-tiers")
async def admin_save_kpi_tiers(request: Request, user: dict = Depends(_auth)):
    """Admin: bulk-save tier assignments. Body: {configs: [{kpi_id, tier}]}"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    configs = body.get("configs", [])
    if not isinstance(configs, list):
        raise HTTPException(status_code=400, detail="configs must be a list")
    for c in configs:
        if c.get("tier") not in ("basic", "pro"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tier '{c.get('tier')}' for {c.get('kpi_id')}",
            )
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    KiranaRepository(request.app.state.engine).upsert_kpi_tier_config(configs)
    return {"saved": len(configs)}


def _get_kpi_tier_config(request: Request) -> dict[str, str]:
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    return KiranaRepository(request.app.state.engine).get_kpi_tier_config()


# ── F4 — per-vertical KPI visibility (admin-controlled, live) ──────────────────


@router.get("/admin/kpi-visibility")
async def admin_get_kpi_visibility(request: Request, user: dict = Depends(_auth)):
    """Admin matrix: every KPI × the verticals it applies to, with its default
    and effective (override-applied) visibility. Drives the admin toggle grid."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from kpis import registry as r
    from kirana.repositories.main import KiranaRepository

    overrides = KiranaRepository(request.app.state.engine).get_kpi_visibility_config()
    items = []
    for k in r.all_kpis():
        for vc in (k.verticals or r.KNOWN_VERTICALS):
            ov = overrides.get((k.kpi_id, vc))
            items.append({
                "kpi_id": k.kpi_id,
                "name": k.name,
                "category": k.category,
                "status": k.status,
                "vertical_code": vc,
                "default_visible": r.default_visible(k),
                "visible": ov if ov is not None else r.default_visible(k),
                "overridden": ov is not None,
                "missing_data": k.missing_data,
            })
    return {"verticals": r.KNOWN_VERTICALS, "items": items}


@router.put("/admin/kpi-visibility")
async def admin_save_kpi_visibility(request: Request, user: dict = Depends(_auth)):
    """Admin: bulk-save visibility. Body: {configs:[{kpi_id, vertical_code, is_visible}]}"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    body = await request.json()
    configs = body.get("configs", [])
    if not isinstance(configs, list):
        raise HTTPException(status_code=400, detail="configs must be a list")
    clean = []
    for c in configs:
        if not c.get("kpi_id") or not c.get("vertical_code") or not isinstance(c.get("is_visible"), bool):
            raise HTTPException(status_code=400, detail="each config needs kpi_id, vertical_code, is_visible(bool)")
        clean.append({
            "kpi_id": c["kpi_id"],
            "vertical_code": c["vertical_code"],
            "is_visible": c["is_visible"],
        })
    from kirana.repositories.main import KiranaRepository

    KiranaRepository(request.app.state.engine).upsert_kpi_visibility_config(clean)
    return {"saved": len(clean)}


@router.get("/kpis/visible")
async def get_visible_kpis(request: Request, user: dict = Depends(_auth)):
    """App: the KPI set this store should show — applicable to its vertical and
    visible after admin overrides. Reflects admin changes live (no app update)."""
    from kpis import registry as r
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    vc = repo.get_vertical_config(user.get("store_id") or 0).get("vertical_code", "grocery")
    overrides = repo.get_kpi_visibility_config()
    kpis = r.visible_kpis_for(vc, overrides)
    return {
        "vertical_code": vc,
        "kpis": [r.kpi_to_metadata(k) for k in kpis],
    }
