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
    StoreUpdateRequest,
)
from kirana.service import KiranaService
from whatsapp.templates import basket_promo_payload

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


# ── Stores ────────────────────────────────────────────────────────────────────


@router.get("/stores")
async def list_stores(request: Request, user: dict = Depends(_auth)):
    stores = _svc(request).list_stores()
    if user.get("role") == "admin":
        return {"stores": stores}
    # Non-admins only see their own store
    filtered = [s for s in stores if s["store_id"] == user.get("store_id")]
    return {"stores": filtered}


@router.patch("/stores/{store_id}")
async def update_store(
    store_id: int,
    body: StoreUpdateRequest,
    request: Request,
    user: dict = Depends(_auth),
):
    _require_store(store_id, user)
    return _svc(request).update_store_profile(store_id, body)


# ── Basket tier config (per-store ranges + auto-discount) ─────────────────────


@router.get("/basket-tier-config")
async def get_basket_tier_config(request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    return {"config": repo.get_tier_config(int(sid))}


@router.put("/basket-tier-config")
async def put_basket_tier_config(request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    body = await request.json()
    config = repo.set_tier_config(int(sid), body.get("config", body))
    # Tiers freeze at creation — let the app offer to recompute existing baskets.
    return {"config": config, "existing_baskets": repo.count_active_baskets(int(sid))}


@router.post("/baskets/retier")
async def retier_baskets(request: Request, user: dict = Depends(_auth)):
    """Recompute tier/price for all existing baskets under the current config."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)
    updated = repo.retier_baskets(int(sid))
    return {"updated": updated}


@router.post("/baskets/{basket_id}/alert")
async def alert_basket_customers(
    basket_id: int, request: Request, user: dict = Depends(_auth)
):
    """Send WhatsApp message to all store customers about this basket deal."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository
    from sqlalchemy import text as _text

    sid = user.get("store_id") or 0
    repo = KiranaRepository(request.app.state.engine)

    # Once-per-day throttle — owners tap repeatedly otherwise, blasting N templates.
    if repo.basket_alerted_today(int(sid), basket_id):
        raise HTTPException(
            status_code=400,
            detail="You've already alerted customers about this basket today. Try again tomorrow.",
        )

    # Get basket details
    baskets = repo.get_baskets(int(sid))
    basket = next((b for b in baskets if b["basket_id"] == basket_id), None)
    if not basket:
        raise HTTPException(status_code=404, detail="Basket not found")

    # Fetch store name
    store_info = repo.get_store(int(sid))
    store_name = (
        store_info.get("store_name", "Our Store") if store_info else "Our Store"
    )

    # Build WhatsApp message payload parameters.
    # NOTE: basket_promo_payload formats price with `:,.2f`, so it must receive a
    # NUMBER, not a pre-formatted "₹…" string (that raises "Unknown format code
    # 'f' for object of type 'str'" for every recipient and nothing sends).
    name = basket["name"]
    price = float(basket["price"]) if basket.get("price") else 0.0
    valid_to = basket.get("valid_to") or "Available now"

    # Meta rejects template parameters containing newlines/tabs or >4 consecutive
    # spaces, so keep item_lines to a single comma-separated line.
    items_list = basket.get("items") or []
    item_lines = ", ".join(
        f"{it.get('product_name', 'Item')} x{it.get('qty', 1)}"
        for it in (items_list if isinstance(items_list, list) else [])
    )
    if not item_lines:
        item_lines = "Exciting items included!"

    # Fetch all customers with phone numbers
    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _text(
                    "SELECT phone FROM kirana_oltp.customer WHERE store_id = :sid AND phone IS NOT NULL AND phone != ''"
                ),
                {"sid": sid},
            )
            .mappings()
            .all()
        )
    phones = [r["phone"] for r in rows]

    wa_client = getattr(request.app.state, "wa_client", None)
    sent = 0
    last_error: str | None = None
    if wa_client and wa_client.is_configured:
        for phone in phones:
            try:
                payload = basket_promo_payload(
                    recipient=phone,
                    lang="en",  # Customers don't pick a language — promos are always English.
                    store_name=store_name,
                    basket_name=name,
                    price=price,
                    item_lines=item_lines,
                    valid_to=valid_to,
                )
                wa_client.send_template(payload)
                sent += 1
            except Exception as exc:
                # Don't hide the failure — a bad/unapproved template fails for
                # EVERY recipient identically, so surfacing the first error is
                # what tells the owner why nobody received the promo.
                last_error = str(exc)
                logger.warning("basket_promo send failed for store %s: %s", sid, exc)
    elif wa_client is not None:
        last_error = wa_client.config_error

    # Record the send so the once-per-day throttle kicks in (only if we reached anyone).
    if sent > 0:
        repo.mark_basket_alerted(int(sid), basket_id)

    return {
        "sent": sent,
        "total": len(phones),
        "error": last_error,
        "message": "Promo templates sent"
        if sent
        else (last_error or "No messages sent"),
    }


@router.post("/admin/cancel-subscription/{store_id}")
async def admin_cancel_subscription(
    store_id: int, request: Request, user: dict = Depends(_auth)
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        result = _svc(request).cancel_subscription(store_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Cannot cancel subscription")

    # Notify the user so the app refreshes and gates features immediately
    _svc(request).send_fcm_to_user(
        user_id=_get_store_owner_id(request, store_id),
        title="Subscription Cancelled",
        body="Your Kirana AI subscription has been cancelled. Please renew to continue.",
        data={
            "action": "subscription_cancelled",
            "route": "/profile/subscription",
            "channel": "kirana_account",
        },
    )
    return result


def _get_store_owner_id(request: Request, store_id: int) -> int:
    """Return user_id of the store owner, or 0 if not found."""
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        row = (
            conn.execute(
                _text(
                    "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid "
                    "AND role = 'store_owner' AND NOT COALESCE(is_deleted, FALSE) LIMIT 1"
                ),
                {"sid": store_id},
            )
            .mappings()
            .first()
        )
    return row["user_id"] if row else 0
