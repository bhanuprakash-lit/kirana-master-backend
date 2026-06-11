"""
POS CRUD — operates on kirana_oltp schema tables in lit_db.
Auth users are resolved from kirana_app_users (created by KiranaRepository).
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import func, or_, text

from pos.models import (
    KiranaStore, KiranaCategory, KiranaProduct,
    KiranaPricing, KiranaInventory,
    KiranaOrder, KiranaOrderItem, KiranaPayment,
)
from pos.schemas import OrderCreate, PaymentCreate


# ── Store-level product table routing ────────────────────────────────────────
# Stores listed here use product_catalog (barcoded + loose items only)
# instead of the full product table.
CATALOG_STORES: frozenset[int] = frozenset({27})

def _product_tbl(store_id: int) -> str:
    return "kirana_oltp.product_catalog" if store_id in CATALOG_STORES else "kirana_oltp.product"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_price(db: Session, product_id: int, store_id: int) -> KiranaPricing | None:
    """Most recent active pricing entry for (product, store)."""
    now = datetime.utcnow()
    return (
        db.query(KiranaPricing)
        .filter(
            KiranaPricing.product_id == product_id,
            KiranaPricing.store_id   == store_id,
            KiranaPricing.valid_from <= now,
        )
        .order_by(KiranaPricing.valid_from.desc())
        .first()
    )


def _inventory(db: Session, product_id: int, store_id: int) -> KiranaInventory | None:
    return (
        db.query(KiranaInventory)
        .filter(
            KiranaInventory.product_id == product_id,
            KiranaInventory.store_id   == store_id,
        )
        .first()
    )


def _earliest_expiry(db: Session, product_id: int, store_id: int) -> str | None:
    """Get the earliest expiry date from active batches for this product."""
    # Note: query uses raw SQL because inventory_batch might not be in POS models yet
    sql = """
        SELECT MIN(expiry_date) 
        FROM kirana_oltp.inventory_batch 
        WHERE product_id = :pid AND store_id = :sid AND qty_in_stock > 0
    """
    res = db.execute(text(sql), {"pid": product_id, "sid": store_id}).scalar()
    return res.strftime("%Y-%m-%d") if res and hasattr(res, 'strftime') else None


# ── Stores ────────────────────────────────────────────────────────────────────

def get_stores(db: Session) -> list[KiranaStore]:
    return db.query(KiranaStore).filter(KiranaStore.is_deleted == False).all()


def get_store(db: Session, store_id: int) -> KiranaStore | None:
    return db.query(KiranaStore).filter(
        KiranaStore.store_id == store_id,
        KiranaStore.is_deleted == False,
    ).first()


# ── Categories ────────────────────────────────────────────────────────────────

def get_categories(db: Session) -> list[KiranaCategory]:
    return db.query(KiranaCategory).order_by(KiranaCategory.name).all()


# ── Products ──────────────────────────────────────────────────────────────────

def _enrich(db: Session, product: KiranaProduct, store_id: int) -> dict:
    pricing = _current_price(db, product.product_id, store_id)
    inv     = _inventory(db,    product.product_id, store_id)
    expiry  = _earliest_expiry(db, product.product_id, store_id)
    
    return {
        "product_id":    product.product_id,
        "name":          product.name,
        "brand":         product.brand,
        "unit":          product.unit,
        "weight":        float(product.weight) if product.weight else None,
        "sku":           product.sku,
        "barcode":       product.barcode,
        "is_perishable": product.is_perishable,
        "is_loose":      product.is_loose,
        "image_url":     product.image_url,
        "category_id":   product.category_id,
        "price":         float(pricing.selling_price) if pricing and pricing.selling_price is not None else 0.0,
        "mrp":           float(pricing.mrp) if pricing and pricing.mrp is not None else None,
        "stock_quantity": inv.quantity if inv else 0,
        "expiry_date":   expiry,
    }


def get_products(db: Session, store_id: int, skip: int = 0, limit: int = 100) -> list[dict]:
    tbl = _product_tbl(store_id)
    rows = db.execute(text(f"""
        SELECT p.product_id, p.name, p.brand, p.unit,
               p.weight::float            AS weight,
               p.sku, p.barcode, p.is_perishable, p.is_loose, p.image_url, p.category_id,
               COALESCE(pr.price,    0.0)::float AS price,
               pr.mrp::float                     AS mrp,
               COALESCE(inv.quantity, 0)          AS stock_quantity,
               TO_CHAR(MIN(ib.expiry_date), 'YYYY-MM-DD') AS expiry_date
        FROM   {tbl} p
        JOIN   kirana_oltp.inventory inv
                   ON inv.product_id = p.product_id AND inv.store_id = :sid
        LEFT   JOIN LATERAL (
                   SELECT price, mrp
                   FROM   kirana_oltp.pricing
                   WHERE  product_id = p.product_id
                     AND  store_id   = :sid
                     AND  valid_from <= NOW()
                   ORDER  BY valid_from DESC LIMIT 1
               ) pr ON TRUE
        LEFT   JOIN kirana_oltp.inventory_batch ib
                   ON ib.product_id = p.product_id
                  AND ib.store_id   = :sid
                  AND ib.qty_in_stock > 0
        GROUP  BY p.product_id, p.name, p.brand, p.unit, p.weight,
                  p.sku, p.barcode, p.is_perishable, p.is_loose,
                  p.image_url, p.category_id, pr.price, pr.mrp, inv.quantity
        ORDER  BY p.product_id
        LIMIT  :limit OFFSET :skip
    """), {"sid": store_id, "limit": limit, "skip": skip}).mappings().all()
    return [dict(r) for r in rows]


def get_product(db: Session, product_id: int, store_id: int) -> dict | None:
    tbl = _product_tbl(store_id)
    if store_id in CATALOG_STORES:
        row = db.execute(text(f"""
            SELECT p.product_id, p.name, p.brand, p.unit,
                   p.weight::float            AS weight,
                   p.sku, p.barcode, p.is_perishable, p.is_loose, p.image_url, p.category_id,
                   COALESCE(pr.price,    0.0)::float AS price,
                   pr.mrp::float                     AS mrp,
                   COALESCE(inv.quantity, 0)          AS stock_quantity,
                   TO_CHAR(MIN(ib.expiry_date), 'YYYY-MM-DD') AS expiry_date
            FROM   {tbl} p
            JOIN   kirana_oltp.inventory inv
                       ON inv.product_id = p.product_id AND inv.store_id = :sid
            LEFT   JOIN LATERAL (
                       SELECT price, mrp
                       FROM   kirana_oltp.pricing
                       WHERE  product_id = p.product_id
                         AND  store_id   = :sid
                         AND  valid_from <= NOW()
                       ORDER  BY valid_from DESC LIMIT 1
                   ) pr ON TRUE
            LEFT   JOIN kirana_oltp.inventory_batch ib
                       ON ib.product_id = p.product_id
                      AND ib.store_id   = :sid
                      AND ib.qty_in_stock > 0
            WHERE  p.product_id = :pid
            GROUP  BY p.product_id, p.name, p.brand, p.unit, p.weight,
                      p.sku, p.barcode, p.is_perishable, p.is_loose,
                      p.image_url, p.category_id, pr.price, pr.mrp, inv.quantity
            LIMIT  1
        """), {"sid": store_id, "pid": product_id}).mappings().first()
        return dict(row) if row else None
    # ── default: full product table (ORM path) ────────────────────────────────
    p = (
        db.query(KiranaProduct)
        .join(KiranaInventory, KiranaInventory.product_id == KiranaProduct.product_id)
        .filter(
            KiranaProduct.product_id == product_id,
            KiranaInventory.store_id == store_id,
        )
        .first()
    )
    return _enrich(db, p, store_id) if p else None


def get_product_by_barcode(db: Session, barcode: str, store_id: int) -> dict | None:
    tbl = _product_tbl(store_id)
    sql = f"""
        SELECT
            p.product_id, p.name, p.brand, p.unit,
            p.weight::float            AS weight,
            p.sku, p.barcode, p.is_perishable, p.is_loose, p.category_id,
            COALESCE(pr.price, 0.0)::float  AS price,
            pr.mrp::float                   AS mrp,
            COALESCE(inv.quantity, 0)        AS stock_quantity,
            TO_CHAR(MIN(ib.expiry_date), 'YYYY-MM-DD') AS expiry_date
        FROM   {tbl} p
        JOIN   kirana_oltp.inventory inv
                   ON inv.product_id = p.product_id AND inv.store_id = :sid
        LEFT   JOIN LATERAL (
                   SELECT price, mrp
                   FROM   kirana_oltp.pricing
                   WHERE  product_id = p.product_id
                     AND  store_id   = :sid
                     AND  valid_from <= NOW()
                   ORDER  BY valid_from DESC
                   LIMIT  1
               ) pr ON TRUE
        LEFT   JOIN kirana_oltp.inventory_batch ib
                   ON ib.product_id = p.product_id
                  AND ib.store_id   = :sid
                  AND ib.qty_in_stock > 0
        WHERE  p.barcode = :barcode
        GROUP  BY p.product_id, p.name, p.brand, p.unit, p.weight, p.sku,
                  p.barcode, p.is_perishable, p.is_loose, p.category_id,
                  pr.price, pr.mrp, inv.quantity
        LIMIT  1
    """
    row = db.execute(text(sql), {"barcode": barcode, "sid": store_id}).mappings().first()
    return dict(row) if row else None


# ── Orders ────────────────────────────────────────────────────────────────────

def create_order(db: Session, order: OrderCreate, user_id: int, store_id: int) -> KiranaOrder:
    db_order = KiranaOrder(
        store_id=store_id,
        user_id=user_id,
        customer_id=order.customer_id,
        order_status="completed",
        order_date=datetime.utcnow(),
        total_amount=0,
        udhaar_amount=order.udhaar_amount,
        cash_paid=order.cash_paid,
    )
    db.add(db_order)
    db.flush()

    total = 0.0
    for item in order.items:
        product = db.query(KiranaProduct).filter(KiranaProduct.product_id == item.product_id).first()
        if not product:
            db.rollback()
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")

        inv = _inventory(db, item.product_id, store_id)
        if not inv or inv.quantity < item.quantity:
            db.rollback()
            stock = inv.quantity if inv else 0
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for {product.name} (have {stock}, need {item.quantity})",
            )

        pricing = _current_price(db, item.product_id, store_id)
        unit_price = item.unit_price if item.unit_price is not None else (item.selling_price if item.selling_price is not None else (float(pricing.selling_price) if pricing else 0.0))
        cost_price = 0.0
        sp = db.execute(text(
            "SELECT cost_price FROM kirana_oltp.product_supplier WHERE product_id = :pid LIMIT 1"
        ), {"pid": item.product_id}).first()
        if sp:
            cost_price = float(sp[0])

        total += unit_price * item.quantity

        db.add(KiranaOrderItem(
            order_id=db_order.order_id,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price=unit_price,
            cost_price=cost_price,
        ))

    # Use caller-supplied total if provided (e.g. after referral discount), else sum from items
    final_total = float(order.total_amount) if order.total_amount is not None else total
    db_order.total_amount = final_total
    db.flush()

    # Auto-create payment record so payment_method filter works correctly
    db.add(KiranaPayment(
        order_id=db_order.order_id,
        amount=final_total,
        payment_method=order.payment_method,
        status="paid",
        created_at=datetime.utcnow(),
    ))

    # ── Auto-create khata (udhaar ledger) entry ────────────────────────────────
    # For udhaar orders that have a customer, create a khata row so the Finance
    # Udhaar tab reflects the credit immediately — no manual entry needed.
    # Split orders: credit = udhaar_amount (what went on credit, not the cash part).
    # Full udhaar:  credit = final_total.
    if order.payment_method.lower() == 'udhaar' and db_order.customer_id is not None:
        credit_amount = float(order.udhaar_amount) if order.udhaar_amount else final_total
        db.execute(text("""
            INSERT INTO kirana_oltp.khata
                (customer_id, store_id, order_id, amount, amount_paid,
                 issue_date, due_date, status)
            VALUES
                (:cid, :sid, :oid, :amt, 0,
                 CURRENT_DATE, CURRENT_DATE + INTERVAL '30 days', 'pending')
        """), {
            "cid": db_order.customer_id,
            "sid": store_id,
            "oid": db_order.order_id,
            "amt": credit_amount,
        })

    db.commit()
    return get_order(db, db_order.order_id)


def get_orders(
    db: Session, 
    store_id: int, 
    skip: int = 0, 
    limit: int = 100,
    status: Optional[str] = None,
    payment_method: Optional[str] = None,
    customer_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
) -> list[KiranaOrder]:
    query = (
        db.query(KiranaOrder)
        .options(
            selectinload(KiranaOrder.items).joinedload(KiranaOrderItem.product),
            selectinload(KiranaOrder.payment)
        )
        .filter(KiranaOrder.store_id == store_id)
    )

    if status:
        query = query.filter(KiranaOrder.order_status == status)
    if customer_id:
        query = query.filter(KiranaOrder.customer_id == customer_id)
    if start_date:
        query = query.filter(KiranaOrder.order_date >= start_date)
    if end_date:
        query = query.filter(KiranaOrder.order_date <= end_date)
    if min_amount is not None:
        query = query.filter(KiranaOrder.total_amount >= min_amount)
    if max_amount is not None:
        query = query.filter(KiranaOrder.total_amount <= max_amount)
    
    if payment_method:
        if payment_method.lower() == "cash":
            # Orders without a payment record default to "cash" in the display,
            # so include both: explicit cash payments AND orders with no payment row.
            query = query.outerjoin(KiranaPayment).filter(
                or_(KiranaPayment.payment_method.is_(None), KiranaPayment.payment_method == "cash")
            )
        else:
            query = query.join(KiranaPayment).filter(KiranaPayment.payment_method == payment_method)

    orders = query.order_by(KiranaOrder.order_date.desc()).offset(skip).limit(limit).all()

    # Post-process to map product names and selling_price for frontend
    for o in orders:
        o.payment_method = o.payment.payment_method if o.payment else "cash"
        for item in o.items:
            if item.product:
                item.product_name = item.product.name
            item.selling_price = float(item.unit_price) if item.unit_price else 0.0
    return orders


def get_order(db: Session, order_id: int) -> KiranaOrder | None:
    order = (
        db.query(KiranaOrder)
        .options(
            selectinload(KiranaOrder.items).joinedload(KiranaOrderItem.product),
            selectinload(KiranaOrder.payment)
        )
        .filter(KiranaOrder.order_id == order_id)
        .first()
    )
    if order:
        order.payment_method = order.payment.payment_method if order.payment else "cash"
        for item in order.items:
            if item.product:
                item.product_name = item.product.name
            item.selling_price = float(item.unit_price) if item.unit_price else 0.0
    return order


# ── Payments ──────────────────────────────────────────────────────────────────

def create_payment(db: Session, payment: PaymentCreate) -> KiranaPayment:
    obj = KiranaPayment(
        order_id=payment.order_id,
        amount=payment.amount,
        payment_method=payment.payment_method,
        status="paid",
        created_at=datetime.utcnow(),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ── Reports ───────────────────────────────────────────────────────────────────

def get_daily_sales(db: Session, date: datetime, store_id: int | None = None) -> dict:
    start = date.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    end   = date.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_utc = start - timedelta(hours=5, minutes=30)
    end_utc   = end   - timedelta(hours=5, minutes=30)

    q = db.query(
        func.coalesce(func.sum(KiranaOrder.total_amount), 0).label("total_sales"),
        func.count(KiranaOrder.order_id).label("total_orders"),
    ).filter(
        KiranaOrder.order_date   >= start_utc,
        KiranaOrder.order_date   <= end_utc,
        KiranaOrder.order_status == "completed",
    )
    if store_id:
        q = q.filter(KiranaOrder.store_id == store_id)

    row = q.first()
    total_sales  = float(row.total_sales  or 0)
    total_orders = int(row.total_orders   or 0)
    return {
        "date":            date,
        "store_id":        store_id,
        "total_sales":     total_sales,
        "total_orders":    total_orders,
        "avg_order_value": round(total_sales / max(total_orders, 1), 2),
    }
