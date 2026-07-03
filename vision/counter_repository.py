"""Postgres persistence for sale-area COUNTER sessions
(kirana_oltp.counter_session / counter_item).

Unlike the shelf flow, detection + line-crossing counting happen ON THE DEVICE
(on-device YOLO). The app only syncs a FINALIZED per-product tally here; the server
resolves each on-device class_name to a real product_id via the shared CatalogMatcher
and persists it, store-scoped.

Idempotent by (store_id, client_uid): re-syncing the same on-device session upserts
the header and fully replaces its items, so a retry after a flaky network never
double-counts.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import text


def upsert_session(
    engine,
    store_id: int,
    client_uid: str,
    session_date: Optional[str],
    device_label: Optional[str],
    started_at: Optional[str],
    ended_at: Optional[str],
    items: list[dict],
) -> dict:
    """Create-or-update a counter session and REPLACE its items in one transaction.

    ``items`` are already matched dicts with keys: product_id, class_name,
    display_name, qty, match_score, is_unknown, avg_confidence.
    Returns the persisted session summary (same shape as get_summary rows).
    """
    sd = session_date or date.today().isoformat()
    total_units = sum(int(i["qty"]) for i in items)
    total_skus = len({i["product_id"] for i in items if i.get("product_id") is not None})
    unknown_count = sum(int(i["qty"]) for i in items if i.get("is_unknown"))

    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO kirana_oltp.counter_session
                (store_id, client_uid, session_date, device_label, source,
                 started_at, ended_at, total_units, total_skus, unknown_count)
            VALUES
                (:store_id, :uid, :sdate, :label, 'on_device',
                 :started, :ended, :units, :skus, :unknown)
            ON CONFLICT (store_id, client_uid) DO UPDATE SET
                session_date = EXCLUDED.session_date,
                device_label = EXCLUDED.device_label,
                started_at   = EXCLUDED.started_at,
                ended_at     = EXCLUDED.ended_at,
                total_units  = EXCLUDED.total_units,
                total_skus   = EXCLUDED.total_skus,
                unknown_count = EXCLUDED.unknown_count
            RETURNING session_id
        """), {
            "store_id": store_id, "uid": client_uid, "sdate": sd,
            "label": device_label, "started": started_at, "ended": ended_at,
            "units": total_units, "skus": total_skus, "unknown": unknown_count,
        }).first()
        session_id = int(row[0])

        # Full replace: a finalized session is authoritative for its own tally.
        conn.execute(text(
            "DELETE FROM kirana_oltp.counter_item WHERE session_id = :sid"
        ), {"sid": session_id})

        if items:
            conn.execute(text("""
                INSERT INTO kirana_oltp.counter_item
                    (session_id, product_id, class_name, display_name,
                     qty, match_score, is_unknown, avg_confidence)
                VALUES
                    (:sid, :product_id, :class_name, :display_name,
                     :qty, :match_score, :is_unknown, :avg_confidence)
            """), [{
                "sid": session_id,
                "product_id": i.get("product_id"),
                "class_name": i["class_name"],
                "display_name": i.get("display_name"),
                "qty": int(i["qty"]),
                "match_score": float(i.get("match_score", 0.0)),
                "is_unknown": bool(i.get("is_unknown", True)),
                "avg_confidence": i.get("avg_confidence"),
            } for i in items])

    return {
        "session_id": session_id,
        "session_date": sd,
        "total_units": total_units,
        "total_skus": total_skus,
        "unknown_count": unknown_count,
    }


def get_summary(engine, store_id: int, session_date: Optional[str] = None) -> dict:
    """Aggregate all of a day's counter sessions into one per-product tally.

    Groups by effective product: a matched product_id if present, else the raw
    class_name (unknowns stay separate so the owner can still see what was counted).
    """
    sd = session_date or date.today().isoformat()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ci.product_id,
                   MAX(ci.display_name)        AS display_name,
                   ci.class_name,
                   SUM(ci.qty)                 AS qty,
                   bool_and(ci.is_unknown)     AS is_unknown
            FROM kirana_oltp.counter_item ci
            JOIN kirana_oltp.counter_session cs ON cs.session_id = ci.session_id
            WHERE cs.store_id = :store_id AND cs.session_date = :sdate
            GROUP BY ci.product_id, ci.class_name
            ORDER BY SUM(ci.qty) DESC
        """), {"store_id": store_id, "sdate": sd}).mappings().all()

    items = []
    for r in rows:
        items.append({
            "product_id": int(r["product_id"]) if r["product_id"] is not None else None,
            "class_name": r["class_name"],
            "display_name": r["display_name"] or _prettify(r["class_name"]),
            "qty": int(r["qty"]),
            "is_unknown": bool(r["is_unknown"]),
        })
    return {
        "store_id": store_id,
        "session_date": sd,
        "items": items,
        "total_units": sum(i["qty"] for i in items),
        "total_skus": len({i["product_id"] for i in items if i["product_id"] is not None}),
    }


def get_sessions(engine, store_id: int, session_date: Optional[str] = None) -> list[dict]:
    sd = session_date or date.today().isoformat()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT session_id, client_uid, session_date, device_label,
                   started_at, ended_at, total_units, total_skus, unknown_count, created_at
            FROM kirana_oltp.counter_session
            WHERE store_id = :store_id AND session_date = :sdate
            ORDER BY created_at DESC
        """), {"store_id": store_id, "sdate": sd}).mappings().all()
    return [dict(r) for r in rows]


def _prettify(class_name: str) -> str:
    """Human label for an unmatched on-device class, e.g. 'red_label_tea_powder'
    -> 'Red Label Tea Powder'."""
    return class_name.replace("_", " ").replace("-", " ").strip().title()
