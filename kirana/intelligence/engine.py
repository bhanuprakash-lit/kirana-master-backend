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
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from kirana.fcm_sender import send_to_token, UNREGISTERED
from kirana.intelligence.repository import IntelligenceRepository
from kirana.intelligence import triggers as T

logger = logging.getLogger("kirana.intelligence.engine")

_IST_TZ = ZoneInfo("Asia/Kolkata")


def _hour(val, default: int) -> int:
    """Coerce a stored quiet-hour to an int, defaulting ONLY when it's missing.
    (Plain `int(val or default)` is wrong because hour 0 = midnight is falsy and
    would be silently replaced by the default.)"""
    return default if val is None else int(val)


def _in_quiet_hours(start_h: int, end_h: int) -> bool:
    """True if the current IST hour falls inside [start_h, end_h).
    Handles overnight windows, e.g. start=22 end=7 covers 22:00–06:59.
    When start == end the window is empty (never quiet), not all-day."""
    if start_h == end_h:
        return False
    now_h = datetime.now(_IST_TZ).hour
    if start_h > end_h:           # overnight window (e.g. 22 → 7)
        return now_h >= start_h or now_h < end_h
    return start_h <= now_h < end_h


def _in_activity_window(target_hour: int, window_minutes: int = 59) -> bool:
    """True if the current IST hour equals `target_hour` (within the hour).
    Jobs fire hourly at :00; a full-hour window means a job that was delayed by
    misfire recovery still counts, while daily dedupe prevents any double-send."""
    now = datetime.now(_IST_TZ)
    start = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    elapsed = (now - start).total_seconds()
    return 0 <= elapsed < window_minutes * 60

_IST = "Asia/Kolkata"

# Postgres advisory-lock keys: the scheduler runs in EVERY replica (Azure
# Container Apps may scale to several), so the heavy nightly jobs guard
# themselves — only the replica that wins the lock actually runs them.
# Abandoned-cart is checked every 5 min and re-nudges hourly — cap the phone
# pushes so we never spam. After this many FCM sends in a day, further nudges
# go in-app only (logged status='internal', shown in the notification feed).
_CART_FCM_DAILY_CAP = 3

_SNAPSHOT_LOCK_KEY = 994201
_ML_RETRAIN_LOCK_KEY = 994202
_KPI_RETRAIN_LOCK_KEY = 994203


