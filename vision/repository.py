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
        "image_index": int(getattr(d, "image_index", 0) or 0),
        "detector_source": getattr(d, "source", None) or "gemini",
    } for d in detections]
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO kirana_oltp.vision_item
                (session_id, sku_id, product_id, display_name, gemini_name,
                 visible_text, count, match_score, is_unknown, bbox_json, image_index,
                 detector_source)
            VALUES
                (:sid, :sku_id, :product_id, :display_name, :gemini_name,
                 :visible_text, :count, :match_score, :is_unknown, :bbox_json, :image_index,
                 :detector_source)
        """), rows)


def finalize_session(engine, session_id: int, total_skus: int, total_units: int,
                    unknown_count: int, status: str = "done") -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE kirana_oltp.vision_session
            SET status=:status, total_skus=:tskus, total_units=:tunits, unknown_count=:unk,
                finished_at=NOW()
            WHERE session_id=:sid
        """), {"status": status, "tskus": total_skus, "tunits": total_units,
               "unk": unknown_count, "sid": session_id})


def fail_session(engine, session_id: int, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE kirana_oltp.vision_session "
            "SET status='failed', error=:err, finished_at=NOW() WHERE session_id=:sid"
        ), {"err": error[:1000], "sid": session_id})


def get_session(engine, store_id: int, session_id: int) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT session_id, store_id, session_type, session_date, image_url, status,
                   total_skus, total_units, unknown_count, error, created_at, finished_at
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
                   i.image_index, i.corrected_product_id, i.corrected_at, i.detector_source
            FROM kirana_oltp.vision_item i
            JOIN kirana_oltp.vision_session s ON s.session_id = i.session_id
            WHERE i.session_id=:sid AND s.store_id=:store_id
            ORDER BY i.item_id
        """), {"sid": session_id, "store_id": store_id}).mappings().all()
    return [dict(r) for r in rows]


def get_item_source(engine, store_id: int, item_id: int) -> Optional[dict]:
    """For cropping the review thumbnail: an item's bbox + which session photo it
    came from + that session's image_url array. Store-scoped (auth-safe)."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT i.bbox_json, i.image_index, s.image_url
            FROM kirana_oltp.vision_item i
            JOIN kirana_oltp.vision_session s ON s.session_id = i.session_id
            WHERE i.item_id=:iid AND s.store_id=:store_id
        """), {"iid": item_id, "store_id": store_id}).mappings().first()
    return dict(row) if row else None


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


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_analytics(engine, store_id: Optional[int] = None, days: int = 30) -> dict:
    """Vision analytics over the last `days` days (shelf + onboarding sessions).
    Scoped to one store when `store_id` is given, else fleet-wide across all stores
    (the admin view). Everything is derived from vision_session / vision_item:

    - sessions: volume by type/status + avg processing seconds (finished_at - created_at)
    - detections: unknown rate, owner-correction rate, avg match score of auto-matches
    - detectors: item/unit split by detector_source (own YOLO vs Gemini fallback)
    - daily: per-day series for trend charts
    """
    # Optional store scoping: the session query hits vision_session unaliased, the
    # item queries alias it `s`. Empty clause ⇒ fleet-wide (all stores).
    store_pred = "AND store_id = :store_id" if store_id is not None else ""
    store_pred_s = "AND s.store_id = :store_id" if store_id is not None else ""
    with engine.connect() as conn:
        params = {"store_id": store_id, "days": days}

        sess = conn.execute(text(f"""
            SELECT COUNT(*)                                            AS total,
                   COUNT(*) FILTER (WHERE status = 'done')             AS done,
                   COUNT(*) FILTER (WHERE status = 'failed')           AS failed,
                   COUNT(*) FILTER (WHERE status = 'pending')          AS pending,
                   COUNT(*) FILTER (WHERE session_type = 'morning')    AS morning,
                   COUNT(*) FILTER (WHERE session_type = 'evening')    AS evening,
                   COUNT(*) FILTER (WHERE session_type = 'onboarding') AS onboarding,
                   COUNT(*) FILTER (WHERE committed_at IS NOT NULL)    AS committed,
                   AVG(EXTRACT(EPOCH FROM finished_at - created_at))
                       FILTER (WHERE status = 'done' AND finished_at IS NOT NULL)
                                                                       AS avg_processing_seconds
            FROM kirana_oltp.vision_session
            WHERE session_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {store_pred}
        """), params).mappings().first()

        det = conn.execute(text(f"""
            SELECT COUNT(*)                                               AS items,
                   COALESCE(SUM(i.count), 0)                              AS units,
                   COUNT(*) FILTER (WHERE i.is_unknown)                   AS unknown_items,
                   COUNT(*) FILTER (WHERE i.corrected_product_id IS NOT NULL) AS corrected_items,
                   AVG(i.match_score) FILTER (WHERE i.product_id IS NOT NULL) AS avg_match_score
            FROM kirana_oltp.vision_item i
            JOIN kirana_oltp.vision_session s ON s.session_id = i.session_id
            WHERE s.session_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {store_pred_s}
        """), params).mappings().first()

        by_detector = conn.execute(text(f"""
            SELECT COALESCE(i.detector_source, 'gemini') AS detector_source,
                   COUNT(*)                              AS items,
                   COALESCE(SUM(i.count), 0)             AS units,
                   COUNT(*) FILTER (WHERE NOT i.is_unknown) AS matched_items
            FROM kirana_oltp.vision_item i
            JOIN kirana_oltp.vision_session s ON s.session_id = i.session_id
            WHERE s.session_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {store_pred_s}
            GROUP BY COALESCE(i.detector_source, 'gemini')
            ORDER BY items DESC
        """), params).mappings().all()

        daily = conn.execute(text(f"""
            SELECT s.session_date                                        AS day,
                   COUNT(DISTINCT s.session_id)                          AS sessions,
                   COALESCE(SUM(i.count), 0)                             AS units,
                   COUNT(i.item_id)                                      AS items,
                   COUNT(i.item_id) FILTER (WHERE i.is_unknown)          AS unknown_items,
                   COUNT(i.item_id) FILTER (WHERE i.corrected_product_id IS NOT NULL)
                                                                         AS corrected_items
            FROM kirana_oltp.vision_session s
            LEFT JOIN kirana_oltp.vision_item i ON i.session_id = s.session_id
            WHERE s.session_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {store_pred_s}
            GROUP BY s.session_date
            ORDER BY s.session_date
        """), params).mappings().all()

        # Most-seen unknown raw names = the next products to label / add to the catalog.
        top_unknowns = conn.execute(text(f"""
            SELECT i.gemini_name AS raw_name, COUNT(*) AS times_seen,
                   COALESCE(SUM(i.count), 0) AS units
            FROM kirana_oltp.vision_item i
            JOIN kirana_oltp.vision_session s ON s.session_id = i.session_id
            WHERE s.session_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              AND i.is_unknown AND i.corrected_product_id IS NULL
              {store_pred_s}
            GROUP BY i.gemini_name
            ORDER BY COUNT(*) DESC, SUM(i.count) DESC
            LIMIT 10
        """), params).mappings().all()

    items = int(det["items"] or 0)
    avg_secs = sess["avg_processing_seconds"]
    return {
        "days": days,
        "sessions": {
            "total": int(sess["total"] or 0),
            "done": int(sess["done"] or 0),
            "failed": int(sess["failed"] or 0),
            "pending": int(sess["pending"] or 0),
            "morning": int(sess["morning"] or 0),
            "evening": int(sess["evening"] or 0),
            "onboarding": int(sess["onboarding"] or 0),
            "committed": int(sess["committed"] or 0),
            "avg_processing_seconds": round(float(avg_secs), 2) if avg_secs is not None else None,
        },
        "detections": {
            "items": items,
            "units": int(det["units"] or 0),
            "unknown_items": int(det["unknown_items"] or 0),
            "corrected_items": int(det["corrected_items"] or 0),
            "unknown_rate": round(int(det["unknown_items"] or 0) / items, 4) if items else 0.0,
            "correction_rate": round(int(det["corrected_items"] or 0) / items, 4) if items else 0.0,
            "avg_match_score": (round(float(det["avg_match_score"]), 4)
                                if det["avg_match_score"] is not None else None),
        },
        "detectors": [{
            "detector_source": r["detector_source"],
            "items": int(r["items"]),
            "units": int(r["units"]),
            "matched_items": int(r["matched_items"]),
        } for r in by_detector],
        "daily": [{
            "date": str(r["day"]),
            "sessions": int(r["sessions"]),
            "items": int(r["items"]),
            "units": int(r["units"]),
            "unknown_items": int(r["unknown_items"]),
            "corrected_items": int(r["corrected_items"]),
        } for r in daily],
        "top_unknowns": [{
            "raw_name": r["raw_name"],
            "times_seen": int(r["times_seen"]),
            "units": int(r["units"]),
        } for r in top_unknowns],
    }


def get_store_breakdown(engine, days: int = 30) -> list[dict]:
    """Per-store vision usage over the last `days` days, for the admin fleet view.
    One row per store that ran at least one session in the window, so the admin can
    see which stores actually use vision and how accurate it is for each. Ordered by
    session volume (most active first)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.store_id,
                   st.name                                              AS store_name,
                   COUNT(DISTINCT s.session_id)                         AS sessions,
                   MAX(s.session_date)                                  AS last_scan,
                   COUNT(i.item_id)                                     AS items,
                   COALESCE(SUM(i.count), 0)                            AS units,
                   COUNT(i.item_id) FILTER (WHERE i.is_unknown)         AS unknown_items,
                   COUNT(i.item_id) FILTER (WHERE i.corrected_product_id IS NOT NULL)
                                                                        AS corrected_items,
                   COUNT(i.item_id) FILTER (WHERE i.detector_source = 'yolo')
                                                                        AS yolo_items
            FROM kirana_oltp.vision_session s
            JOIN kirana_oltp.store st ON st.store_id = s.store_id
            LEFT JOIN kirana_oltp.vision_item i ON i.session_id = s.session_id
            WHERE s.session_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
            GROUP BY s.store_id, st.name
            ORDER BY COUNT(DISTINCT s.session_id) DESC, s.store_id
        """), {"days": days}).mappings().all()

    out = []
    for r in rows:
        items = int(r["items"] or 0)
        out.append({
            "store_id": int(r["store_id"]),
            "store_name": r["store_name"],
            "sessions": int(r["sessions"] or 0),
            "last_scan": str(r["last_scan"]) if r["last_scan"] else None,
            "items": items,
            "units": int(r["units"] or 0),
            "unknown_rate": round(int(r["unknown_items"] or 0) / items, 4) if items else 0.0,
            "correction_rate": round(int(r["corrected_items"] or 0) / items, 4) if items else 0.0,
            "yolo_share": round(int(r["yolo_items"] or 0) / items, 4) if items else 0.0,
        })
    return out
