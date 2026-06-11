"""
Intelligence layer — database operations.

All methods take a SQLAlchemy engine and return plain Python dicts/lists.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger("kirana.intelligence.repo")


class IntelligenceRepository:
    def __init__(self, engine):
        self._engine = engine

    @contextmanager
    def _conn(self):
        with self._engine.begin() as conn:
            yield conn

    # ── Active stores ─────────────────────────────────────────────────────────

    def get_active_stores(self) -> list[dict]:
        """
        Returns all stores that have an owner user with an FCM token.
        Includes stores on trial, basic, or pro — excludes none/expired.
        """
        sql = """
        SELECT
            s.store_id,
            u.user_id,
            u.fcm_token,
            s.name AS store_name
        FROM kirana_oltp.store s
        JOIN kirana_oltp.users u
            ON u.store_id = s.store_id AND u.role = 'store_owner'
        WHERE u.fcm_token IS NOT NULL AND u.fcm_token != ''
        ORDER BY s.store_id
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]

    def purge_fcm_token(self, token: str) -> None:
        """Delete a stale/unregistered FCM token from all tables so it's never retried."""
        with self._conn() as conn:
            conn.execute(text(
                "DELETE FROM kirana_oltp.user_fcm_tokens WHERE fcm_token = :tok"
            ), {"tok": token})
            conn.execute(text(
                "UPDATE kirana_oltp.users SET fcm_token = NULL WHERE fcm_token = :tok"
            ), {"tok": token})
            conn.commit()
        logger.info("Purged stale FCM token ...%s", token[-8:] if token else "?")

    # ── Deduplication ────────────────────────────────────────────────────────

    def was_sent_today(self, store_id: int, trigger_type: str) -> bool:
        """True if this trigger was already sent to this store today (IST)."""
        sql = """
        SELECT 1 FROM kirana_oltp.intelligence_log
        WHERE store_id = :sid
          AND trigger_type = :tt
          AND sent_at AT TIME ZONE 'Asia/Kolkata' >= CURRENT_DATE
          AND status != 'failed'
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id, "tt": trigger_type}).fetchone()
        return row is not None

    def was_sent_this_week(self, store_id: int, trigger_type: str) -> bool:
        """True if this trigger was sent in the last 7 days."""
        sql = """
        SELECT 1 FROM kirana_oltp.intelligence_log
        WHERE store_id = :sid
          AND trigger_type = :tt
          AND sent_at > NOW() - INTERVAL '7 days'
          AND status != 'failed'
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id, "tt": trigger_type}).fetchone()
        return row is not None

    # ── Logging ───────────────────────────────────────────────────────────────

    def log_notification(
        self,
        store_id: int,
        user_id: int | None,
        trigger_type: str,
        title: str,
        body: str,
        payload: dict,
        status: str = "sent",
    ) -> int:
        sql = """
        INSERT INTO kirana_oltp.intelligence_log
            (store_id, user_id, trigger_type, title, body, payload, status)
        VALUES (:sid, :uid, :tt, :title, :body, :payload, :status)
        RETURNING id
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {
                "sid": store_id,
                "uid": user_id,
                "tt": trigger_type,
                "title": title,
                "body": body,
                "payload": json.dumps(payload),
                "status": status,
            }).fetchone()
        return row[0] if row else -1

    def mark_opened(self, log_id: int) -> None:
        sql = """
        UPDATE kirana_oltp.intelligence_log
        SET opened_at = NOW(), status = 'opened'
        WHERE id = :id
        """
        with self._conn() as conn:
            conn.execute(text(sql), {"id": log_id})

    def list_logs(self, store_id: int, limit: int = 50) -> list[dict]:
        sql = """
        SELECT id, store_id, user_id, trigger_type, title, body, payload,
               sent_at, opened_at, status
        FROM kirana_oltp.intelligence_log
        WHERE store_id = :sid
        ORDER BY sent_at DESC
        LIMIT :lim
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id, "lim": limit}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d["sent_at"]:
                d["sent_at"] = d["sent_at"].isoformat()
            if d["opened_at"]:
                d["opened_at"] = d["opened_at"].isoformat()
            result.append(d)
        return result

    def list_all_logs(self, limit: int = 200) -> list[dict]:
        sql = """
        SELECT il.id, il.store_id, s.name AS store_name, il.user_id,
               il.trigger_type, il.title, il.body,
               il.sent_at, il.opened_at, il.status
        FROM kirana_oltp.intelligence_log il
        LEFT JOIN kirana_oltp.store s ON s.store_id = il.store_id
        ORDER BY il.sent_at DESC
        LIMIT :lim
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"lim": limit}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d["sent_at"]:
                d["sent_at"] = d["sent_at"].isoformat()
            if d["opened_at"]:
                d["opened_at"] = d["opened_at"].isoformat()
            result.append(d)
        return result

    # ── Cart session ──────────────────────────────────────────────────────────

    def upsert_cart_session(self, store_id: int, item_count: int, cart_data: list) -> None:
        sql = """
        INSERT INTO kirana_oltp.cart_session (store_id, item_count, cart_data, updated_at)
        VALUES (:sid, :cnt, :data, NOW())
        ON CONFLICT (store_id) DO UPDATE
            SET item_count   = EXCLUDED.item_count,
                cart_data    = EXCLUDED.cart_data,
                updated_at   = NOW(),
                -- reset notification flag when cart changes meaningfully
                notified_at  = CASE
                    WHEN kirana_oltp.cart_session.item_count != EXCLUDED.item_count
                    THEN NULL
                    ELSE kirana_oltp.cart_session.notified_at
                END,
                converted_at = NULL
        """
        with self._conn() as conn:
            conn.execute(text(sql), {
                "sid": store_id,
                "cnt": item_count,
                "data": json.dumps(cart_data),
            })

    def mark_cart_converted(self, store_id: int) -> None:
        sql = """
        UPDATE kirana_oltp.cart_session
        SET converted_at = NOW(), item_count = 0, cart_data = '[]'
        WHERE store_id = :sid
        """
        with self._conn() as conn:
            conn.execute(text(sql), {"sid": store_id})

    def mark_cart_notified(self, store_id: int) -> None:
        sql = "UPDATE kirana_oltp.cart_session SET notified_at = NOW() WHERE store_id = :sid"
        with self._conn() as conn:
            conn.execute(text(sql), {"sid": store_id})

    def get_abandoned_sessions(self, stale_minutes: int = 10) -> list[dict]:
        """
        Returns cart sessions where:
        - item_count > 0
        - updated_at is older than stale_minutes
        - not yet notified (or notified > 1 hour ago for re-nudge)
        - not converted
        """
        sql = """
        SELECT cs.store_id, cs.item_count, cs.cart_data, cs.updated_at,
               u.user_id, u.fcm_token, s.name AS store_name,
               COALESCE(up.quiet_hours_start, 22) AS quiet_hours_start,
               COALESCE(up.quiet_hours_end, 7) AS quiet_hours_end
        FROM kirana_oltp.cart_session cs
        JOIN kirana_oltp.store s ON s.store_id = cs.store_id
        JOIN kirana_oltp.users u
            ON u.store_id = cs.store_id AND u.role = 'store_owner'
        LEFT JOIN kirana_oltp.user_prefs up
            ON up.user_id = u.user_id
        WHERE cs.item_count > 0
          AND cs.updated_at < NOW() - (:mins || ' minutes')::interval
          AND (cs.notified_at IS NULL OR cs.notified_at < NOW() - INTERVAL '1 hour')
          AND (cs.converted_at IS NULL OR cs.converted_at < cs.updated_at)
          AND u.fcm_token IS NOT NULL AND u.fcm_token != ''
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"mins": stale_minutes}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("updated_at"):
                d["updated_at"] = d["updated_at"].isoformat()
            result.append(d)
        return result

    # ── Store context (for personalising messages) ────────────────────────────

    def get_store_context(self, store_id: int) -> dict:
        """Fetch store name + yesterday & today stats in one query."""
        sql = """
        SELECT
            s.name AS store_name,
            -- yesterday
            COALESCE(SUM(CASE WHEN DATE(o.order_date AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE - 1
                              THEN o.total_amount END), 0)   AS yesterday_revenue,
            COUNT(CASE WHEN DATE(o.order_date AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE - 1
                       THEN 1 END)                            AS yesterday_orders,
            -- today
            COALESCE(SUM(CASE WHEN DATE(o.order_date AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                              THEN o.total_amount END), 0)   AS today_revenue,
            COUNT(CASE WHEN DATE(o.order_date AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                       THEN 1 END)                            AS today_orders
        FROM kirana_oltp.store s
        LEFT JOIN kirana_oltp.orders o ON o.store_id = s.store_id
        WHERE s.store_id = :sid
        GROUP BY s.name
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        return dict(row) if row else {"store_name": "your store"}

    def get_yesterday_top_product(self, store_id: int) -> str | None:
        sql = """
        SELECT p.name
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.product p ON p.product_id = oi.product_id
        JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
        WHERE o.store_id = :sid
          AND DATE(o.order_date AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE - 1
        GROUP BY p.product_id, p.name
        ORDER BY SUM(oi.quantity) DESC
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).fetchone()
        return row[0] if row else None

    def get_overdue_udhaar(self, store_id: int, days: int = 7) -> dict:
        sql = """
        SELECT
            COUNT(DISTINCT k.customer_id)                           AS overdue_customers,
            COALESCE(SUM(k.amount - COALESCE(k.amount_paid, 0)), 0) AS total_overdue
        FROM kirana_oltp.khata k
        WHERE k.store_id = :sid
          AND (k.amount - COALESCE(k.amount_paid, 0)) > 0
          AND k.issue_date < CURRENT_DATE - (:days || ' days')::interval
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id, "days": days}).mappings().first()
        return dict(row) if row else {"overdue_customers": 0, "total_overdue": 0}

    def get_distributor_dues(self, store_id: int) -> dict:
        sql = """
        SELECT
            COUNT(*)                                    AS pending_suppliers,
            COALESCE(SUM(p.total_amount), 0)           AS total_due
        FROM kirana_oltp.purchases p
        WHERE p.store_id = :sid
          AND p.payment_status IN ('pending', 'partial')
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        return dict(row) if row else {"pending_suppliers": 0, "total_due": 0}

    def get_low_stock_count(self, store_id: int) -> int:
        sql = """
        SELECT COUNT(*) AS cnt
        FROM kirana_oltp.inventory i
        WHERE i.store_id = :sid
          AND i.reorder_level IS NOT NULL
          AND i.quantity <= i.reorder_level
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).fetchone()
        return row[0] if row else 0

    def get_expiring_count(self, store_id: int, days: int = 7) -> int:
        sql = """
        SELECT COUNT(*) AS cnt
        FROM kirana_oltp.inventory_batch ib
        WHERE ib.store_id = :sid
          AND ib.expiry_date BETWEEN CURRENT_DATE AND CURRENT_DATE + :days
          AND ib.quantity > 0
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id, "days": days}).fetchone()
        return row[0] if row else 0

    def get_inactive_customer_count(self, store_id: int, days: int = 45) -> int:
        sql = """
        SELECT COUNT(*) AS cnt
        FROM kirana_oltp.customer c
        WHERE c.store_id = :sid
          AND NOT EXISTS (
              SELECT 1 FROM kirana_oltp.orders o
              WHERE o.customer_id = c.customer_id
                AND o.store_id = :sid
                AND o.order_date > NOW() - (:days || ' days')::interval
          )
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id, "days": days}).fetchone()
        return row[0] if row else 0

    def get_weekly_summary(self, store_id: int) -> dict:
        sql = """
        SELECT
            COALESCE(SUM(o.total_amount), 0)    AS week_revenue,
            COUNT(o.order_id)                   AS week_orders,
            COALESCE(AVG(o.total_amount), 0)    AS avg_order_value,
            COUNT(DISTINCT o.customer_id)       AS unique_customers
        FROM kirana_oltp.orders o
        WHERE o.store_id = :sid
          AND o.order_date >= NOW() - INTERVAL '7 days'
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        return dict(row) if row else {}

    def get_feature_usage(self, store_id: int) -> dict:
        """Check which features the store has/hasn't used — for discovery nudges."""
        with self._conn() as conn:
            has_customers = conn.execute(
                text("SELECT 1 FROM kirana_oltp.customer WHERE store_id = :sid LIMIT 1"),
                {"sid": store_id},
            ).fetchone() is not None

            has_associations = conn.execute(
                text("SELECT 1 FROM kirana_oltp.store_association WHERE store_id = :sid LIMIT 1"),
                {"sid": store_id},
            ).fetchone() is not None

            has_kpi_subs = conn.execute(
                text("SELECT 1 FROM kirana_oltp.user_prefs up JOIN kirana_oltp.users u ON u.user_id = up.user_id WHERE u.store_id = :sid AND up.subscribed_kpis IS NOT NULL AND up.subscribed_kpis != '[]' LIMIT 1"),
                {"sid": store_id},
            ).fetchone() is not None

            has_referral = conn.execute(
                text("SELECT 1 FROM kirana_oltp.referral_campaign WHERE store_id = :sid LIMIT 1"),
                {"sid": store_id},
            ).fetchone() is not None

        return {
            "has_customers": has_customers,
            "has_associations": has_associations,
            "has_kpi_subs": has_kpi_subs,
            "has_referral": has_referral,
        }