class IntelligenceEngine:
    def __init__(self, engine, kirana_svc=None):
        self._db = engine
        self._kirana_svc = kirana_svc
        # Per-store open/close hours change at most once a day, but the percentile
        # query over 30 days of orders is expensive. Compute it once per IST day
        # and reuse across all dispatch cycles. {store_id: (open_h, close_h)}
        self._activity_cache: dict[int, tuple[int, int]] = {}
        self._activity_cache_day: date | None = None
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

        # Personalised notifications fire at each store's derived open/close hour.
        # Targets are always whole hours, so an hourly wall-clock-aligned cron
        # (minute=0) is enough — each handler checks if THIS hour is the store's
        # target. CronTrigger(minute=0) is phase-locked to :00 (unlike
        # IntervalTrigger, which drifts from scheduler-start time); a generous
        # misfire grace lets a job still run if the loop was briefly busy.
        _hourly = dict(trigger=CronTrigger(minute=0, timezone=_IST),
                       misfire_grace_time=1800, coalesce=True, replace_existing=True)

        # Daily greetings & summaries
        s.add_job(self._run_morning_greeting, id="morning_greeting", **_hourly)
        s.add_job(self._run_evening_summary,  id="evening_summary",  **_hourly)

        # Daily operational alerts — staggered hours after each store's open time.
        s.add_job(self._run_distributor_due,  id="distributor_due",  **_hourly)
        s.add_job(self._run_expiry_alert,     id="expiry_alert",     **_hourly)
        s.add_job(self._run_low_stock_alert,  id="low_stock_alert",  **_hourly)
        s.add_job(self._run_overdue_udhaar,   id="overdue_udhaar",   **_hourly)

        # Weekly jobs — handlers additionally gate on the day of week.
        s.add_job(self._run_weekly_report,     id="weekly_report",     **_hourly)
        s.add_job(self._run_inactive_customer, id="inactive_customer", **_hourly)
        s.add_job(self._run_feature_discovery, id="feature_discovery", **_hourly)

        # Abandoned cart — checked every 5 minutes
        s.add_job(self._run_abandoned_cart, IntervalTrigger(minutes=5), id="abandoned_cart", replace_existing=True)

        # Nightly inventory snapshot (2am IST) — keeps ML predictions fresh
        s.add_job(self._run_snapshot_refresh, CronTrigger(hour=2, minute=0, timezone=_IST), id="snapshot_refresh", replace_existing=True)

        # Nightly ML retrain (3am IST) — runs train_all.py on latest DB data, then reloads CSVs
        # Scheduled 1 hour after snapshot_refresh so fresh inventory is in DB first.
        s.add_job(self._run_ml_retrain, CronTrigger(hour=3, minute=0, timezone=_IST), id="ml_retrain", replace_existing=True)

        # Weekly KPI model retrain (Sunday 4am IST) — churn/BCG/trial/shrinkage/supplier models.
        # These are slower-changing customer/category signals; weekly is sufficient.
        s.add_job(self._run_kpi_retrain, CronTrigger(day_of_week="sun", hour=4, minute=0, timezone=_IST), id="kpi_retrain", replace_existing=True)

        # ML prediction refresh every 6 hours — safety net reload of CSVs
        s.add_job(self._run_ml_refresh, IntervalTrigger(hours=6), id="ml_refresh", replace_existing=True)

        logger.info("Intelligence engine: %d jobs registered", len(s.get_jobs()))

    # ── Core dispatch helpers ─────────────────────────────────────────────────

    def _repo(self) -> IntelligenceRepository:
        return IntelligenceRepository(self._db)

    def _activity_hours(self, repo: IntelligenceRepository) -> dict[int, tuple[int, int]]:
        """Per-store (open_hour, close_hour), computed once per IST day and cached.
        The underlying percentile query scans 30 days of orders, so we must not
        re-run it on every hourly dispatch."""
        today = datetime.now(_IST_TZ).date()
        if self._activity_cache_day != today:
            try:
                self._activity_cache = repo.get_store_activity_hours()
                self._activity_cache_day = today
            except Exception:
                logger.exception("activity-hours refresh failed — using stale/empty cache")
        return self._activity_cache

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

    async def _dispatch(
        self,
        trigger_name: str,
        trigger_fn,
        dedupe: str = "daily",     # "daily" | "weekly" | "none"
        *,
        hour_offset: int = 0,      # hours after store open (0=open, 1=open+1h, etc.)
        use_close: bool = False,   # anchor to close_hour instead of open_hour
        extra_kwargs: dict | None = None,
    ) -> None:
        repo = self._repo()
        stores = repo.get_active_stores()
        activity = self._activity_hours(repo)
        sent = failed = skipped = 0

        for store in stores:
            store_id = store["store_id"]
            user_id  = store["user_id"]
            token    = store["fcm_token"]

            try:
                # Activity-window guard: only fire when the current IST hour is
                # this store's personalised target (derived open/close ± offset).
                open_h, close_h = activity.get(store_id, (8, 21))
                base_hour = close_h if use_close else open_h
                target_h  = min(max(base_hour + hour_offset, 0), 23)
                if not _in_activity_window(target_h):
                    skipped += 1
                    continue

                # Quiet hours guard. Default window is a narrow 23:00–05:00 so it
                # only catches genuine middle-of-night sends — a wider default
                # (e.g. 22–07) would collide with real shop hours (6 AM opens,
                # 22:00 closes) and silently suppress personalised notifications.
                q_start = _hour(store.get("quiet_hours_start"), 23)
                q_end   = _hour(store.get("quiet_hours_end"), 5)
                if _in_quiet_hours(q_start, q_end):
                    skipped += 1
                    continue

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
        # Fires at each store's typical open hour (open + 0h)
        await self._dispatch("morning_greeting", T.morning_greeting, dedupe="daily", hour_offset=0)

    async def _run_evening_summary(self) -> None:
        # Fires at each store's typical close hour
        await self._dispatch("evening_summary", T.evening_summary, dedupe="daily", use_close=True)

    async def _run_distributor_due(self) -> None:
        # 1h after open — store owner has settled in before stock reminders hit
        await self._dispatch("distributor_due", T.distributor_due, dedupe="daily", hour_offset=1)

    async def _run_expiry_alert(self) -> None:
        # 1h after open, same cluster as distributor
        await self._dispatch("expiry_alert", T.expiry_alert, dedupe="daily", hour_offset=1)

    async def _run_low_stock_alert(self) -> None:
        # 2h after open — actionable once the day is underway
        await self._dispatch("low_stock_alert", T.low_stock_alert, dedupe="daily", hour_offset=2)

    async def _run_overdue_udhaar(self) -> None:
        # 2h after open — mid-morning udhaar reminder
        await self._dispatch("overdue_udhaar", T.overdue_udhaar, dedupe="daily", hour_offset=2)

    async def _run_weekly_report(self) -> None:
        if datetime.now(_IST_TZ).weekday() != 0:   # 0 = Monday
            return
        await self._dispatch("weekly_report", T.weekly_report, dedupe="weekly", hour_offset=1)

    async def _run_inactive_customer(self) -> None:
        if datetime.now(_IST_TZ).weekday() != 2:   # 2 = Wednesday
            return
        await self._dispatch("inactive_customer", T.inactive_customer, dedupe="weekly", hour_offset=2)

    async def _run_feature_discovery(self) -> None:
        if datetime.now(_IST_TZ).weekday() != 4:   # 4 = Friday
            return
        await self._dispatch("feature_discovery", T.feature_discovery, dedupe="weekly", hour_offset=2)

    async def _run_abandoned_cart(self) -> None:
        """Special case: uses cart_session table, not the generic store loop."""
        repo = self._repo()
        sessions = repo.get_abandoned_sessions(stale_minutes=10)
        sent = skipped = internal = 0

        for session in sessions:
            store_id   = session["store_id"]
            user_id    = session["user_id"]
            token      = session["fcm_token"]
            item_count = session["item_count"]
            cart_data  = session.get("cart_data") or []

            # Respect per-store quiet hours (default 22:00–07:00 IST)
            q_start = _hour(session.get("quiet_hours_start"), 22)
            q_end   = _hour(session.get("quiet_hours_end"), 7)
            if _in_quiet_hours(q_start, q_end):
                skipped += 1
                continue

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

                # Daily FCM cap: at most 3 abandoned-cart PUSHES per store per day.
                # Beyond that, keep nudging but ONLY in-app (log with status
                # 'internal', no FCM) so we stop spamming the owner's phone.
                if repo.count_sent_today(store_id, "abandoned_cart") >= _CART_FCM_DAILY_CAP:
                    repo.log_notification(
                        store_id=store_id, user_id=user_id,
                        trigger_type="abandoned_cart",
                        title=title, body=body, payload=payload,
                        status="internal",
                    )
                    repo.mark_cart_notified(store_id)  # keep the hourly cadence
                    internal += 1
                    continue

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

        if sent or skipped or internal:
            logger.info("Trigger abandoned_cart sent=%d internal=%d skipped=%d",
                        sent, internal, skipped)

    # ── Snapshot refresh ─────────────────────────────────────────────────────

    async def _run_snapshot_refresh(self) -> None:
        """Write daily inventory snapshots from live orders + inventory tables."""
        from datetime import date, timedelta
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository

        # One replica only (Azure Container Apps may run several).
        lock_conn = self._try_lock(_SNAPSHOT_LOCK_KEY)
        if lock_conn is None:
            logger.info("Snapshot refresh: another replica holds the lock — skipping here")
            return

        # Runs at 2AM IST — write yesterday's snapshot (yesterday is a complete day).
        yesterday = (date.today() - timedelta(days=1)).isoformat()
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
                              AND o.order_date::date = :snap_date
                        ) s30 ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT price FROM kirana_oltp.pricing pr
                            WHERE pr.product_id = i.product_id AND pr.store_id = i.store_id
                            ORDER BY pr.valid_from DESC LIMIT 1
                        ) pr ON TRUE
                        WHERE i.store_id = :sid
                    """), {"sid": sid, "snap_date": yesterday}).mappings().all()

                items = [dict(r) for r in rows]
                if items:
                    n = repo.upsert_inventory_snapshot(int(sid), yesterday, items)
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

                # train_all.py deliberately swallows a load_to_db() failure so the
                # models still get saved — which means a retrain can report success
                # while ml_signals stays stale for days, and every CSV-based status
                # check looks green. The forecast + ML cards read ml_signals, so a
                # stale table silently degrades them. Verify the table actually
                # advanced and shout if it didn't.
                try:
                    sf = self._kirana_svc.ml.signals_freshness()
                    age = sf.get("age_hours")
                    if not sf.get("available"):
                        logger.error(
                            "ML retrain: ml_signals freshness unavailable after a "
                            "successful retrain (%s) — cannot confirm the forecast "
                            "data was updated", sf.get("reason"))
                    elif age is None or age > 2:
                        logger.error(
                            "ML retrain: ml_signals NOT REFRESHED — table is %sh old "
                            "(%d rows, %d stores) after a retrain that reported success. "
                            "load_to_db() almost certainly failed; the forecast and ML "
                            "cards are serving stale data. Check train_all.py's Postgres "
                            "load step in the retrain log.",
                            age, sf.get("rows", 0), sf.get("stores", 0))
                    else:
                        logger.info(
                            "ML retrain: ml_signals refreshed (%sh old, %d rows, %d stores)",
                            age, sf.get("rows", 0), sf.get("stores", 0))
                except Exception:
                    logger.exception("ML retrain: ml_signals freshness check failed")

        except Exception:
            logger.exception("ML retrain failed unexpectedly")
        finally:
            self._release_lock(lock_conn, _ML_RETRAIN_LOCK_KEY)

    # ── KPI model retraining (weekly) ────────────────────────────────────────

    async def _run_kpi_retrain(self) -> None:
        """
        Retrain the 5 KPI ML models (churn, BCG, trial, shrinkage, supplier).
        Runs train_kpi_models.py as a subprocess. Scheduled weekly on Sundays
        at 4am IST. KPI models are customer/category-level signals that change
        more slowly than inventory, so weekly retraining is sufficient.
        """
        import asyncio
        import sys
        import os

        lock_conn = self._try_lock(_KPI_RETRAIN_LOCK_KEY)
        if lock_conn is None:
            logger.info("KPI retrain: another replica holds the lock — skipping here")
            return

        try:
            kpi_script = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..",
                             "ml_models", "kpi_models", "train_kpi_models.py")
            )
            if not os.path.isfile(kpi_script):
                logger.error("KPI retrain: train_kpi_models.py not found at %s", kpi_script)
                return

            logger.info("KPI retrain: starting %s", kpi_script)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, kpi_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=os.path.dirname(kpi_script),
            )
            stdout, _ = await proc.communicate()
            if stdout:
                logger.info("KPI retrain output (tail):\n%s",
                            stdout.decode(errors="replace")[-2000:])

            if proc.returncode != 0:
                logger.error("KPI retrain failed with exit code %d", proc.returncode)
                return

            logger.info("KPI retrain completed — reloading KPI model artifacts")
            from kpis.ml_inference import get_kpi_models
            get_kpi_models().reload()

        except Exception:
            logger.exception("KPI retrain failed unexpectedly")
        finally:
            self._release_lock(lock_conn, _KPI_RETRAIN_LOCK_KEY)

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

    def _handler_map(self) -> dict:
        """name -> coroutine for every manually-fireable trigger."""
        return {
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
            "ml_retrain":        self._run_ml_retrain,
            "kpi_retrain":       self._run_kpi_retrain,
            "ml_refresh":        self._run_ml_refresh,
        }

    def available_triggers(self) -> list[str]:
        """JSON-safe list of trigger names for the admin panel."""
        return sorted(self._handler_map().keys())

    async def fire(self, trigger_name: str, store_id: int | None = None) -> dict:
        """
        Manually fire a trigger immediately, bypassing deduplication.
        Used by the admin API for testing.
        """
        handler = self._handler_map().get(trigger_name)
        if not handler:
            return {"error": f"Unknown trigger: {trigger_name}"}
        await handler()
        return {"fired": trigger_name}
