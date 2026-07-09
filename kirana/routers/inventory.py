import logging
from typing import TYPE_CHECKING
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

from kirana.schemas import (
    InventorySnapshotWriteRequest,
    BatchMarkdownRequest,
    BatchWasteRequest,
    ReturnCreate,
    SetPriceRequest,
    SetCostRequest,
    SetCustomerPriceRequest,
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


# ── Snapshots / Inventory Ingestion ───────────────────────────────────────────


@router.post("/stores/{store_id}/snapshot")
async def ingest_snapshot(
    store_id: int,
    body: InventorySnapshotWriteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(_auth),
):
    _require_store(store_id, user)
    result = _svc(request).ingest_store_snapshot(store_id, body)
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    background_tasks.add_task(
        KiranaRepository(request.app.state.engine).compute_store_footfall, store_id
    )
    return result


@router.get("/stores/{store_id}/snapshot")
async def get_latest_snapshot(
    store_id: int, request: Request, user: dict = Depends(_auth)
):
    _require_store(store_id, user)
    return _svc(request).get_store_snapshot(store_id)


# ── Inventory: Expiry loss prevention ───────────────────────────────────────────


@router.get("/inventory/near-expiry")
async def near_expiry_batches(
    request: Request, days: int = 7, user: dict = Depends(_auth)
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    return {"batches": repo.get_near_expiry_batches(int(sid), days)}


@router.post("/inventory/batch/{batch_id}/markdown")
async def set_batch_markdown(
    batch_id: int,
    request: Request,
    body: BatchMarkdownRequest,
    user: dict = Depends(_auth),
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    try:
        return repo.set_batch_markdown(int(sid), batch_id, body.markdown_pct)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/inventory/batch/{batch_id}/waste")
async def record_batch_waste(
    batch_id: int,
    request: Request,
    body: BatchWasteRequest,
    user: dict = Depends(_auth),
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    try:
        return repo.record_batch_waste(int(sid), batch_id, body.units)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/inventory/reorder-suggestions")
async def reorder_suggestions(
    request: Request,
    cover_days: int = 14,
    lookback_days: int = 30,
    user: dict = Depends(_auth),
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    return {
        "suggestions": repo.get_reorder_suggestions(int(sid), cover_days, lookback_days)
    }


# ── Returns / exchanges (purchase memory) ───────────────────────────────────────


@router.post("/returns")
async def record_return(
    request: Request, body: ReturnCreate, user: dict = Depends(_auth)
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    items = [it.model_dump() for it in body.items]
    if not items:
        raise HTTPException(status_code=400, detail="No items to return")
    return repo.record_return(
        int(sid), body.order_id, items, body.reason,
        refund_amount=body.refund_amount, is_exchange=body.is_exchange,
        customer_id=body.customer_id)


@router.get("/customers/{customer_id}/purchases")
async def customer_purchases(
    customer_id: int, request: Request, user: dict = Depends(_auth)
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    return {"purchases": repo.get_customer_purchases(int(sid), customer_id)}


@router.get("/customers/{customer_id}/price-memory")
async def customer_price_memory(
    customer_id: int, request: Request, user: dict = Depends(_auth)
):
    """Per-customer price memory — products where this customer's last-paid price
    differs from the current catalog price (powers POS customer-specific pricing)."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    repo = KiranaRepository(request.app.state.engine)
    return {"prices": repo.get_customer_price_memory(int(sid), customer_id)}


@router.post("/customers/{customer_id}/price")
async def set_customer_price(
    customer_id: int,
    request: Request,
    body: SetCustomerPriceRequest,
    user: dict = Depends(_auth),
):
    """Pin (or, with price=null, remove) a customer-specific price for a product."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    if body.price is not None and body.price < 0:
        raise HTTPException(status_code=400, detail="Price must be ≥ 0")
    repo = KiranaRepository(request.app.state.engine)
    return repo.set_customer_product_price(
        int(sid), customer_id, body.product_id, body.price
    )


# ── AI Price Memory (forgotten / unset prices) ──────────────────────────────────


@router.get("/inventory/missing-prices")
async def missing_prices(request: Request, user: dict = Depends(_auth)):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return {
        "products": KiranaRepository(request.app.state.engine).get_missing_prices(
            int(sid)
        )
    }


@router.post("/inventory/price")
async def set_product_price(
    request: Request, body: SetPriceRequest, user: dict = Depends(_auth)
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    if body.price < 0:
        raise HTTPException(status_code=400, detail="Price must be ≥ 0")
    return KiranaRepository(request.app.state.engine).set_product_price(
        int(sid), body.product_id, body.price, body.mrp
    )


@router.get("/inventory/flags")
async def inventory_flags(request: Request, user: dict = Depends(_auth)):
    """Per-product ML flags (fast_moving / reorder_now / dead_stock / stockout_risk
    / profit_opportunity) for the store — used to tag items in inventory/POS."""
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    flags = _svc(request).ml.flags_for_store(int(sid))
    return {"flags": {str(pid): types for pid, types in flags.items()}}


@router.post("/inventory/cost")
async def set_product_cost(
    request: Request, body: SetCostRequest, user: dict = Depends(_auth)
):
    """Capture a product's real purchase cost (product_supplier.cost_price)."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    if body.cost_price < 0:
        raise HTTPException(status_code=400, detail="Cost must be ≥ 0")
    return KiranaRepository(request.app.state.engine).set_product_cost(
        body.product_id, body.cost_price, body.supplier_id
    )


# ── Admin — Product Inventory Management ──────────────────────────────────────


@router.get("/admin/categories")
async def admin_list_categories(request: Request, user: dict = Depends(_auth)):
    """All product categories — used by the admin inventory editor."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    with request.app.state.engine.connect() as conn:
        rows = (
            conn.execute(
                _text("""
            SELECT category_id, name, parent_category_id, vertical_code
            FROM   kirana_oltp.category
            ORDER  BY name
        """)
            )
            .mappings()
            .all()
        )
    return {"categories": [dict(r) for r in rows]}


@router.get("/admin/products")
async def admin_list_products(
    request: Request,
    q: str = "",
    category_id: int = 0,
    vertical: str = "",  # filter by the category's vertical_code
    has_barcode: str = "",  # "yes" | "no" | ""
    is_loose: str = "",  # "yes" | "no" | ""
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(_auth),
):
    """Paginated product list with search + filters. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    from sqlalchemy import text as _text

    limit = min(max(limit, 1), 100)
    conditions: list[str] = []
    base_params: dict = {}

    if q.strip():
        conditions.append(
            "(p.name ILIKE :q OR p.brand ILIKE :q OR p.barcode ILIKE :q OR p.sku ILIKE :q)"
        )
        base_params["q"] = f"%{q.strip()}%"
    if category_id:
        conditions.append("p.category_id = :cat_id")
        base_params["cat_id"] = category_id
    if vertical.strip():
        conditions.append("c.vertical_code = :vertical")
        base_params["vertical"] = vertical.strip()
    if has_barcode == "yes":
        conditions.append("p.barcode IS NOT NULL AND p.barcode <> ''")
    elif has_barcode == "no":
        conditions.append("(p.barcode IS NULL OR p.barcode = '')")
    if is_loose == "yes":
        conditions.append("p.is_loose = TRUE")
    elif is_loose == "no":
        conditions.append("(p.is_loose = FALSE OR p.is_loose IS NULL)")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with request.app.state.engine.connect() as conn:
        total = conn.execute(
            _text(f"SELECT COUNT(*) FROM kirana_oltp.product p "
                  f"LEFT JOIN kirana_oltp.category c ON p.category_id = c.category_id {where}"),
            base_params,
        ).scalar()
        rows = (
            conn.execute(
                _text(f"""
            SELECT p.product_id, p.name, p.brand, p.unit, p.weight,
                   p.barcode, p.sku, p.image_url,
                   p.is_loose, p.is_perishable, p.is_private_label,
                   p.category_id, p.created_at,
                   c.name AS category_name, c.vertical_code
            FROM   kirana_oltp.product p
            LEFT   JOIN kirana_oltp.category c ON p.category_id = c.category_id
            {where}
            ORDER  BY p.name
            LIMIT  :limit OFFSET :offset
        """),
                {**base_params, "limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )

    return {
        "products": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/admin/products/{product_id}")
async def admin_update_product(
    product_id: int,
    request: Request,
    user: dict = Depends(_auth),
):
    """Update editable fields of a product. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    body = await request.json()

    ALLOWED = {
        "name",
        "brand",
        "unit",
        "weight",
        "barcode",
        "sku",
        "image_url",
        "is_loose",
        "is_perishable",
        "is_private_label",
        "category_id",
    }
    updates = {k: v for k, v in body.items() if k in ALLOWED}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    if "name" in updates and not str(updates.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="Product name cannot be empty")

    # Normalise empty strings → NULL for nullable fields
    for field in ("barcode", "sku", "brand", "unit", "image_url"):
        if field in updates and updates[field] == "":
            updates[field] = None

    from sqlalchemy import text as _text
    from sqlalchemy.exc import IntegrityError as _IntegrityError

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["_pid"] = product_id

    try:
        with request.app.state.engine.connect() as conn:
            result = conn.execute(
                _text(
                    f"UPDATE kirana_oltp.product SET {set_clause} WHERE product_id = :_pid"
                ),
                updates,
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Product not found")
            conn.commit()
    except HTTPException:
        raise
    except _IntegrityError as exc:
        msg = str(exc.orig)
        if "product_barcode_key" in msg:
            raise HTTPException(
                status_code=409,
                detail="This barcode is already used by another product",
            )
        if "product_sku_key" in msg:
            raise HTTPException(
                status_code=409,
                detail="This SKU is already assigned to another product",
            )
        if "product_category_id_fkey" in msg:
            raise HTTPException(status_code=400, detail="Invalid category ID")
        logger.warning(
            "Product update constraint violation for product_id=%s: %s", product_id, exc
        )
        raise HTTPException(status_code=409, detail="Database constraint violation")
    except Exception:
        logger.exception("Failed to update product %s", product_id)
        raise HTTPException(status_code=500, detail="Failed to update product")

    return {"success": True, "product_id": product_id}
