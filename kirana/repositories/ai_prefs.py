from __future__ import annotations
import logging
from sqlalchemy import text
from fastapi import HTTPException

logger = logging.getLogger("kirana.repository")


class Ai_prefsRepositoryMixin:
    _AI_DAILY_LIMITS: dict[str, int] = {
        "voice":     3,
        "handwrite": 5,
        "invoice":   2,
    }
    _PREF_DEFAULTS = {
        "forecast_horizon_days":    7,
        "alert_stockout_threshold": 0.5,
        "alert_min_velocity":       0.3,
        "alert_reorder_days":       3,
        "alert_dead_stock_days":    21,
        "alert_expiry_days":        7,
        "notify_whatsapp":          False,
        "notify_in_app":            True,
        "quiet_hours_start":        22,
        "quiet_hours_end":          7,
        "subscribed_kpis":          None,
        "allow_social_marketing":   False,
    }

    def check_and_record_ai_use(self, user_id: int, feature: str) -> None:
        """
        Atomically checks whether the user may use this AI feature and records
        one use.  Raises HTTPException 429 when the daily quota is exhausted
        AND no credits remain.
        """
        import datetime

        today = datetime.date.today().isoformat()
        daily_lim = self._AI_DAILY_LIMITS.get(feature, 0)

        with self._conn() as conn:
            # Ensure a today-row exists, then lock it
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.ai_usage (user_id, feature, usage_date, count)
                VALUES (:uid, :feat, :today, 0)
                ON CONFLICT (user_id, feature, usage_date) DO NOTHING
            """),
                {"uid": user_id, "feat": feature, "today": today},
            )

            used = (
                conn.execute(
                    text("""
                SELECT count FROM kirana_oltp.ai_usage
                WHERE user_id = :uid AND feature = :feat AND usage_date = :today
                FOR UPDATE
            """),
                    {"uid": user_id, "feat": feature, "today": today},
                ).scalar()
                or 0
            )

            if used < daily_lim:
                conn.execute(
                    text("""
                    UPDATE kirana_oltp.ai_usage
                    SET count = count + 1
                    WHERE user_id = :uid AND feature = :feat AND usage_date = :today
                """),
                    {"uid": user_id, "feat": feature, "today": today},
                )
            else:
                # Try credits — lock the row first
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.ai_credits (user_id, feature, balance)
                    VALUES (:uid, :feat, 0)
                    ON CONFLICT (user_id, feature) DO NOTHING
                """),
                    {"uid": user_id, "feat": feature},
                )

                balance = (
                    conn.execute(
                        text("""
                    SELECT balance FROM kirana_oltp.ai_credits
                    WHERE user_id = :uid AND feature = :feat
                    FOR UPDATE
                """),
                        {"uid": user_id, "feat": feature},
                    ).scalar()
                    or 0
                )

                if balance <= 0:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Daily limit reached for {feature}. Purchase credits to continue.",
                    )
                conn.execute(
                    text("""
                    UPDATE kirana_oltp.ai_credits
                    SET balance = balance - 1
                    WHERE user_id = :uid AND feature = :feat
                """),
                    {"uid": user_id, "feat": feature},
                )

            conn.commit()

    def get_ai_status(self, user_id: int) -> dict:
        """Return current usage + credits for all AI features."""
        import datetime

        today = datetime.date.today().isoformat()

        with self._conn() as conn:
            usage_rows = (
                conn.execute(
                    text("""
                SELECT feature, count FROM kirana_oltp.ai_usage
                WHERE user_id = :uid AND usage_date = :today
            """),
                    {"uid": user_id, "today": today},
                )
                .mappings()
                .all()
            )

            credit_rows = (
                conn.execute(
                    text("""
                SELECT feature, balance FROM kirana_oltp.ai_credits
                WHERE user_id = :uid
            """),
                    {"uid": user_id},
                )
                .mappings()
                .all()
            )

        used_map = {r["feature"]: r["count"] for r in usage_rows}
        credits_map = {r["feature"]: r["balance"] for r in credit_rows}

        result = {}
        for feat, lim in self._AI_DAILY_LIMITS.items():
            used = used_map.get(feat, 0)
            credits = credits_map.get(feat, 0)
            free_left = max(0, lim - used)
            remaining = free_left if free_left > 0 else credits
            result[feat] = {
                "used": used,
                "limit": lim,
                "credits": credits,
                "remaining": remaining,
            }
        return result

    def add_ai_credits(self, user_id: int, feature: str, count: int) -> dict:
        """Add purchased credits for a feature and return updated status."""
        with self._conn() as conn:
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.ai_credits (user_id, feature, balance)
                VALUES (:uid, :feat, :count)
                ON CONFLICT (user_id, feature)
                DO UPDATE SET balance = ai_credits.balance + :count
            """),
                {"uid": user_id, "feat": feature, "count": count},
            )
            conn.commit()
        return self.get_ai_status(user_id)

    def get_user_prefs(self, user_id: int) -> dict:
        sql = "SELECT * FROM kirana_oltp.user_prefs WHERE user_id = :uid"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        return dict(row) if row else {**self._PREF_DEFAULTS, "user_id": user_id}

    def upsert_user_prefs(self, user_id: int, **fields) -> dict:
        clean = {
            k: v
            for k, v in fields.items()
            if k in self._PREF_DEFAULTS and v is not None
        }
        if not clean:
            return self.get_user_prefs(user_id)
        merged = {**self._PREF_DEFAULTS, **clean}
        cols = list(merged.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in clean)
        sql = (
            f"INSERT INTO kirana_oltp.user_prefs(user_id, {', '.join(cols)}, updated_at) "
            f"VALUES(:uid, {placeholders}, NOW()) "
            f"ON CONFLICT (user_id) DO UPDATE SET {update_set}, updated_at = NOW() "
            f"RETURNING *"
        )
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id, **merged}).mappings().first()
            conn.commit()
        return dict(row)
