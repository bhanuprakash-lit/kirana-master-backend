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
    FcmTokenUpdate,
    LoginRequest,
    PhoneLoginRequest,
    RegisterStoreOwnerRequest,
    ChangePasswordRequest,
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


# ── Health ────────────────────────────────────────────────────────────────────


@router.get("/health", include_in_schema=True)
async def health(request: Request):
    return _svc(request).health()


# ── Auth ──────────────────────────────────────────────────────────────────────


def _telemetry(request: Request) -> dict:
    """Device/OS headers sent by the mobile app + the client IP, persisted in
    user_sessions for the admin Sessions page (see docs/TELEMETRY_SPEC.md).
    Honours X-Forwarded-For so we record the real client IP behind the proxy."""
    h = request.headers
    xff = h.get("X-Forwarded-For")
    ip = (
        xff.split(",")[0].strip()
        if xff
        else (request.client.host if request.client else None)
    )
    return {
        "device_brand": h.get("X-Device-Brand"),
        "device_model": h.get("X-Device-Model"),
        "os_name": h.get("X-OS-Name"),
        "os_version": h.get("X-OS-Version"),
        "ip_address": ip,
    }


@router.post("/auth/login")
async def login(request: Request, body: LoginRequest):
    try:
        return _svc(request).login(body, telemetry=_telemetry(request))
    except ValueError:
        logger.warning("Failed login attempt for user: %s", body.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/auth/register")
async def register(request: Request, body: RegisterStoreOwnerRequest):
    return (
        _svc(request)
        .register_store_owner(body, telemetry=_telemetry(request))
        .model_dump()
    )


@router.post("/auth/phone-login")
async def phone_login(request: Request, body: PhoneLoginRequest):
    """Log in using a Firebase-verified phone number. Returns 401 if no account exists."""
    try:
        return _svc(request).phone_login(body, telemetry=_telemetry(request))
    except ValueError:
        logger.warning("Phone login: no account for %s", body.phone_number[:4] + "****")
        raise HTTPException(
            status_code=404, detail="No account found for this phone number"
        )


@router.get("/auth/check-username/{username}")
async def check_username(username: str, request: Request):
    """Returns {available: bool} — call before registration to validate uniqueness."""
    available = _svc(request).check_username_available(username)
    return {"available": available, "username": username}


@router.get("/auth/me")
async def me(user: dict = Depends(_auth)):
    return user


@router.get("/catalog/search")
def catalog_search(
    request: Request,
    q: str = "",
    barcode: str = "",
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(_auth),
):
    """Search global product catalog by name (ILIKE) or barcode (exact)."""
    from sqlalchemy import text as _text

    engine = request.app.state.engine
    params: dict = {"limit": limit, "offset": offset}

    q = q.strip()
    barcode = barcode.strip()

    if barcode:
        where = "p.barcode = :barcode"
        params["barcode"] = barcode
    elif len(q) >= 2:
        where = "p.name ILIKE :q OR p.brand ILIKE :q"
        params["q"] = f"%{q}%"
    else:
        return {"products": []}

    # Pull the most-recent active pricing row for the caller's store so the
    # Flutter "Add Product" sheet can pre-fill price + MRP. Falls back to NULL
    # when this store has no pricing row (truly first-time use).
    store_id = user.get("store_id") or 0
    params["store_id"] = store_id

    # Store 27 operates on the curated product_catalog (barcoded + loose only)
    from pos.crud import CATALOG_STORES as _CATALOG_STORES

    product_tbl = (
        "kirana_oltp.product_catalog"
        if store_id in _CATALOG_STORES
        else "kirana_oltp.product"
    )

    # Vertical scope: only show catalog products whose category matches the
    # store's vertical (or shared NULL categories), so a mobile store searching
    # never gets grocery items. Skipped for admin/no-store callers.
    vertical_clause = ""
    if store_id:
        vertical_clause = (
            " AND (c.vertical_code IS NULL OR c.vertical_code = "
            "(SELECT COALESCE(s.vertical_code, 'grocery') "
            "FROM kirana_oltp.store s WHERE s.store_id = :store_id))"
        )

    sql = f"""
    SELECT p.product_id, p.name, p.brand, p.unit, p.weight,
           p.barcode, p.is_perishable, p.is_loose, p.image_url, p.sku,
           p.category_id,
           c.name AS category_name,
           pc.name AS parent_category_name,
           lp.price AS price,
           lp.mrp   AS mrp
    FROM {product_tbl} p
    JOIN kirana_oltp.category c ON p.category_id = c.category_id
    LEFT JOIN kirana_oltp.category pc ON c.parent_category_id = pc.category_id
    LEFT JOIN LATERAL (
        SELECT pr.price, pr.mrp
        FROM kirana_oltp.pricing pr
        WHERE pr.product_id = p.product_id
          AND pr.store_id   = :store_id
          AND pr.valid_from <= NOW()
        ORDER BY pr.valid_from DESC
        LIMIT 1
    ) lp ON TRUE
    WHERE ({where}){vertical_clause}
    ORDER BY p.name
    LIMIT :limit OFFSET :offset
    """
    with engine.connect() as conn:
        rows = conn.execute(_text(sql), params).mappings().all()
    return {"products": [dict(r) for r in rows]}


@router.get("/auth/password-status")
async def password_status(request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    return repo.get_password_status(user["user_id"])


@router.get("/vertical-config")
async def vertical_config(request: Request, user: dict = Depends(_auth)):
    """Foundation 1: the calling store's merged vertical config (feature flags,
    units, KPI/ML/tax profiles, copy). Drives config-gated UI in the app."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    return repo.get_vertical_config(user.get("store_id") or 0)


@router.post("/auth/change-password")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    user: dict = Depends(_auth),
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    repo = KiranaRepository(request.app.state.engine)
    try:
        repo.change_password(user["user_id"], body.old_password, body.new_password)
    except ValueError:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    return {"success": True}


@router.post("/auth/fcm-token")
async def update_fcm_token(
    request: Request, body: FcmTokenUpdate, user: dict = Depends(_auth)
):
    ok = _svc(request).update_fcm_token(user["user_id"], body.fcm_token)
    return {"success": ok}
