"""Commit a reviewed onboarding session's quantities into store inventory.

The detection/persistence reuses the shelf pipeline (vision_session with
session_type='onboarding' + vision_item). This module owns only the final step:
turning the owner-confirmed quantities into real kirana_oltp.inventory rows, then
stamping the session committed so it can't be double-applied.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text


def count_today_onboarding_sessions(engine, store_id: int) -> int:
    """How many onboarding scans this store has started today (IST-agnostic, uses
    the DB's CURRENT_DATE / session_date). Used to rate-limit the ungated CTA."""
    with engine.connect() as conn:
        n = conn.execute(text("""
            SELECT COUNT(*) FROM kirana_oltp.vision_session
            WHERE store_id = :store_id
              AND session_type = 'onboarding'
              AND session_date = CURRENT_DATE
        """), {"store_id": store_id}).scalar()
    return int(n or 0)


def get_onboarding_session(engine, store_id: int, session_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT session_id, store_id, session_type, status, committed_at
            FROM kirana_oltp.vision_session
            WHERE session_id = :sid AND store_id = :store_id
        """), {"sid": session_id, "store_id": store_id}).mappings().first()
    return dict(row) if row else None


def commit_to_inventory(engine, store_id: int, session_id: int, items: list[dict]) -> dict:
    """Upsert inventory for each reviewed item, then mark the session committed.

    ``items``: [{product_id: int, quantity: int}] — product_id already resolved by
    the app (matched, owner-corrected, or owner-picked for an unrecognised item).
    Quantity is SET (not incremented): the owner's count off the shelf photo is the
    authoritative opening stock, so a re-commit is idempotent. Rows with no
    product_id or quantity <= 0 are skipped.
    """
    # Dedupe by product_id keeping the last quantity (guards a double-listed item).
    by_product: dict[int, int] = {}
    for it in items:
        pid = it.get("product_id")
        qty = int(it.get("quantity", 0))
        if pid is None or qty <= 0:
            continue
        by_product[int(pid)] = qty

    added = 0
    total_qty = 0
    with engine.begin() as conn:
        for pid, qty in by_product.items():
            conn.execute(text("""
                INSERT INTO kirana_oltp.inventory (store_id, product_id, variant_id, quantity)
                VALUES (:sid, :pid, NULL, :qty)
                ON CONFLICT (store_id, product_id, COALESCE(variant_id, 0))
                DO UPDATE SET quantity = EXCLUDED.quantity
            """), {"sid": store_id, "pid": pid, "qty": qty})
            added += 1
            total_qty += qty

        conn.execute(text(
            "UPDATE kirana_oltp.vision_session SET committed_at = NOW() "
            "WHERE session_id = :sid AND store_id = :store_id"
        ), {"sid": session_id, "store_id": store_id})

    return {"products_added": added, "total_quantity": total_qty, "skipped": len(items) - added}
