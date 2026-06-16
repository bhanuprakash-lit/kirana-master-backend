"""Postgres persistence for vision shelf sessions (kirana_oltp.vision_session /
vision_item). Mirrors the old SQLite inventory_db.py but store-scoped by INT
store_id and matched to real product_id.

Sales delta = max(0, morning_count - evening_count) per effective product, where
the effective product is the owner-corrected one if present, else the matched one.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import text


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(engine, store_id: int, session_type: str, image_url: Optional[str],
                   session_date: Optional[str] = None) -> int:
    sd = session_date or date.today().isoformat()
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO kirana_oltp.vision_session
                (store_id, session_type, session_date, image_url, status)
            VALUES (:store_id, :stype, :sdate, :url, 'pending')
            RETURNING session_id
        """), {"store_id": store_id, "stype": session_type, "sdate": sd, "url": image_url}).first()
    return int(row[0])


def save_items(engine, session_id: int, detections) -> None:
    if not detections:
        return
    rows = [{
        "sid": session_id,
        "sku_id": d.sku_id,
        "product_id": d.product_id,
        "display_name": d.display_name,
        "gemini_name": d.raw_name,
        "visible_text": d.visible_text,
        "count": d.count,
        "match_score": float(d.match_score),
        "is_unknown": bool(d.is_unknown),
        "bbox_json": d.bbox_json(),
    } for d in detections]
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO kirana_oltp.vision_item
                (session_id, sku_id, product_id, display_name, gemini_name,
                 visible_text, count, match_score, is_unknown, bbox_json)
            VALUES
                (:sid, :sku_id, :product_id, :display_name, :gemini_name,
                 :visible_text, :count, :match_score, :is_unknown, :bbox_json)
        """), rows)


def finalize_session(engine, session_id: int, total_skus: int, total_units: int,
                    unknown_count: int, status: str = "done") -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE kirana_oltp.vision_session
            SET status=:status, total_skus=:tskus, total_units=:tunits, unknown_count=:unk
            WHERE session_id=:sid
        """), {"status": status, "tskus": total_skus, "tunits": total_units,
               "unk": unknown_count, "sid": session_id})


def fail_session(engine, session_id: int, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE kirana_oltp.vision_session SET status='failed', error=:err WHERE session_id=:sid"
        ), {"err": error[:1000], "sid": session_id})


def get_session(engine, store_id: int, session_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT session_id, store_id, session_type, session_date, image_url, status,
                   total_skus, total_units, unknown_count, error, created_at
            FROM kirana_oltp.vision_session
            WHERE session_id=:sid AND store_id=:store_id
        """), {"sid": session_id, "store_id": store_id}).mappings().first()
    return dict(row) if row else None


def get_sessions(engine, store_id: int, session_date: Optional[str] = None) -> list[dict]:
    sd = session_date or date.today().isoformat()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT session_id, session_type, session_date, status,
                   total_skus, total_units, unknown_count, created_at
            FROM kirana_oltp.vision_session
            WHERE store_id=:store_id AND session_date=:sdate
            ORDER BY created_at
        """), {"store_id": store_id, "sdate": sd}).mappings().all()
    return [dict(r) for r in rows]


def get_items(engine, store_id: int, session_id: int) -> list[dict]:
    """Items for a session, scoped to the owning store (auth-safe)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT i.item_id, i.sku_id, i.product_id, i.display_name, i.gemini_name,
                   i.visible_text, i.count, i.match_score, i.is_unknown, i.bbox_json,
                   i.corrected_product_id, i.corrected_at
            FROM kirana_oltp.vision_item i
            JOIN kirana_oltp.vision_session s ON s.session_id = i.session_id
            WHERE i.session_id=:sid AND s.store_id=:store_id
            ORDER BY i.item_id
        """), {"sid": session_id, "store_id": store_id}).mappings().all()
    return [dict(r) for r in rows]


# ── Sales delta ───────────────────────────────────────────────────────────────

def compute_sales_delta(engine, store_id: int, session_date: Optional[str] = None) -> list[dict]:
    """Latest morning session vs latest evening session for the date. Groups by the
    effective product (corrected_product_id or product_id); ignores unmatched items."""
    sd = session_date or date.today().isoformat()
    with engine.connect() as conn:
        def counts(stype: str) -> dict:
            srow = conn.execute(text("""
                SELECT session_id FROM kirana_oltp.vision_session
                WHERE store_id=:store_id AND session_date=:sdate AND session_type=:stype
                  AND status='done'
                ORDER BY created_at DESC LIMIT 1
            """), {"store_id": store_id, "sdate": sd, "stype": stype}).first()
            if not srow:
                return {}
            rows = conn.execute(text("""
                SELECT COALESCE(corrected_product_id, product_id) AS pid,
                       MAX(display_name) AS name,
                       SUM(count) AS total
                FROM kirana_oltp.vision_item
                WHERE session_id=:sid
                  AND COALESCE(corrected_product_id, product_id) IS NOT NULL
                GROUP BY COALESCE(corrected_product_id, product_id)
            """), {"sid": srow[0]}).mappings().all()
            return {int(r["pid"]): {"count": int(r["total"]), "name": r["name"]} for r in rows}

        morning, evening = counts("morning"), counts("evening")

    out = []
    for pid in set(morning) | set(evening):
        m = morning.get(pid, {"count": 0, "name": None})
        e = evening.get(pid, {"count": 0, "name": None})
        out.append({
            "product_id": pid,
            "display_name": m["name"] or e["name"] or f"#{pid}",
            "morning_count": m["count"],
            "evening_count": e["count"],
            "sold": max(0, m["count"] - e["count"]),
        })
    out.sort(key=lambda d: d["sold"], reverse=True)
    return out


# ── Owner correction ──────────────────────────────────────────────────────────

def correct_item(engine, store_id: int, item_id: int, corrected_product_id: Optional[int]) -> bool:
    """Set the owner-corrected product on an item (store-scoped). Returns True if a
    row was updated. corrected_product_id=None clears the correction."""
    with engine.begin() as conn:
        res = conn.execute(text("""
            UPDATE kirana_oltp.vision_item i
            SET corrected_product_id=:cpid,
                corrected_at = CASE WHEN :cpid IS NULL THEN NULL ELSE NOW() END,
                is_unknown   = CASE WHEN :cpid IS NULL THEN is_unknown ELSE FALSE END
            FROM kirana_oltp.vision_session s
            WHERE i.item_id=:iid AND i.session_id=s.session_id AND s.store_id=:store_id
        """), {"cpid": corrected_product_id, "iid": item_id, "store_id": store_id})
    return res.rowcount > 0
