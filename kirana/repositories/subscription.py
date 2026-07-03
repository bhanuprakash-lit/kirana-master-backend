from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class SubscriptionRepositoryMixin:
    def get_segment_prices(self, store_id: int) -> dict:
        """Basic/Pro monthly price for this store's segment (store.store_type),
        falling back to the '__default__' row for any store_type with no
        dedicated price (e.g. fruits_vegetables, other, unknown)."""
        sql = """
        SELECT COALESCE(sp.basic_price, d.basic_price) AS basic_price,
               COALESCE(sp.pro_price,   d.pro_price)   AS pro_price
        FROM kirana_oltp.store s
        LEFT JOIN kirana_oltp.segment_pricing sp ON sp.store_type = s.store_type
        LEFT JOIN kirana_oltp.segment_pricing d ON d.store_type = '__default__'
        WHERE s.store_id = :sid
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        if not row or row["basic_price"] is None:
            return {"basic": 200, "pro": 500}
        return {"basic": row["basic_price"], "pro": row["pro_price"]}

    def get_active_subscription(self, store_id: int) -> dict | None:
        sql = """
        SELECT * FROM kirana_oltp.subscription
        WHERE store_id = :sid
          AND (ended_at IS NULL OR ended_at > NOW())
        ORDER BY started_at DESC
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        if not row:
            return None
        d = dict(row)
        from datetime import datetime

        now = datetime.now()
        if d.get("is_trial") and d.get("trial_ends_at"):
            delta = d["trial_ends_at"] - now
            d["days_remaining"] = max(0, delta.days)
            d["seconds_remaining"] = max(0, int(delta.total_seconds()))
            d["is_expired"] = delta.total_seconds() <= 0
            d["trial_ends_at"] = d["trial_ends_at"].isoformat()
        else:
            d["days_remaining"] = 0
            d["seconds_remaining"] = 0
            d["is_expired"] = False
        if d.get("started_at"):
            d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"):
            d["ended_at"] = d["ended_at"].isoformat()
        prices = self.get_segment_prices(store_id)
        d["basic_price"] = prices["basic"]
        d["pro_price"] = prices["pro"]
        with self._conn() as conn:
            store_type = conn.execute(
                text("SELECT store_type FROM kirana_oltp.store WHERE store_id = :sid"),
                {"sid": store_id},
            ).scalar()
        d["store_type"] = store_type
        return d

    def request_trial(self, store_id: int, requested_tier: str = "basic") -> dict:
        """Create or reset to pending_trial. Updates the most recent cancelled row, or inserts a fresh one."""
        if requested_tier not in ("basic", "pro"):
            requested_tier = "basic"

        existing = self.get_active_subscription(store_id)
        if existing:
            # Active (non-cancelled) subscription exists
            if existing.get("tier") == "pending_trial":
                # Allow updating requested_tier on an existing pending request
                with self._conn() as conn:
                    conn.execute(
                        text(
                            "UPDATE kirana_oltp.subscription SET requested_tier = :rt "
                            "WHERE store_id = :sid AND tier = 'pending_trial'"
                        ),
                        {"rt": requested_tier, "sid": store_id},
                    )
                    conn.commit()
                existing["requested_tier"] = requested_tier
            return existing

        # No active subscription (first-time or previously cancelled).
        # Try to UPDATE the most recent cancelled row back to pending_trial.
        # If no row exists at all, INSERT a fresh one.
        with self._conn() as conn:
            updated = (
                conn.execute(
                    text("""
                UPDATE kirana_oltp.subscription
                SET tier           = 'pending_trial',
                    monthly_price  = 0,
                    started_at     = NOW(),
                    is_trial       = TRUE,
                    requested_tier = :rt,
                    ended_at       = NULL,
                    trial_ends_at  = NULL
                WHERE store_id = :sid
                  AND subscription_id = (
                      SELECT subscription_id FROM kirana_oltp.subscription
                      WHERE store_id = :sid
                      ORDER BY started_at DESC
                      LIMIT 1
                  )
                RETURNING *
            """),
                    {"sid": store_id, "rt": requested_tier},
                )
                .mappings()
                .first()
            )

            if updated:
                row = updated
            else:
                row = (
                    conn.execute(
                        text("""
                    INSERT INTO kirana_oltp.subscription
                        (store_id, tier, monthly_price, started_at, is_trial, requested_tier)
                    VALUES (:sid, 'pending_trial', 0, NOW(), TRUE, :rt)
                    RETURNING *
                """),
                        {"sid": store_id, "rt": requested_tier},
                    )
                    .mappings()
                    .first()
                )
            conn.commit()
        d = dict(row)
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"):
            d["ended_at"] = d["ended_at"].isoformat()
        d["days_remaining"] = 0
        d["seconds_remaining"] = 0
        return d

    def approve_trial(self, store_id: int, trial_days: int) -> dict:
        """Promote pending_trial → trial, preserving the requested tier."""
        from datetime import datetime, timedelta

        trial_ends_at = datetime.now() + timedelta(days=trial_days)
        # Read requested_tier before updating
        with self._conn() as conn:
            pending = (
                conn.execute(
                    text(
                        "SELECT requested_tier FROM kirana_oltp.subscription WHERE store_id = :sid AND tier = 'pending_trial'"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .first()
            )
        if not pending:
            raise ValueError(f"No pending trial found for store {store_id}")
        trial_tier = pending["requested_tier"] or "basic"
        sql = """
        UPDATE kirana_oltp.subscription
        SET tier = 'trial',
            trial_tier = :tt,
            trial_ends_at = :te,
            ended_at = NULL
        WHERE store_id = :sid
          AND tier = 'pending_trial'
        RETURNING *
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql), {"sid": store_id, "te": trial_ends_at, "tt": trial_tier}
                )
                .mappings()
                .first()
            )
            conn.commit()
        if not row:
            raise ValueError(f"No pending trial found for store {store_id}")
        d = dict(row)
        d["days_remaining"] = trial_days
        d["seconds_remaining"] = int(trial_days * 86400)
        d["trial_ends_at"] = d["trial_ends_at"].isoformat()
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"):
            d["ended_at"] = d["ended_at"].isoformat()
        return d

    def extend_trial(self, store_id: int, days: int) -> dict:
        """Extend an active trial by `days`, added to the current end date.
        If the trial already lapsed (or has no end date), extends from now."""
        from datetime import datetime, timedelta

        if days <= 0:
            raise ValueError("Extension days must be a positive number")
        sub = self.get_active_subscription(store_id)
        if not sub or sub.get("tier") != "trial":
            raise ValueError(f"No active trial to extend for store {store_id}")
        now = datetime.now()
        current_end = None
        if sub.get("trial_ends_at"):
            current_end = datetime.fromisoformat(sub["trial_ends_at"])
        base = current_end if (current_end and current_end > now) else now
        new_end = base + timedelta(days=days)
        sql = """
        UPDATE kirana_oltp.subscription
        SET trial_ends_at = :te,
            ended_at = NULL
        WHERE store_id = :sid
          AND tier = 'trial'
          AND (ended_at IS NULL OR ended_at > NOW())
        RETURNING *
        """
        with self._conn() as conn:
            row = (
                conn.execute(text(sql), {"sid": store_id, "te": new_end})
                .mappings()
                .first()
            )
            conn.commit()
        if not row:
            raise ValueError(f"No active trial to extend for store {store_id}")
        d = dict(row)
        delta = new_end - now
        d["days_remaining"] = max(0, delta.days)
        d["seconds_remaining"] = max(0, int(delta.total_seconds()))
        d["trial_ends_at"] = d["trial_ends_at"].isoformat()
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"):
            d["ended_at"] = d["ended_at"].isoformat()
        return d

    def cancel_subscription(self, store_id: int) -> dict:
        """Mark current subscription as ended."""
        sql = """
        UPDATE kirana_oltp.subscription
        SET ended_at = NOW()
        WHERE store_id = :sid
          AND (ended_at IS NULL OR ended_at > NOW())
          AND tier NOT IN ('pending_trial')
        RETURNING *
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
            conn.commit()
        if not row:
            raise ValueError("No active subscription to cancel")
        d = dict(row)
        if d.get("started_at"):
            d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"):
            d["ended_at"] = d["ended_at"].isoformat()
        if d.get("trial_ends_at"):
            d["trial_ends_at"] = d["trial_ends_at"].isoformat()
        return d

    def upgrade_subscription(self, store_id: int, tier: str) -> dict:
        prices = self.get_segment_prices(store_id)
        if tier not in prices:
            raise ValueError(f"Invalid tier: {tier}")
        with self._conn() as conn:
            conn.execute(
                text("""
                UPDATE kirana_oltp.subscription
                SET ended_at = NOW()
                WHERE store_id = :sid AND (ended_at IS NULL OR ended_at > NOW())
            """),
                {"sid": store_id},
            )
            row = (
                conn.execute(
                    text("""
                INSERT INTO kirana_oltp.subscription
                    (store_id, tier, monthly_price, started_at, is_trial)
                VALUES (:sid, :tier, :price, NOW(), FALSE)
                RETURNING *
            """),
                    {"sid": store_id, "tier": tier, "price": prices[tier]},
                )
                .mappings()
                .first()
            )
            conn.commit()
        d = dict(row)
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"):
            d["ended_at"] = d["ended_at"].isoformat()
        d["days_remaining"] = 0
        d["seconds_remaining"] = 0
        return d

    def create_razorpay_order(
        self, store_id: int, tier: str, key_id: str, key_secret: str
    ) -> dict:
        """Call Razorpay API to create a payment order. Returns order details."""
        import requests as req_lib

        prices = self.get_segment_prices(store_id)
        if tier not in prices:
            raise ValueError(f"Invalid tier: {tier}")
        amount_paise = int(prices[tier] * 100)  # Razorpay uses paise
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"kirana_{store_id}_{tier}",
            "notes": {"store_id": str(store_id), "tier": tier},
        }
        resp = req_lib.post(
            "https://api.razorpay.com/v1/orders",
            json=payload,
            auth=(key_id, key_secret),
            timeout=15,
        )
        if resp.status_code != 200:
            raise ValueError(f"Razorpay order creation failed: {resp.text}")
        order = resp.json()
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key_id": key_id,
            "tier": tier,
        }

    def verify_razorpay_payment(
        self,
        store_id: int,
        tier: str,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
        key_secret: str,
    ) -> dict:
        """Verify HMAC signature and upgrade subscription on success."""
        import hmac
        import hashlib

        expected = hmac.new(
            key_secret.encode(),
            f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if expected != razorpay_signature:
            raise ValueError("Payment signature verification failed")
        return self.upgrade_subscription(store_id, tier)

    # ── Admin settings ────────────────────────────────────────────────────────

    def get_admin_setting(self, key: str, default: str = "") -> str:
        sql = "SELECT value FROM kirana_oltp.admin_settings WHERE key = :key"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"key": key}).mappings().first()
        return row["value"] if row else default

    def set_admin_setting(self, key: str, value: str) -> None:
        sql = """
        INSERT INTO kirana_oltp.admin_settings (key, value, updated_at)
        VALUES (:key, :value, NOW())
        ON CONFLICT (key) DO UPDATE SET value = :value, updated_at = NOW()
        """
        with self._conn() as conn:
            conn.execute(text(sql), {"key": key, "value": value})
            conn.commit()
