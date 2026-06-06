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
from sqlalchemy import text

from kirana.fcm_sender import send_to_token, UNREGISTERED
from kirana.intelligence.repository import IntelligenceRepository
from kirana.intelligence import triggers as T

logger = logging.getLogger("kirana.intelligence.engine")

_IST = "Asia/Kolkata"

# Postgres advisory-lock keys: every process that constructs an
# IntelligenceEngine (each uvicorn worker, each Azure Container App replica)
# would otherwise run its own scheduler and fire every job — and every push —
# once per process. _SCHEDULER_LOCK_KEY gates the whole scheduler so only the
# leader runs jobs; the heavy nightly jobs additionally guard themselves as a
# belt-and-braces against a brief lock handover.
# NOTE: advisory locks are scoped per *database*, so DEV/QA/UAT sharing one
# Postgres *server* but separate databases are isolated automatically.
_SCHEDULER_LOCK_KEY = 994200
_SNAPSHOT_LOCK_KEY = 994201
_ML_RETRAIN_LOCK_KEY = 994202


class IntelligenceEngine:
    def __init__(self, engine, kirana_svc=None):
        self._db = engine
        self._kirana_svc = kirana_svc
        self._scheduler = AsyncIOScheduler(timezone=_IST)
        self._lock_conn = None  # held connection for the leader's scheduler lock
        self._is_leader = False

    # ── Public lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._scheduler.running:
            return
        # The scheduler runs in every worker/replica, but only the one that holds
        # the advisory lock registers the real jobs (leader). The rest poll for
        # leadership every 2 min so that if the current leader dies or releases
        # the lock — e.g. the brief overlap during a rolling deploy — a standby
        # takes over instead of leaving nobody running the jobs.
        self._scheduler.start()
        self._try_become_leader()
        if not self._is_leader:
            logger.info("Intelligence engine: scheduler lock held elsewhere — standby, retrying leadership every 2 min")
            self._scheduler.add_job(
                self._try_become_leader, IntervalTrigger(minutes=2),
                id="_leader_election", replace_existing=True,
            )

    def _try_become_leader(self) -> None:
        if self._is_leader:
            return
        conn = self._try_lock(_SCHEDULER_LOCK_KEY)
        if conn is None:
            return  # someone else still holds it; the election job retries
        self._lock_conn = conn
        self._is_leader = True
        self._setup_jobs()  # registers the real jobs + lock keepalive
        try:
            job = self._scheduler.get_job("_leader_election")
            if job:
                job.remove()
        except Exception:
            pass
        logger.info("Intelligence engine started (scheduler leader)")

    def _step_down(self) -> None:
        """Lost the lock — drop the jobs and re-enter the election."""
        self._is_leader = False
        if self._lock_conn is not None:
            self._release_lock(self._lock_conn, _SCHEDULER_LOCK_KEY)
            self._lock_conn = None
        for job in self._scheduler.get_jobs():
            if job.id != "_leader_election":
                try:
                    job.remove()
                except Exception:
                    pass
        self._scheduler.add_job(
            self._try_become_leader, IntervalTrigger(minutes=2),
            id="_leader_election", replace_existing=True,
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Intelligence engine stopped")
        if self._lock_conn is not None:
            self._release_lock(self._lock_conn, _SCHEDULER_LOCK_KEY)
            self._lock_conn = None

    @property
    def handlers(self) -> list[dict]:
        """Registered scheduled triggers, for admin introspection."""
        return [
            {
                "id": job.id,
                "trigger": str(job.trigger),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]

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

        # Delayed onboarding template — checked every 5 minutes
        s.add_job(self._run_delayed_onboarding, IntervalTrigger(minutes=5), id="delayed_onboarding", replace_existing=True)

        # Nightly inventory snapshot (2am IST) — keeps ML predictions fresh
        s.add_job(self._run_snapshot_refresh, CronTrigger(hour=2, minute=0, timezone=_IST), id="snapshot_refresh", replace_existing=True)

        # Nightly ML retrain (3am IST) — runs train_all.py on latest DB data, then reloads CSVs
        # Scheduled 1 hour after snapshot_refresh so fresh inventory is in DB first.
        s.add_job(self._run_ml_retrain, CronTrigger(hour=3, minute=0, timezone=_IST), id="ml_retrain", replace_existing=True)

        # ML prediction refresh every 6 hours — safety net reload of CSVs
        s.add_job(self._run_ml_refresh, IntervalTrigger(hours=6), id="ml_refresh", replace_existing=True)

        # Keep the leader's advisory-lock connection warm. Azure can cut idle TCP
        # sessions, which would silently release the lock; only the leader runs
        # this job (standby instances never start the scheduler).
        s.add_job(self._keepalive_lock, IntervalTrigger(minutes=4), id="_lock_keepalive", replace_existing=True)

        logger.info("Intelligence engine: %d jobs registered", len(s.get_jobs()))

    # ── Core dispatch helpers ─────────────────────────────────────────────────

    def _repo(self) -> IntelligenceRepository:
        return IntelligenceRepository(self._db)

    def _try_lock(self, key: int):
        """Acquire a session-level advisory lock. Returns the held connection
        (release + close it when done) or None if another replica holds it."""
        conn = self._db.connect()
        try:
            if conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
                return conn
        except Exception:
            logger.exception("Advisory lock %s acquisition failed", key)
        conn.close()
        return None

    @staticmethod
    def _release_lock(conn, key: int) -> None:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        except Exception:
            logger.exception("Advisory unlock %s failed", key)
        finally:
            conn.close()

    def _keepalive_lock(self) -> None:
        """Ping the held lock connection so Azure doesn't reap it as idle. If the
        connection has dropped, step down and re-enter the election so another
        replica (or this one) can pick the lock back up."""
        if self._lock_conn is None:
            return
        try:
            self._lock_conn.execute(text("SELECT 1"))
        except Exception:
            logger.warning("Scheduler lock connection lost — stepping down to standby and re-electing")
            self._step_down()

    def _is_in_quiet_hours(self, quiet_hours_start: int, quiet_hours_end: int) -> bool:
        from datetime import datetime
        import zoneinfo
        now_hour = datetime.now(zoneinfo.ZoneInfo(_IST)).hour
        if quiet_hours_start <= quiet_hours_end:
            return quiet_hours_start <= now_hour < quiet_hours_end
        else:
            return now_hour >= quiet_hours_start or now_hour < quiet_hours_end

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

                if self._is_in_quiet_hours(store.get("quiet_hours_start", 22), store.get("quiet_hours_end", 7)):
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

    async def _run_delayed_onboarding(self) -> None:
        """Finds store owners whose trial started 60-65 minutes ago and sends them the onboarding WhatsApp template."""
        from kirana.repository import KiranaRepository
        repo = self._repo()
        sent = 0

        # Query for users who hit the 1-hour mark
        with self._db.connect() as conn:
            rows = conn.execute(text("""
                SELECT u.phone_number, u.user_id, s.store_id,
                       COALESCE(up.quiet_hours_start, 22) AS quiet_hours_start,
                       COALESCE(up.quiet_hours_end, 7) AS quiet_hours_end
                FROM kirana_oltp.users u
                JOIN kirana_oltp.subscription s ON s.store_id = u.store_id
                LEFT JOIN kirana_oltp.user_prefs up ON up.user_id = u.user_id
                WHERE u.role = 'store_owner'
                  AND u.phone_number IS NOT NULL
                  AND u.phone_number != ''
                  AND NOT COALESCE(u.is_deleted, FALSE)
                  AND s.started_at BETWEEN NOW() - INTERVAL '65 minutes' AND NOW() - INTERVAL '60 minutes'
            """)).mappings().all()

        if not rows:
            return

        from whatsapp.templates import onboarding_payload
        import traceback

        for row in rows:
            phone = row["phone_number"]
            store_id = row["store_id"]

            if self._is_in_quiet_hours(row.get("quiet_hours_start", 22), row.get("quiet_hours_end", 7)):
                continue

            # We can use intelligence_logs to ensure we never double-send
            if repo.was_sent_today(store_id, "delayed_onboarding"):
                continue

            try:
                # Need wa_client to send the message
                # It's usually attached to app.state, but we need to fetch it via the engine here.
                # Since we don't have direct access to `app.state`, we'll import get_settings
                # and initialize a temp client if needed, or rely on the kirana_svc context if possible.
                # Wait, engine doesn't easily hold the wa_client. Let's get it via FastAPI if we can,
                # but better yet, let's use the DB directly to check if they have a WhatsApp session.
                
                # A robust way is to just fire an HTTP request to our own endpoint? No, that's messy.
                # We can construct the WhatsAppClient right here since we have settings.
                from config import get_settings
                from whatsapp.client import WhatsAppClient
                s = get_settings()
                if not s.whatsapp_access_token:
                    continue

                wa_client = WhatsAppClient(
                    access_token=s.whatsapp_access_token,
                    phone_number_id=s.whatsapp_phone_number_id,
                    base_url=s.whatsapp_api_base_url,
                )

                if not wa_client.is_configured:
                    continue

                # The onboarding template requires a 'user_number'. In conversation_handler, it defaults to 1.
                payload = onboarding_payload(phone, 1)
                wa_client.send_template(payload)

                repo.log_notification(
                    store_id=store_id, user_id=row["user_id"],
                    trigger_type="delayed_onboarding",
                    title="Onboarding WhatsApp", body="Sent 1 hour after trial start",
                    payload={"phone": phone},
                    status="sent",
                )
                sent += 1
            except Exception as exc:
                logger.error("Failed to send delayed onboarding to %s: %s", phone, exc)
                logger.debug(traceback.format_exc())

        if sent:
            logger.info("Trigger delayed_onboarding sent=%d", sent)

    # ── Snapshot refresh ─────────────────────────────────────────────────────

    async def _run_snapshot_refresh(self) -> None:
        """Write daily inventory snapshots from live orders + inventory tables."""
        from datetime import date
        from kirana.repository import KiranaRepository

        # One replica only (Azure Container Apps may run several).
        lock_conn = self._try_lock(_SNAPSHOT_LOCK_KEY)
        if lock_conn is None:
            logger.info("Snapshot refresh: another replica holds the lock — skipping here")
            return

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
                            i.quantity                               AS stock_on_hand,
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
        finally:
            self._release_lock(lock_conn, _SNAPSHOT_LOCK_KEY)

    # ── ML model retraining ───────────────────────────────────────────────────

    async def _run_ml_retrain(self) -> None:
        """
        Retrain all ML models on the latest DB data, then reload CSVs into memory.

        Runs ml_models/train_all.py as a subprocess using the same Python
        interpreter as the server process (conda kirana-ml env).
        Scheduled nightly at 3am IST — 1 hour after snapshot_refresh writes
        the latest inventory data.
        """
        import asyncio
        import sys
        import os

        # Only one replica should run the (expensive) training subprocess.
        lock_conn = self._try_lock(_ML_RETRAIN_LOCK_KEY)
        if lock_conn is None:
            logger.info("ML retrain: another replica holds the lock — skipping here")
            return

        try:
            train_script = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "ml_models", "train_all.py")
            )

            if not os.path.isfile(train_script):
                logger.error("ML retrain: train_all.py not found at %s", train_script)
                return

            logger.info("ML retrain: starting %s", train_script)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, train_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=os.path.dirname(train_script),
            )
            stdout, _ = await proc.communicate()

            if stdout:
                # Log last 4000 chars to avoid flooding logs with full training output
                output = stdout.decode(errors="replace")
                logger.info("ML retrain output (tail):\n%s", output[-4000:])

            if proc.returncode != 0:
                logger.error("ML retrain failed with exit code %d", proc.returncode)
                return

            logger.info("ML retrain completed — refreshing predictions in memory")
            if self._kirana_svc is not None:
                self._kirana_svc.ml.refresh()
                rows = self._kirana_svc.ml.get_frame().shape[0]
                logger.info("ML predictions refreshed: %d rows loaded", rows)

        except Exception:
            logger.exception("ML retrain failed unexpectedly")
        finally:
            self._release_lock(lock_conn, _ML_RETRAIN_LOCK_KEY)

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

    @property
    def handlers(self) -> list[str]:
        return [
            "morning_greeting", "evening_summary", "weekly_report",
            "overdue_udhaar", "distributor_due", "low_stock_alert",
            "expiry_alert", "inactive_customer", "feature_discovery",
            "abandoned_cart", "delayed_onboarding", "snapshot_refresh",
            "ml_retrain", "ml_refresh",
        ]

    async def fire(self, trigger_name: str, store_id: int | None = None) -> dict:
        """
        Manually fire a trigger immediately, bypassing deduplication.
        Used by the admin API for testing.
        """
        method_map = {
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
            "delayed_onboarding": self._run_delayed_onboarding,
            "snapshot_refresh":  self._run_snapshot_refresh,
            "ml_retrain":        self._run_ml_retrain,
            "ml_refresh":        self._run_ml_refresh,
        }
        handler = method_map.get(trigger_name)
        if not handler:
            return {"error": f"Unknown trigger: {trigger_name}"}
        await handler()
        return {"fired": trigger_name}
