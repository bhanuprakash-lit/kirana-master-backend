"""
Intelligence Engine — scheduled notification dispatcher.

Uses APScheduler (AsyncIOScheduler) to run all notification triggers on a
per-store basis. All times are in IST (Asia/Kolkata).

Lifecycle:
    engine = IntelligenceEngine(sqlalchemy_engine)
    engine.start()   # called from FastAPI lifespan startup
    engine.stop()    # called from FastAPI lifespan shutdown
"""
from __future__ import annotations

import json
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from kirana.fcm_sender import send_to_token, UNREGISTERED
from kirana.intelligence.repository import IntelligenceRepository
from kirana.intelligence import triggers as T

logger = logging.getLogger("kirana.intelligence.engine")

_IST = "Asia/Kolkata"


class IntelligenceEngine:
    def __init__(self, engine, kirana_svc=None):
        self._db = engine
        self._kirana_svc = kirana_svc
        self._scheduler = AsyncIOScheduler(timezone=_IST)
        self._setup_jobs()

    # ── Public lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Intelligence engine started")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Intelligence engine stopped")

    # ── Job registration ──────────────────────────────────────────────────────

    def _setup_jobs(self) -> None:
        s = self._scheduler

        # Daily greetings & summaries
        s.add_job(self._run_morning_greeting,  CronTrigger(hour=8,  minute=0,  timezone=_IST), id="morning_greeting",  replace_existing=True)
        s.add_job(self._run_evening_summary,   CronTrigger(hour=21, minute=0,  timezone=_IST), id="evening_summary",   replace_existing=True)

        # Daily operational alerts (staggered to avoid bursts)
        s.add_job(self._run_distributor_due,   CronTrigger(hour=9,  minute=0,  timezone=_IST), id="distributor_due",   replace_existing=True)
        s.add_job(self._run_expiry_alert,      CronTrigger(hour=9,  minute=15, timezone=_IST), id="expiry_alert",      replace_existing=True)
        s.add_job(self._run_low_stock_alert,   CronTrigger(hour=9,  minute=30, timezone=_IST), id="low_stock_alert",   replace_existing=True)
        s.add_job(self._run_overdue_udhaar,    CronTrigger(hour=10, minute=0,  timezone=_IST), id="overdue_udhaar",    replace_existing=True)

        # Weekly jobs
        s.add_job(self._run_weekly_report,       CronTrigger(day_of_week="mon", hour=9,  minute=0,  timezone=_IST), id="weekly_report",       replace_existing=True)
        s.add_job(self._run_inactive_customer,   CronTrigger(day_of_week="wed", hour=10, minute=0,  timezone=_IST), id="inactive_customer",   replace_existing=True)
        s.add_job(self._run_feature_discovery,   CronTrigger(day_of_week="fri", hour=11, minute=0,  timezone=_IST), id="feature_discovery",   replace_existing=True)

        # Abandoned cart — checked every 5 minutes
        s.add_job(self._run_abandoned_cart, IntervalTrigger(minutes=5), id="abandoned_cart", replace_existing=True)

        # Nightly inventory snapshot (2am IST) — keeps ML predictions fresh
        s.add_job(self._run_snapshot_refresh, CronTrigger(hour=2, minute=0, timezone=_IST), id="snapshot_refresh", replace_existing=True)

        # ML prediction refresh every 6 hours — reloads CSVs after any retraining
        s.add_job(self._run_ml_refresh, IntervalTrigger(hours=6), id="ml_refresh", replace_existing=True)

        logger.info("Intelligence engine: %d jobs registered", len(s.get_jobs()))

    # ── Core dispatch helpers ─────────────────────────────────────────────────

    def _repo(self) -> IntelligenceRepository:
        return IntelligenceRepository(self._db)

    async def _dispatch(
        self,
        trigger_name: str,
        trigger_fn,
        dedupe: str = "daily",     # "daily" | "weekly" | "none"
        *,
        extra_kwargs: dict | None = None,
    ) -> None:
        repo = self._repo()
        stores = repo.get_active_stores()
        sent = failed = skipped = 0

        for store in stores:
            store_id = store["store_id"]
            user_id  = store["user_id"]
            token    = store["fcm_token"]

            try:
                # Deduplication
                if dedupe == "daily" and repo.was_sent_today(store_id, trigger_name):
                    skipped += 1
                    continue
                if dedupe == "weekly" and repo.was_sent_this_week(store_id, trigger_name):
                    skipped += 1
                    continue

                kwargs = {"store_id": store_id, "repo": repo}
                if extra_kwargs:
                    kwargs.update(extra_kwargs)

                result = trigger_fn(**kwargs)
                if result is None:
                    skipped += 1
                    continue

                title   = result["title"]
                body    = result["body"]
                payload = result.get("payload", {})
                payload["log_id_placeholder"] = "pending"  # filled after log insert

                log_id = repo.log_notification(
                    store_id=store_id,
                    user_id=user_id,
                    trigger_type=trigger_name,
                    title=title,
                    body=body,
                    payload=payload,
                    status="sent",
                )
                payload["log_id"] = str(log_id)

                result = send_to_token(token, title, body, payload)
                if result == UNREGISTERED:
                    # Token is dead — purge it so it's never tried again
                    repo.purge_fcm_token(token)
                    failed += 1
                elif not result:
                    repo.log_notification(
                        store_id=store_id, user_id=user_id,
                        trigger_type=trigger_name, title=title, body=body,
                        payload=payload, status="failed",
                    )
                    failed += 1
                else:
                    sent += 1

            except Exception as exc:
                logger.exception("Intelligence dispatch error store_id=%s trigger=%s: %s", store_id, trigger_name, exc)
                failed += 1

        logger.info("Trigger %-22s sent=%d failed=%d skipped=%d", trigger_name, sent, failed, skipped)

    # ── Individual job handlers ───────────────────────────────────────────────

    async def _run_morning_greeting(self) -> None:
        await self._dispatch("morning_greeting", T.morning_greeting, dedupe="daily")

    async def _run_evening_summary(self) -> None:
        await self._dispatch("evening_summary", T.evening_summary, dedupe="daily")

    async def _run_weekly_report(self) -> None:
        await self._dispatch("weekly_report", T.weekly_report, dedupe="weekly")

    async def _run_overdue_udhaar(self) -> None:
        await self._dispatch("overdue_udhaar", T.overdue_udhaar, dedupe="daily")

    async def _run_distributor_due(self) -> None:
        await self._dispatch("distributor_due", T.distributor_due, dedupe="daily")

    async def _run_low_stock_alert(self) -> None:
        await self._dispatch("low_stock_alert", T.low_stock_alert, dedupe="daily")

    async def _run_expiry_alert(self) -> None:
        await self._dispatch("expiry_alert", T.expiry_alert, dedupe="daily")

    async def _run_inactive_customer(self) -> None:
        await self._dispatch("inactive_customer", T.inactive_customer, dedupe="weekly")

    async def _run_feature_discovery(self) -> None:
        await self._dispatch("feature_discovery", T.feature_discovery, dedupe="weekly")

    async def _run_abandoned_cart(self) -> None:
        """Special case: uses cart_session table, not the generic store loop."""
        repo = self._repo()
        sessions = repo.get_abandoned_sessions(stale_minutes=10)
        sent = skipped = 0

        for session in sessions:
            store_id   = session["store_id"]
            user_id    = session["user_id"]
            token      = session["fcm_token"]
            item_count = session["item_count"]
            cart_data  = session.get("cart_data") or []

            if isinstance(cart_data, str):
                try:
                    cart_data = json.loads(cart_data)
                except Exception:
                    cart_data = []

            try:
                result = T.abandoned_cart(store_id, repo, cart_data, item_count)
                if result is None:
                    skipped += 1
                    continue

                title   = result["title"]
                body    = result["body"]
                payload = result.get("payload", {})

                log_id = repo.log_notification(
                    store_id=store_id, user_id=user_id,
                    trigger_type="abandoned_cart",
                    title=title, body=body, payload=payload,
                    status="sent",
                )
                payload["log_id"] = str(log_id)

                ok = send_to_token(token, title, body, payload)
                if ok:
                    repo.mark_cart_notified(store_id)
                    sent += 1
                else:
                    failed_log_id = repo.log_notification(
                        store_id=store_id, user_id=user_id,
                        trigger_type="abandoned_cart",
                        title=title, body=body, payload=payload,
                        status="failed",
                    )

            except Exception as exc:
                logger.exception("abandoned_cart dispatch error store_id=%s: %s", store_id, exc)

        if sent or skipped:
            logger.info("Trigger abandoned_cart sent=%d skipped=%d", sent, skipped)

    # ── Snapshot refresh ─────────────────────────────────────────────────────

    async def _run_snapshot_refresh(self) -> None:
        """Write daily inventory snapshots from live orders + inventory tables."""
        from datetime import date
        from sqlalchemy import text
        from kirana.repository import KiranaRepository

        today = date.today().isoformat()
        repo  = KiranaRepository(self._db)
        total = 0

        try:
            with self._db.connect() as conn:
                store_ids = conn.execute(text(
                    "SELECT DISTINCT store_id FROM kirana_oltp.inventory"
                )).scalars().all()

            for sid in store_ids:
                with self._db.connect() as conn:
                    rows = conn.execute(text("""
                        SELECT
                            i.product_id                             AS sku_id,
                            i.quantity                               AS stock,
                            COALESCE(s30.units_sold, 0)              AS units_sold,
                            COALESCE(s30.revenue, 0)                 AS revenue,
                            COALESCE(s30.profit, 0)                  AS profit,
                            COALESCE(pr.price, 0)                    AS price,
                            FALSE                                    AS promo_flag
                        FROM kirana_oltp.inventory i
                        LEFT JOIN LATERAL (
                            SELECT
                                SUM(oi.quantity)                                     AS units_sold,
                                SUM(oi.quantity * COALESCE(pr2.price, oi.unit_price, 0)) AS revenue,
                                SUM(oi.quantity * COALESCE(pr2.price, oi.unit_price, 0) * 0.25) AS profit
                            FROM kirana_oltp.orders o
                            JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
                            LEFT JOIN kirana_oltp.pricing pr2
                                ON pr2.product_id = i.product_id AND pr2.store_id = i.store_id
                            WHERE oi.product_id = i.product_id
                              AND o.store_id = i.store_id
                              AND o.order_date >= NOW() - INTERVAL '30 days'
                        ) s30 ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT price FROM kirana_oltp.pricing pr
                            WHERE pr.product_id = i.product_id AND pr.store_id = i.store_id
                            ORDER BY pr.valid_from DESC LIMIT 1
                        ) pr ON TRUE
                        WHERE i.store_id = :sid
                    """), {"sid": sid}).mappings().all()

                items = [dict(r) for r in rows]
                if items:
                    n = repo.upsert_inventory_snapshot(int(sid), today, items)
                    total += n

            logger.info("Snapshot refresh: wrote %d rows across %d stores", total, len(store_ids))
        except Exception:
            logger.exception("Snapshot refresh failed")

    # ── ML prediction refresh ─────────────────────────────────────────────────

    async def _run_ml_refresh(self) -> None:
        """Reload ML prediction CSVs from disk (picks up any newly generated files)."""
        if self._kirana_svc is None:
            return
        try:
            self._kirana_svc.ml.refresh()
            rows = self._kirana_svc.ml.get_frame().shape[0]
            logger.info("ML predictions refreshed: %d rows loaded", rows)
        except Exception:
            logger.exception("ML refresh failed")

    # ── Manual trigger (for testing/admin) ───────────────────────────────────

    async def fire(self, trigger_name: str, store_id: int | None = None) -> dict:
        """
        Manually fire a trigger immediately, bypassing deduplication.
        Used by the admin API for testing.
        """
        handlers = {
            "morning_greeting":  self._run_morning_greeting,
            "evening_summary":   self._run_evening_summary,
            "weekly_report":     self._run_weekly_report,
            "overdue_udhaar":    self._run_overdue_udhaar,
            "distributor_due":   self._run_distributor_due,
            "low_stock_alert":   self._run_low_stock_alert,
            "expiry_alert":      self._run_expiry_alert,
            "inactive_customer": self._run_inactive_customer,
            "feature_discovery": self._run_feature_discovery,
            "abandoned_cart":    self._run_abandoned_cart,
            "snapshot_refresh":  self._run_snapshot_refresh,
            "ml_refresh":        self._run_ml_refresh,
        }
        handler = handlers.get(trigger_name)
        if not handler:
            return {"error": f"Unknown trigger: {trigger_name}"}
        await handler()
        return {"fired": trigger_name}
