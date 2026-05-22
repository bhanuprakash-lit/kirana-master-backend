"""
PostgreSQL-backed repository for Kirana users, stores, sessions, snapshots.

All data lives in kirana_oltp schema — no public schema tables.
On first startup the _ensure_schema / migration methods extend kirana_oltp with
the extra auth/app columns it needs and copy across any rows that exist in the
old public-schema tables (kirana_app_users, kirana_user_sessions, kirana_stores,
kirana_inventory_snapshots, kirana_user_prefs).
"""
from __future__ import annotations

import hashlib
import secrets
import logging

from sqlalchemy import inspect as sa_inspect, text

logger = logging.getLogger("kirana.repository")

# Per-process flag — _ensure_schema runs at most once per gunicorn worker.
# The PG advisory lock below handles the cross-process race on first start.
_schema_initialized: bool = False


class KiranaRepository:
    def __init__(self, engine):
        self._engine = engine
        global _schema_initialized
        if not _schema_initialized:
            self._ensure_schema()
            _schema_initialized = True

    def _conn(self):
        return self._engine.connect()

    # ── Schema bootstrap ──────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Idempotently extend kirana_oltp with auth + app columns, then migrate.

        Uses a PG advisory lock (session-level) so concurrent gunicorn workers
        queue rather than deadlock on the ALTER TABLE statements.
        """
        with self._conn() as conn:
            # Transaction-level advisory lock — blocks concurrent workers, auto-releases on commit.
            # This prevents the deadlocks caused by multiple gunicorn workers ALTER-ing the same
            # tables simultaneously on startup.
            conn.execute(text("SELECT pg_advisory_xact_lock(1919191919)"))

            # kirana_oltp.users — add auth/app columns
            for ddl in [
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS full_name     VARCHAR(255) NOT NULL DEFAULT ''",
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS password_salt VARCHAR(64)",
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128)",
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS is_active     BOOLEAN NOT NULL DEFAULT TRUE",
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS fcm_token     VARCHAR(255)",
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS phone_number  VARCHAR(20)",
                "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS firebase_uid  VARCHAR(128)",
            ]:
                conn.execute(text(ddl))

            # Unique index on phone_number (only for non-null values)
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uidx_users_phone
                ON kirana_oltp.users(phone_number)
                WHERE phone_number IS NOT NULL
            """))

            # kirana_oltp.user_sessions
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.user_sessions (
                    session_id   BIGSERIAL PRIMARY KEY,
                    user_id      BIGINT NOT NULL
                                     REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    access_token VARCHAR(128) UNIQUE NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    revoked_at   TIMESTAMPTZ,
                    login_method VARCHAR(20) DEFAULT 'password'
                )
            """))
            conn.execute(text(
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS login_method VARCHAR(20) DEFAULT 'password'"
            ))

            # kirana_oltp.issue_report
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.issue_report (
                    report_id   BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id),
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    category    VARCHAR(50) NOT NULL,
                    title       VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    status      VARCHAR(20) DEFAULT 'open',
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """))

            # kirana_oltp.user_fcm_tokens — multi-device FCM token storage
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.user_fcm_tokens (
                    token_id    BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    fcm_token   VARCHAR(255) NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_user_fcm_tokens_token UNIQUE (fcm_token)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_user_fcm_tokens_user_id ON kirana_oltp.user_fcm_tokens(user_id)"
            ))

            # kirana_oltp.app_activity — foreground/background lifecycle events
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.app_activity (
                    id              BIGSERIAL PRIMARY KEY,
                    user_id         BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    event           VARCHAR(20) NOT NULL,   -- 'foreground' | 'background'
                    duration_sec    INT,                    -- seconds in foreground (set on background event)
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_app_activity_user_id ON kirana_oltp.app_activity(user_id, created_at)"
            ))

            # kirana_oltp.khata_payments — recovery history log
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.khata_payments (
                    payment_id  BIGSERIAL PRIMARY KEY,
                    khata_id    BIGINT NOT NULL REFERENCES kirana_oltp.khata(khata_id) ON DELETE CASCADE,
                    store_id    BIGINT NOT NULL,
                    amount      NUMERIC NOT NULL,
                    paid_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    notes       TEXT
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_khata_payments_khata_id ON kirana_oltp.khata_payments(khata_id)"
            ))

            # kirana_oltp.basket — product bundles / combo deals
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.basket (
                    basket_id   BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    name        VARCHAR(200) NOT NULL,
                    description TEXT,
                    price       NUMERIC,
                    valid_from  DATE,
                    valid_to    DATE,
                    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.basket_item (
                    id          BIGSERIAL PRIMARY KEY,
                    basket_id   BIGINT NOT NULL REFERENCES kirana_oltp.basket(basket_id) ON DELETE CASCADE,
                    product_id  BIGINT NOT NULL,
                    product_name VARCHAR(255),
                    qty         NUMERIC NOT NULL DEFAULT 1
                )
            """))

            # kirana_oltp.store — add app-metadata columns
            for ddl in [
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS store_type   VARCHAR(100) DEFAULT 'kirana'",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS footfall     INT",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS budget       NUMERIC",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS daily_budget NUMERIC",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS latitude     NUMERIC(10,7)",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS longitude    NUMERIC(10,7)",
            ]:
                conn.execute(text(ddl))

            # kirana_oltp.user_prefs
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.user_prefs (
                    user_id                  BIGINT PRIMARY KEY
                                                 REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    forecast_horizon_days    INT     NOT NULL DEFAULT 7,
                    alert_stockout_threshold REAL    NOT NULL DEFAULT 0.5,
                    alert_min_velocity       REAL    NOT NULL DEFAULT 0.3,
                    alert_reorder_days       INT     NOT NULL DEFAULT 3,
                    alert_dead_stock_days    INT     NOT NULL DEFAULT 21,
                    notify_whatsapp          BOOLEAN NOT NULL DEFAULT FALSE,
                    notify_in_app            BOOLEAN NOT NULL DEFAULT TRUE,
                    quiet_hours_start        INT     NOT NULL DEFAULT 22,
                    quiet_hours_end          INT     NOT NULL DEFAULT 7,
                    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            # kirana_oltp.inventory_snapshots — ensure upserted_at exists
            conn.execute(text(
                "ALTER TABLE kirana_oltp.inventory_snapshots "
                "ADD COLUMN IF NOT EXISTS upserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ))

            # kirana_oltp.cashflow_requests — cash support requests
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.cashflow_requests (
                    request_id       BIGSERIAL PRIMARY KEY,
                    store_id         BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    user_id          BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id),
                    amount_requested NUMERIC(12,2) NOT NULL,
                    selected_bank    VARCHAR(100),
                    status           VARCHAR(20) NOT NULL DEFAULT 'pending',
                    store_name       VARCHAR(200),
                    location         VARCHAR(500),
                    avg_footfall     INT,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            # kirana_oltp.purchases — extensions for Distributor Payments
            for ddl in [
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS total_amount   NUMERIC(12,2)",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS due_date       DATE",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS payment_status VARCHAR(20) DEFAULT 'unpaid'",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS notes          VARCHAR(255)",
            ]:
                conn.execute(text(ddl))

            # kirana_oltp.customer — add store_id for multi-tenancy + unique constraint + indexes
            conn.execute(text(
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS store_id BIGINT "
                "REFERENCES kirana_oltp.store(store_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_customer_store_id "
                "ON kirana_oltp.customer(store_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_customer_store_phone "
                "ON kirana_oltp.customer(store_id, phone)"
            ))
            # Performance indexes for high-frequency queries
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_orders_store_date "
                "ON kirana_oltp.orders(store_id, order_date DESC)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_inventory_store_product "
                "ON kirana_oltp.inventory_snapshots(store_id, product_id)"
            ))

            # ── Referral System tables ────────────────────────────────────────

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.referral_campaigns (
                    campaign_id           BIGSERIAL PRIMARY KEY,
                    store_id              BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    name                  VARCHAR(100) NOT NULL,
                    referral_discount_pct NUMERIC(5,2) NOT NULL DEFAULT 10,
                    milestone_every_n     INT NOT NULL DEFAULT 10,
                    milestone_reward_pct  NUMERIC(5,2) NOT NULL DEFAULT 5,
                    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.referral_tokens (
                    token_id             BIGSERIAL PRIMARY KEY,
                    store_id             BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    referrer_customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
                    campaign_id          BIGINT NOT NULL REFERENCES kirana_oltp.referral_campaigns(campaign_id),
                    token_hash           VARCHAR(64) UNIQUE NOT NULL,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (referrer_customer_id, campaign_id)
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.referrals (
                    referral_id      BIGSERIAL PRIMARY KEY,
                    token_id         BIGINT NOT NULL REFERENCES kirana_oltp.referral_tokens(token_id),
                    new_customer_id  BIGINT REFERENCES kirana_oltp.customer(customer_id),
                    order_id         BIGINT REFERENCES kirana_oltp.orders(order_id),
                    discount_applied NUMERIC(5,2),
                    status           VARCHAR(20) NOT NULL DEFAULT 'rewarded',
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.referral_vouchers (
                    voucher_id       BIGSERIAL PRIMARY KEY,
                    customer_id      BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
                    store_id         BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    campaign_id      BIGINT NOT NULL REFERENCES kirana_oltp.referral_campaigns(campaign_id),
                    discount_pct     NUMERIC(5,2) NOT NULL,
                    status           VARCHAR(20) NOT NULL DEFAULT 'pending',
                    earned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    used_at          TIMESTAMPTZ,
                    used_on_order_id BIGINT REFERENCES kirana_oltp.orders(order_id)
                )
            """))

            # Referral-related column extensions
            for ddl in [
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS referral_count INT NOT NULL DEFAULT 0",
                "ALTER TABLE kirana_oltp.user_prefs ADD COLUMN IF NOT EXISTS allow_social_marketing BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE kirana_oltp.user_prefs ADD COLUMN IF NOT EXISTS alert_expiry_days INT NOT NULL DEFAULT 7",
                "ALTER TABLE kirana_oltp.referral_campaigns ADD COLUMN IF NOT EXISTS max_referrals_per_referrer INT NOT NULL DEFAULT 50",
                # Allow fractional quantities (loose items like rice, dal)
                "ALTER TABLE kirana_oltp.order_item ALTER COLUMN quantity TYPE NUMERIC USING quantity::NUMERIC",
            ]:
                conn.execute(text(ddl))

            # ── AI usage tracking (server-side, replaces SharedPreferences) ─────

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.ai_usage (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    feature     VARCHAR(20) NOT NULL,
                    usage_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                    count       INT NOT NULL DEFAULT 0,
                    UNIQUE (user_id, feature, usage_date)
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.ai_credits (
                    id       BIGSERIAL PRIMARY KEY,
                    user_id  BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    feature  VARCHAR(20) NOT NULL,
                    balance  INT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    UNIQUE (user_id, feature)
                )
            """))

            conn.commit()

        self._migrate_legacy_public_tables()

    def _migrate_legacy_public_tables(self) -> None:
        """One-time copy from old public-schema tables to kirana_oltp equivalents.
        Identifies users by username to ensure credentials (salt/hash) are propagated.
        """
        public = sa_inspect(self._engine).get_table_names(schema="public")

        with self._conn() as conn:
            if "kirana_app_users" in public:
                # Upsert by username. We don't force user_id here to avoid PK conflicts
                # with existing rows that might have different usernames.
                conn.execute(text("""
                    INSERT INTO kirana_oltp.users
                        (username, email, full_name, role, store_id,
                         password_salt, password_hash, is_active)
                    SELECT
                        a.username,
                        CASE WHEN a.username LIKE '%@%' THEN a.username ELSE NULL END,
                        COALESCE(NULLIF(a.full_name, ''), a.username),
                        a.role,
                        a.store_id,
                        a.password_salt,
                        a.password_hash,
                        a.is_active
                    FROM public.kirana_app_users a
                    WHERE a.store_id IS NULL OR a.store_id IN (SELECT store_id FROM kirana_oltp.store)
                    ON CONFLICT (username) DO UPDATE SET
                        password_salt = EXCLUDED.password_salt,
                        password_hash = EXCLUDED.password_hash,
                        full_name     = EXCLUDED.full_name,
                        role          = EXCLUDED.role,
                        store_id      = EXCLUDED.store_id,
                        is_active     = EXCLUDED.is_active
                """))
                # Sync sequence
                conn.execute(text(
                    "SELECT setval(pg_get_serial_sequence('kirana_oltp.users','user_id'),"
                    " (SELECT COALESCE(MAX(user_id), 1) FROM kirana_oltp.users))"
                ))

            if "kirana_user_sessions" in public:
                # Map old sessions to new user_ids via username
                conn.execute(text("""
                    INSERT INTO kirana_oltp.user_sessions
                        (user_id, access_token, created_at, revoked_at)
                    SELECT u_new.user_id, s.access_token, s.created_at, s.revoked_at
                    FROM public.kirana_user_sessions s
                    JOIN public.kirana_app_users a ON s.user_id = a.user_id
                    JOIN kirana_oltp.users u_new ON a.username = u_new.username
                    ON CONFLICT (access_token) DO NOTHING
                """))
                conn.execute(text(
                    "SELECT setval(pg_get_serial_sequence('kirana_oltp.user_sessions','session_id'),"
                    " (SELECT COALESCE(MAX(session_id), 1) FROM kirana_oltp.user_sessions))"
                ))

            if "kirana_stores" in public:
                conn.execute(text("""
                    UPDATE kirana_oltp.store s
                    SET store_type   = COALESCE(s.store_type,   ks.store_type),
                        footfall     = COALESCE(s.footfall,     ks.footfall),
                        budget       = COALESCE(s.budget,       ks.budget),
                        daily_budget = COALESCE(s.daily_budget, ks.daily_budget)
                    FROM public.kirana_stores ks
                    WHERE ks.store_id = s.store_id
                """))

            # Seed deterministic footfall/budget for stores that still have nulls
            # (seed-data rows have no footfall/budget from the original schema).
            conn.execute(text("""
                UPDATE kirana_oltp.store
                SET footfall     = COALESCE(footfall,     80 + (store_id * 17) % 80),
                    budget       = COALESCE(budget,       100000 + (store_id * 7000) % 50000),
                    daily_budget = COALESCE(daily_budget, 4000 + (store_id * 200) % 2000),
                    store_type   = COALESCE(store_type,   'kirana')
                WHERE COALESCE(is_deleted, FALSE) = FALSE
                  AND (footfall IS NULL OR budget IS NULL)
            """))
            conn.execute(text(
                "SELECT setval(pg_get_serial_sequence('kirana_oltp.store','store_id'),"
                " (SELECT COALESCE(MAX(store_id), 1) FROM kirana_oltp.store))"
            ))

            if "kirana_inventory_snapshots" in public:
                try:
                    conn.execute(text("""
                        INSERT INTO kirana_oltp.inventory_snapshots
                            (snapshot_date, store_id, product_id, units_sold, stock,
                             revenue, profit, price, promo_flag)
                        SELECT snapshot_date, store_id, sku_id, units_sold, stock,
                               revenue, profit, price, promo_flag
                        FROM kirana_inventory_snapshots
                        ON CONFLICT DO NOTHING
                    """))
                except Exception as exc:
                    logger.warning("inventory_snapshots migration skipped: %s", exc)
                    conn.rollback()

            if "kirana_user_prefs" in public:
                conn.execute(text("""
                    INSERT INTO kirana_oltp.user_prefs
                        (user_id, forecast_horizon_days, alert_stockout_threshold,
                         alert_min_velocity, alert_reorder_days, alert_dead_stock_days,
                         notify_whatsapp, notify_in_app, quiet_hours_start,
                         quiet_hours_end, updated_at)
                    SELECT user_id, forecast_horizon_days, alert_stockout_threshold,
                           alert_min_velocity, alert_reorder_days, alert_dead_stock_days,
                           notify_whatsapp, notify_in_app, quiet_hours_start,
                           quiet_hours_end, updated_at
                    FROM kirana_user_prefs
                    WHERE user_id IN (SELECT user_id FROM kirana_oltp.users)
                    ON CONFLICT DO NOTHING
                """))

            conn.commit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _default_email(username: str) -> str | None:
        uname = (username or "").strip()
        return uname if "@" in uname else None

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.sha256((salt + password).encode()).hexdigest()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def get_password_status(self, user_id: int) -> dict:
        from datetime import datetime, timezone
        sql = """
        SELECT password_changed_at
        FROM kirana_oltp.users
        WHERE user_id = :uid AND COALESCE(is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        if not row:
            return {"has_password": False, "last_changed_at": None, "can_change": True}
        last_changed = row["password_changed_at"]
        has_password = last_changed is not None
        can_change = True
        days_left = 0
        if last_changed:
            last_changed_utc = last_changed.replace(tzinfo=timezone.utc) if last_changed.tzinfo is None else last_changed.astimezone(timezone.utc)
            days_since = (datetime.now(timezone.utc) - last_changed_utc).days
            can_change = days_since >= 14
            days_left = max(0, 14 - days_since)
        return {
            "has_password": has_password,
            "last_changed_at": last_changed.isoformat() if last_changed else None,
            "can_change": can_change,
            "days_until_change": days_left,
        }

    def change_password(self, user_id: int, old_password: str | None, new_password: str) -> None:
        from datetime import datetime, timezone
        sql = """
        SELECT password_hash, password_salt, password_changed_at
        FROM kirana_oltp.users
        WHERE user_id = :uid AND COALESCE(is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        if not row:
            raise ValueError("User not found")
        has_password = row["password_changed_at"] is not None
        # Cooldown check
        if row["password_changed_at"]:
            last_changed = row["password_changed_at"]
            last_changed_utc = last_changed.replace(tzinfo=timezone.utc) if last_changed.tzinfo is None else last_changed.astimezone(timezone.utc)
            days_since = (datetime.now(timezone.utc) - last_changed_utc).days
            if days_since < 14:
                days_left = 14 - days_since
                raise ValueError(f"Password can only be changed once every 14 days. Try again in {days_left} day(s).")
        # Verify old password when user already has one
        if has_password:
            if not old_password:
                raise ValueError("Current password is required")
            if not secrets.compare_digest(
                self._hash(old_password, row["password_salt"] or ""),
                row["password_hash"] or "",
            ):
                raise ValueError("Current password is incorrect")
        if len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters")
        salt = secrets.token_hex(16)
        ph = self._hash(new_password, salt)
        with self._conn() as conn:
            conn.execute(text("""
            UPDATE kirana_oltp.users
            SET password_hash = :ph, password_salt = :salt, password_changed_at = NOW()
            WHERE user_id = :uid
            """), {"ph": ph, "salt": salt, "uid": user_id})
            conn.commit()

    def authenticate_user(self, username: str, password: str) -> dict | None:
        sql = """
        SELECT user_id, username, full_name, role, store_id, password_salt, password_hash
        FROM kirana_oltp.users
        WHERE username = :u AND is_active = TRUE AND COALESCE(is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"u": username}).mappings().first()
        if not row:
            return None
        if not secrets.compare_digest(self._hash(password, row["password_salt"] or ""), row["password_hash"] or ""):
            return None
        return {"user_id": row["user_id"], "username": row["username"],
                "full_name": row["full_name"], "role": row["role"], "store_id": row["store_id"]}

    def authenticate_by_phone(self, phone_number: str, firebase_uid: str | None = None) -> dict | None:
        """Look up an active user by phone number or firebase_uid (Firebase already verified the OTP)."""
        sql = """
        SELECT user_id, username, full_name, role, store_id
        FROM kirana_oltp.users
        WHERE (phone_number = :phone OR (:fuid IS NOT NULL AND firebase_uid = :fuid))
          AND is_active = TRUE AND COALESCE(is_deleted, FALSE) = FALSE
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"phone": phone_number, "fuid": firebase_uid}).mappings().first()
        return dict(row) if row else None

    def check_username_available(self, username: str) -> bool:
        sql = "SELECT 1 FROM kirana_oltp.users WHERE LOWER(username) = LOWER(:u)"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"u": username}).first()
        return row is None

    def create_session(self, user_id: int, login_method: str = "password") -> str:
        token = secrets.token_hex(32)
        sql = """
            INSERT INTO kirana_oltp.user_sessions(user_id, access_token, created_at, login_method)
            VALUES(:uid, :tok, NOW(), :method)
        """
        with self._conn() as conn:
            conn.execute(text(sql), {"uid": user_id, "tok": token, "method": login_method})
            conn.commit()
        return token

    def get_user_by_token(self, token: str) -> dict | None:
        sql = """
        SELECT u.user_id, u.username, u.full_name, u.role, u.store_id
        FROM kirana_oltp.user_sessions s
        JOIN kirana_oltp.users u ON s.user_id = u.user_id
        WHERE s.access_token = :tok
          AND s.revoked_at IS NULL
          AND s.created_at > NOW() - INTERVAL '30 days'
          AND u.is_active = TRUE
          AND COALESCE(u.is_deleted, FALSE) = FALSE
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"tok": token}).mappings().first()
        return dict(row) if row else None

    # ── User CRUD ─────────────────────────────────────────────────────────────

    def create_user(self, username: str, password: str, full_name: str,
                    role: str, store_id: int | None) -> dict:
        salt = secrets.token_hex(16)
        ph   = self._hash(password, salt)
        sql  = """
        INSERT INTO kirana_oltp.users
            (username, email, full_name, role, store_id, password_salt, password_hash, is_active)
        VALUES(:u, :email, :fn, :r, :sid, :salt, :ph, TRUE)
        RETURNING user_id, username, full_name, role, store_id
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {
                "u": username, "email": self._default_email(username),
                "fn": full_name, "r": role, "sid": store_id, "salt": salt, "ph": ph,
            }).mappings().first()
            conn.commit()
        return dict(row)

    def get_user_by_username(self, username: str) -> dict | None:
        sql = """
        SELECT user_id, username, full_name, role, store_id, is_active
        FROM kirana_oltp.users
        WHERE username = :username AND is_active = TRUE
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"username": username}).mappings().first()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        sql = """
        SELECT user_id, username, full_name, role, store_id, is_active
        FROM kirana_oltp.users
        ORDER BY user_id
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]

    def delete_user(self, user_id: int) -> bool:
        sql = "UPDATE kirana_oltp.users SET is_active = FALSE WHERE user_id = :uid RETURNING user_id"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).first()
            conn.commit()
        return row is not None

    def update_user_profile(self, user_id: int, full_name: str | None, password: str | None) -> dict | None:
        sets, params = [], {"uid": user_id}
        if full_name:
            sets.append("full_name = :fn")
            params["fn"] = full_name
        if password:
            salt = secrets.token_hex(16)
            params.update({"salt": salt, "ph": self._hash(password, salt)})
            sets += ["password_salt = :salt", "password_hash = :ph"]
        if not sets:
            return None
        sql = (f"UPDATE kirana_oltp.users SET {', '.join(sets)} WHERE user_id = :uid "
               f"RETURNING user_id, username, full_name, role, store_id")
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        return dict(row) if row else None

    # ── Atomic registration ───────────────────────────────────────────────────

    def register_store_owner_atomic(
        self,
        store_name: str,
        store_type: str,
        footfall: int,
        location: str | None,
        region: str | None,
        username: str,
        password: str,
        full_name: str,
        email: str | None = None,
        phone_number: str | None = None,
        firebase_uid: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> tuple[dict, dict]:
        """Create store + user in one transaction. Any failure rolls back both.
        For phone-auth users, password may be empty; a random credential is stored."""
        # For phone-auth users with no password, generate a random internal credential
        if password:
            salt = secrets.token_hex(16)
            ph   = self._hash(password, salt)
        else:
            salt = secrets.token_hex(16)
            ph   = self._hash(secrets.token_hex(32), salt)   # random, unrecoverable

        with self._conn() as conn:
            # 1. kirana_oltp.store (now holds all metadata)
            store_row = conn.execute(text("""
                INSERT INTO kirana_oltp.store(name, location, region, store_type, footfall, latitude, longitude)
                VALUES(:sn, :location, :region, :st, :fp, :lat, :lng)
                RETURNING store_id, name, location, region, store_type, footfall, latitude, longitude
            """), {"sn": store_name, "location": location, "region": region,
                   "st": store_type, "fp": footfall,
                   "lat": latitude, "lng": longitude}).mappings().first()
            store_id = store_row["store_id"]

            # 2. kirana_oltp.users — UNIQUE(username) violation rolls back the store too
            user_row = conn.execute(text("""
                INSERT INTO kirana_oltp.users
                    (username, email, full_name, role, store_id,
                     password_salt, password_hash, is_active, phone_number, firebase_uid)
                VALUES(:u, :email, :fn, 'store_owner', :sid, :salt, :ph, TRUE, :phone, :fbuid)
                RETURNING user_id, username, full_name, role, store_id
            """), {"u": username, "email": email or self._default_email(username), "fn": full_name,
                   "sid": store_id, "salt": salt, "ph": ph,
                   "phone": phone_number, "fbuid": firebase_uid}).mappings().first()

            # Advance sequence so auto-inserts never collide with the explicit id
            conn.execute(text(
                "SELECT setval(pg_get_serial_sequence('kirana_oltp.users','user_id'),"
                " (SELECT COALESCE(MAX(user_id), 1) FROM kirana_oltp.users))"
            ))

            conn.commit()

        store = {**dict(store_row), "store_name": store_row["name"]}
        return store, dict(user_row)

    # ── Store CRUD ────────────────────────────────────────────────────────────

    def create_store(self, store_name: str, store_type: str, footfall: int,
                     location: str | None = None, region: str | None = None) -> dict:
        sql = """
        INSERT INTO kirana_oltp.store(name, location, region, store_type, footfall)
        VALUES(:sn, :location, :region, :st, :fp)
        RETURNING store_id, name, store_type, footfall
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {
                "sn": store_name, "location": location, "region": region,
                "st": store_type, "fp": footfall,
            }).mappings().first()
            conn.commit()
        return {**dict(row), "store_name": row["name"], "oltp_store_name": row["name"]}

    def list_store_master(self) -> list[dict]:
        sql = """
        SELECT
            store_id,
            name                              AS store_name,
            COALESCE(store_type, 'kirana')    AS store_type,
            COALESCE(footfall, 0)             AS footfall,
            budget,
            daily_budget,
            location,
            region,
            (SELECT COUNT(DISTINCT product_id)
             FROM kirana_oltp.inventory
             WHERE store_id = s.store_id)     AS sku_count
        FROM kirana_oltp.store s
        WHERE COALESCE(is_deleted, FALSE) = FALSE
        ORDER BY store_id
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]

    def get_store(self, store_id: int) -> dict:
        sql = "SELECT *, name AS store_name FROM kirana_oltp.store WHERE store_id = :sid"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        if not row:
            raise ValueError("Store not found")
        return dict(row)

    def update_store(self, store_id: int, **kwargs) -> dict | None:
        field_map = {
            "store_name": "name", 
            "store_type": "store_type",
            "footfall": "footfall", 
            "budget": "budget", 
            "daily_budget": "daily_budget",
            "location": "location",
            "region": "region",
        }
        sets, params = [], {"sid": store_id}
        for k, v in kwargs.items():
            if v is not None and k in field_map:
                col = field_map[k]
                sets.append(f"{col} = :{k}")
                params[k] = v
        if not sets:
            return None
        sql = (f"UPDATE kirana_oltp.store SET {', '.join(sets)} WHERE store_id = :sid "
               f"RETURNING store_id, name AS store_name, store_type, footfall, budget, daily_budget, location, region")
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        return dict(row) if row else None

    def compute_store_footfall(self, store_id: int) -> int:
        """
        Compute average daily footfall based on order volume.
        Logic: Average daily orders in last 30 days * 1.2 multiplier (for non-buying visitors).
        """
        sql = """
        SELECT COUNT(order_id)::float / 30.0 as avg_orders
        FROM kirana_oltp.orders
        WHERE store_id = :sid 
          AND order_date > NOW() - INTERVAL '30 days'
        """
        with self._conn() as conn:
            res = conn.execute(text(sql), {"sid": store_id}).mappings().first()
            avg_orders = float(res["avg_orders"] or 0)
            
            # Heuristic: 20% of people don't buy anything
            new_footfall = int(max(avg_orders * 1.2, 10)) # Minimum 10
            
            # Update store table
            conn.execute(text("UPDATE kirana_oltp.store SET footfall = :f WHERE store_id = :sid"),
                         {"f": new_footfall, "sid": store_id})
            conn.commit()
            
        return new_footfall

    # ── Inventory Snapshots ───────────────────────────────────────────────────

    def upsert_inventory_snapshot(self, store_id: int, snapshot_date: str, items: list[dict]) -> int:
        sql = """
        INSERT INTO kirana_oltp.inventory_snapshots
            (snapshot_date, store_id, product_id, units_sold, stock, revenue, profit, price, promo_flag)
        VALUES
            (:d, :sid, :skuid, :us, :st, :rev, :prof, :price, :pf)
        ON CONFLICT (snapshot_date, store_id, product_id)
        DO UPDATE SET
            units_sold  = EXCLUDED.units_sold,
            stock       = EXCLUDED.stock,
            revenue     = EXCLUDED.revenue,
            profit      = EXCLUDED.profit,
            price       = EXCLUDED.price,
            promo_flag  = EXCLUDED.promo_flag,
            upserted_at = NOW()
        """
        if not items:
            return 0
        params = [
            {
                "d": snapshot_date, "sid": store_id,
                "skuid": item.get("sku_id"),   "us": item.get("units_sold"),
                "st": item.get("stock"),        "rev": item.get("revenue"),
                "prof": item.get("profit"),     "price": item.get("price"),
                "pf": item.get("promo_flag"),
            }
            for item in items
        ]
        with self._conn() as conn:
            conn.execute(text(sql), params)
            conn.commit()
        return len(params)

    def get_store_snapshot(self, store_id: int) -> dict:
        latest_sql = """
        SELECT MAX(snapshot_date)::text AS snapshot_date
        FROM kirana_oltp.inventory_snapshots
        WHERE store_id = :sid
        """
        latest_rows_sql = """
        SELECT
            s.product_id AS sku_id,
            s.snapshot_date::text AS snapshot_date,
            s.units_sold,
            s.stock,
            NULL::numeric AS lost_sales,
            s.revenue,
            s.profit,
            s.price,
            CASE WHEN s.promo_flag IS TRUE THEN 1 WHEN s.promo_flag IS FALSE THEN 0 ELSE NULL END AS promo_flag,
            c.name AS category,
            p.name AS product_name
        FROM kirana_oltp.inventory_snapshots s
        LEFT JOIN kirana_oltp.product p ON p.product_id = s.product_id
        LEFT JOIN kirana_oltp.category c ON c.category_id = p.category_id
        WHERE s.store_id = :sid AND s.snapshot_date = CAST(:snap_date AS date)
        ORDER BY s.product_id
        """
        fallback_sql = """
        SELECT
            i.product_id AS sku_id,
            CURRENT_DATE::text AS snapshot_date,
            NULL::numeric AS units_sold,
            i.quantity::numeric AS stock,
            NULL::numeric AS lost_sales,
            NULL::numeric AS revenue,
            NULL::numeric AS profit,
            pr.price,
            NULL::int AS promo_flag,
            c.name AS category,
            p.name AS product_name
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p ON p.product_id = i.product_id
        LEFT JOIN kirana_oltp.category c ON c.category_id = p.category_id
        LEFT JOIN LATERAL (
            SELECT price FROM kirana_oltp.pricing pr
            WHERE pr.product_id = i.product_id AND pr.store_id = i.store_id
              AND pr.valid_from <= NOW() AND pr.valid_to >= NOW()
            ORDER BY pr.valid_from DESC LIMIT 1
        ) pr ON TRUE
        WHERE i.store_id = :sid
        ORDER BY i.product_id
        """
        today_sales_sql = """
        SELECT oi.product_id, SUM(oi.quantity) as sold_today
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid AND DATE(o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE AT TIME ZONE 'Asia/Kolkata'
        GROUP BY oi.product_id
        """
        with self._conn() as conn:
            sales_rows = conn.execute(text(today_sales_sql), {"sid": store_id}).mappings().all()
            sales_map = {r["product_id"]: int(r["sold_today"]) for r in sales_rows}
            
            latest = conn.execute(text(latest_sql), {"sid": store_id}).scalar()
            if latest:
                rows = conn.execute(text(latest_rows_sql),
                                    {"sid": store_id, "snap_date": latest}).mappings().all()
                items = [dict(r) for r in rows]
                for item in items:
                    item["units_sold"] = sales_map.get(item["sku_id"], 0)
                return {"store_id": store_id, "snapshot_count": len(rows),
                        "snapshot_date": latest, "items": items}
            
            rows = conn.execute(text(fallback_sql), {"sid": store_id}).mappings().all()
            items = [dict(r) for r in rows]
            for item in items:
                item["units_sold"] = sales_map.get(item["sku_id"], 0)
            return {"store_id": store_id, "snapshot_count": len(rows),
                    "snapshot_date": rows[0]["snapshot_date"] if rows else None,
                    "items": items}

    # ── AI Usage & Credits ────────────────────────────────────────────────────

    _AI_DAILY_LIMITS: dict[str, int] = {
        "voice":     3,
        "handwrite": 5,
        "invoice":   2,
    }

    def check_and_record_ai_use(self, user_id: int, feature: str) -> None:
        """
        Atomically checks whether the user may use this AI feature and records
        one use.  Raises HTTPException 429 when the daily quota is exhausted
        AND no credits remain.
        """
        import datetime
        today      = datetime.date.today().isoformat()
        daily_lim  = self._AI_DAILY_LIMITS.get(feature, 0)

        with self._conn() as conn:
            # Ensure a today-row exists, then lock it
            conn.execute(text("""
                INSERT INTO kirana_oltp.ai_usage (user_id, feature, usage_date, count)
                VALUES (:uid, :feat, :today, 0)
                ON CONFLICT (user_id, feature, usage_date) DO NOTHING
            """), {"uid": user_id, "feat": feature, "today": today})

            used = conn.execute(text("""
                SELECT count FROM kirana_oltp.ai_usage
                WHERE user_id = :uid AND feature = :feat AND usage_date = :today
                FOR UPDATE
            """), {"uid": user_id, "feat": feature, "today": today}).scalar() or 0

            if used < daily_lim:
                conn.execute(text("""
                    UPDATE kirana_oltp.ai_usage
                    SET count = count + 1
                    WHERE user_id = :uid AND feature = :feat AND usage_date = :today
                """), {"uid": user_id, "feat": feature, "today": today})
            else:
                # Try credits — lock the row first
                conn.execute(text("""
                    INSERT INTO kirana_oltp.ai_credits (user_id, feature, balance)
                    VALUES (:uid, :feat, 0)
                    ON CONFLICT (user_id, feature) DO NOTHING
                """), {"uid": user_id, "feat": feature})

                balance = conn.execute(text("""
                    SELECT balance FROM kirana_oltp.ai_credits
                    WHERE user_id = :uid AND feature = :feat
                    FOR UPDATE
                """), {"uid": user_id, "feat": feature}).scalar() or 0

                if balance <= 0:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Daily limit reached for {feature}. Purchase credits to continue.",
                    )
                conn.execute(text("""
                    UPDATE kirana_oltp.ai_credits
                    SET balance = balance - 1
                    WHERE user_id = :uid AND feature = :feat
                """), {"uid": user_id, "feat": feature})

            conn.commit()

    def get_ai_status(self, user_id: int) -> dict:
        """Return current usage + credits for all AI features."""
        import datetime
        today = datetime.date.today().isoformat()

        with self._conn() as conn:
            usage_rows = conn.execute(text("""
                SELECT feature, count FROM kirana_oltp.ai_usage
                WHERE user_id = :uid AND usage_date = :today
            """), {"uid": user_id, "today": today}).mappings().all()

            credit_rows = conn.execute(text("""
                SELECT feature, balance FROM kirana_oltp.ai_credits
                WHERE user_id = :uid
            """), {"uid": user_id}).mappings().all()

        used_map    = {r["feature"]: r["count"]   for r in usage_rows}
        credits_map = {r["feature"]: r["balance"] for r in credit_rows}

        result = {}
        for feat, lim in self._AI_DAILY_LIMITS.items():
            used      = used_map.get(feat, 0)
            credits   = credits_map.get(feat, 0)
            free_left = max(0, lim - used)
            remaining = free_left if free_left > 0 else credits
            result[feat] = {
                "used":      used,
                "limit":     lim,
                "credits":   credits,
                "remaining": remaining,
            }
        return result

    def add_ai_credits(self, user_id: int, feature: str, count: int) -> dict:
        """Add purchased credits for a feature and return updated status."""
        with self._conn() as conn:
            conn.execute(text("""
                INSERT INTO kirana_oltp.ai_credits (user_id, feature, balance)
                VALUES (:uid, :feat, :count)
                ON CONFLICT (user_id, feature)
                DO UPDATE SET balance = ai_credits.balance + :count
            """), {"uid": user_id, "feat": feature, "count": count})
            conn.commit()
        return self.get_ai_status(user_id)

    # ── User preferences ──────────────────────────────────────────────────────

    _PREF_DEFAULTS = {
        "forecast_horizon_days":    7,
        "alert_stockout_threshold": 0.5,
        "alert_min_velocity":       0.3,
        "alert_reorder_days":       3,
        "alert_dead_stock_days":    21,
        "notify_whatsapp":          False,
        "notify_in_app":            True,
        "quiet_hours_start":        22,
        "quiet_hours_end":          7,
        "subscribed_kpis":          None,
    }

    def get_user_prefs(self, user_id: int) -> dict:
        sql = "SELECT * FROM kirana_oltp.user_prefs WHERE user_id = :uid"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        return dict(row) if row else {**self._PREF_DEFAULTS, "user_id": user_id}

    def upsert_user_prefs(self, user_id: int, **fields) -> dict:
        clean = {k: v for k, v in fields.items() if k in self._PREF_DEFAULTS and v is not None}
        if not clean:
            return self.get_user_prefs(user_id)
        merged = {**self._PREF_DEFAULTS, **clean}
        cols = list(merged.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        update_set   = ", ".join(f"{c} = EXCLUDED.{c}" for c in clean)
        sql = (f"INSERT INTO kirana_oltp.user_prefs(user_id, {', '.join(cols)}, updated_at) "
               f"VALUES(:uid, {placeholders}, NOW()) "
               f"ON CONFLICT (user_id) DO UPDATE SET {update_set}, updated_at = NOW() "
               f"RETURNING *")
        with self._conn() as conn:
            row = conn.execute(text(sql), {"uid": user_id, **merged}).mappings().first()
            conn.commit()
        return dict(row)

    # ── Customer Segments ─────────────────────────────────────────────────────

    def list_customers_with_segments(self, store_id: int) -> list[dict]:
        sql = """
        SELECT
            c.customer_id,
            c.name,
            c.phone,
            c.email,
            c.household_size,
            c.store_id,
            c.created_at,
            c.association_id,
            COALESCE(ord.total_orders, 0)   AS total_orders,
            COALESCE(ord.total_spent, 0)    AS total_spent,
            ord.last_order_date,
            COALESCE(ord.orders_30d, 0)     AS orders_30d,
            COALESCE(ord.orders_90d, 0)     AS orders_90d,
            COALESCE(kh.balance, 0)         AS balance
        FROM kirana_oltp.customer c
        LEFT JOIN (
            SELECT
                o.customer_id,
                COUNT(*)                                                                     AS total_orders,
                SUM(o.total_amount)                                                          AS total_spent,
                MAX(o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')            AS last_order_date,
                COUNT(CASE WHEN o.order_date >= NOW() - INTERVAL '30 days' THEN 1 END)      AS orders_30d,
                COUNT(CASE WHEN o.order_date >= NOW() - INTERVAL '90 days' THEN 1 END)      AS orders_90d
            FROM kirana_oltp.orders o
            WHERE o.store_id = :sid AND o.customer_id IS NOT NULL
            GROUP BY o.customer_id
        ) ord ON ord.customer_id = c.customer_id
        LEFT JOIN (
            SELECT customer_id, SUM(amount) AS balance
            FROM kirana_oltp.khata
            WHERE store_id = :sid
            GROUP BY customer_id
        ) kh ON kh.customer_id = c.customer_id
        WHERE c.store_id = :sid
        ORDER BY c.name ASC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("last_order_date"):
                d["last_order_date"] = d["last_order_date"].isoformat()
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result

    # ── Subscription ──────────────────────────────────────────────────────────

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
                    conn.execute(text(
                        "UPDATE kirana_oltp.subscription SET requested_tier = :rt "
                        "WHERE store_id = :sid AND tier = 'pending_trial'"
                    ), {"rt": requested_tier, "sid": store_id})
                    conn.commit()
                existing["requested_tier"] = requested_tier
            return existing

        # No active subscription (first-time or previously cancelled).
        # Try to UPDATE the most recent cancelled row back to pending_trial.
        # If no row exists at all, INSERT a fresh one.
        with self._conn() as conn:
            updated = conn.execute(text("""
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
            """), {"sid": store_id, "rt": requested_tier}).mappings().first()

            if updated:
                row = updated
            else:
                row = conn.execute(text("""
                    INSERT INTO kirana_oltp.subscription
                        (store_id, tier, monthly_price, started_at, is_trial, requested_tier)
                    VALUES (:sid, 'pending_trial', 0, NOW(), TRUE, :rt)
                    RETURNING *
                """), {"sid": store_id, "rt": requested_tier}).mappings().first()
            conn.commit()
        d = dict(row)
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"): d["ended_at"] = d["ended_at"].isoformat()
        d["days_remaining"] = 0
        d["seconds_remaining"] = 0
        return d

    def approve_trial(self, store_id: int, trial_days: int) -> dict:
        """Promote pending_trial → trial, preserving the requested tier."""
        from datetime import datetime, timedelta
        trial_ends_at = datetime.now() + timedelta(days=trial_days)
        # Read requested_tier before updating
        with self._conn() as conn:
            pending = conn.execute(text(
                "SELECT requested_tier FROM kirana_oltp.subscription WHERE store_id = :sid AND tier = 'pending_trial'"
            ), {"sid": store_id}).mappings().first()
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
            row = conn.execute(text(sql), {"sid": store_id, "te": trial_ends_at, "tt": trial_tier}).mappings().first()
            conn.commit()
        if not row:
            raise ValueError(f"No pending trial found for store {store_id}")
        d = dict(row)
        d["days_remaining"] = trial_days
        d["seconds_remaining"] = int(trial_days * 86400)
        d["trial_ends_at"] = d["trial_ends_at"].isoformat()
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"): d["ended_at"] = d["ended_at"].isoformat()
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
        if d.get("started_at"): d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"): d["ended_at"] = d["ended_at"].isoformat()
        if d.get("trial_ends_at"): d["trial_ends_at"] = d["trial_ends_at"].isoformat()
        return d

    def upgrade_subscription(self, store_id: int, tier: str) -> dict:
        prices = {"basic": 200, "pro": 500}
        if tier not in prices:
            raise ValueError(f"Invalid tier: {tier}")
        with self._conn() as conn:
            conn.execute(text("""
                UPDATE kirana_oltp.subscription
                SET ended_at = NOW()
                WHERE store_id = :sid AND (ended_at IS NULL OR ended_at > NOW())
            """), {"sid": store_id})
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.subscription
                    (store_id, tier, monthly_price, started_at, is_trial)
                VALUES (:sid, :tier, :price, NOW(), FALSE)
                RETURNING *
            """), {"sid": store_id, "tier": tier, "price": prices[tier]}).mappings().first()
            conn.commit()
        d = dict(row)
        d["started_at"] = d["started_at"].isoformat()
        if d.get("ended_at"): d["ended_at"] = d["ended_at"].isoformat()
        d["days_remaining"] = 0
        d["seconds_remaining"] = 0
        return d

    def create_razorpay_order(self, store_id: int, tier: str, key_id: str, key_secret: str) -> dict:
        """Call Razorpay API to create a payment order. Returns order details."""
        import requests as req_lib
        prices = {"basic": 200, "pro": 500}
        if tier not in prices:
            raise ValueError(f"Invalid tier: {tier}")
        amount_paise = prices[tier] * 100  # Razorpay uses paise
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

    def verify_razorpay_payment(self, store_id: int, tier: str,
                                 razorpay_order_id: str, razorpay_payment_id: str,
                                 razorpay_signature: str, key_secret: str) -> dict:
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

    # ── Finance ───────────────────────────────────────────────────────────────

    def get_today_items_sold(self, store_id: int) -> int:
        """Total units sold today (IST) for the given store."""
        sql = """
        SELECT COALESCE(SUM(oi.quantity), 0) AS items_sold
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid
          AND DATE(o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')
              = CURRENT_DATE AT TIME ZONE 'Asia/Kolkata'
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).first()
        return int(row[0]) if row and row[0] else 0

    def get_finance_overview(self, store_id: int) -> dict:
        sales_sql = """
        SELECT
            COALESCE(SUM(total_amount), 0) AS amount
        FROM kirana_oltp.orders
        WHERE store_id = :sid
          AND DATE_TRUNC('month', order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') = 
              DATE_TRUNC('month', CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')
        """
        sku_count_sql = """
        SELECT
            COUNT(DISTINCT product_id) AS sku_count
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid
          AND DATE_TRUNC('month', o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') = 
              DATE_TRUNC('month', CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')
        """
        udhaar_sql = """
        SELECT
            COALESCE(SUM(amount - amount_paid), 0) AS total_pending,
            COALESCE(SUM(amount_paid), 0)          AS total_recovered,
            COUNT(DISTINCT customer_id)            AS customer_count
        FROM kirana_oltp.khata
        WHERE store_id = :sid
        """
        with self._conn() as conn:
            sales = conn.execute(text(sales_sql), {"sid": store_id}).mappings().first()
            skus  = conn.execute(text(sku_count_sql), {"sid": store_id}).mappings().first()
            udhaar = conn.execute(text(udhaar_sql), {"sid": store_id}).mappings().first()
        
        return {
            "monthly_sales": {
                "amount": float(sales["amount"]),
                "sku_count": int(skus["sku_count"])
            },
            "udhaar_stats": {
                "total_pending": float(udhaar["total_pending"]),
                "total_recovered": float(udhaar["total_recovered"]),
                "customer_count": int(udhaar["customer_count"])
            }
        }

    def get_udhaar_list(self, store_id: int, include_recovered: bool = False) -> list[dict]:
        sql = """
        SELECT
            k.khata_id,
            k.customer_id,
            c.name AS customer_name,
            c.phone,
            (k.amount - k.amount_paid) AS balance,
            k.issue_date::text AS date_taken,
            (CURRENT_DATE - k.issue_date) AS days_pending
        FROM kirana_oltp.khata k
        JOIN kirana_oltp.customer c ON k.customer_id = c.customer_id
        WHERE k.store_id = :sid
        """
        if not include_recovered:
            sql += " AND k.status IN ('open', 'overdue', 'pending')"
        else:
            sql += " AND k.status != 'written_off'"
        
        sql += " ORDER BY k.issue_date DESC"
        
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def record_udhaar_recovery(self, store_id: int, khata_id: int, recovery_amount: float) -> dict:
        # 1. Fetch current record
        sql_fetch = "SELECT amount, amount_paid FROM kirana_oltp.khata WHERE khata_id = :kid AND store_id = :sid"
        with self._conn() as conn:
            row = conn.execute(text(sql_fetch), {"kid": khata_id, "sid": store_id}).mappings().first()
            if not row:
                raise ValueError("Udhaar record not found")
            
            new_paid = float(row["amount_paid"]) + recovery_amount
            status = 'settled' if new_paid >= float(row["amount"]) else 'open'
            
            sql_update = """
            UPDATE kirana_oltp.khata
            SET amount_paid = :p, status = :s
            WHERE khata_id = :kid AND store_id = :sid
            """
            conn.execute(text(sql_update), {"p": new_paid, "s": status, "kid": khata_id, "sid": store_id})
            conn.execute(text("""
                INSERT INTO kirana_oltp.khata_payments(khata_id, store_id, amount, paid_at)
                VALUES (:kid, :sid, :amt, NOW())
            """), {"kid": khata_id, "sid": store_id, "amt": recovery_amount})
            conn.commit()

            # 2. Return the updated record with customer info
            sql_final = """
            SELECT
                k.khata_id,
                k.customer_id,
                c.name AS customer_name,
                c.phone,
                (k.amount - k.amount_paid) AS balance,
                k.issue_date::text AS date_taken,
                (CURRENT_DATE - k.issue_date) AS days_pending
            FROM kirana_oltp.khata k
            JOIN kirana_oltp.customer c ON k.customer_id = c.customer_id
            WHERE k.khata_id = :kid
            """
            result = conn.execute(text(sql_final), {"kid": khata_id}).mappings().first()
            
        return dict(result)

    def get_khata_payments(self, store_id: int, khata_id: int) -> list[dict]:
        sql = """
            SELECT payment_id, amount, paid_at::text AS paid_at, notes
            FROM kirana_oltp.khata_payments
            WHERE khata_id = :kid AND store_id = :sid
            ORDER BY paid_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"kid": khata_id, "sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def add_udhaar(self, store_id: int, customer_name: str, phone: str, amount: float) -> dict:
        with self._conn() as conn:
            # 1. Find or create customer (scoped to store_id)
            cust_sql = "SELECT customer_id FROM kirana_oltp.customer WHERE phone = :p AND store_id = :sid"
            cust_row = conn.execute(text(cust_sql), {"p": phone, "sid": store_id}).mappings().first()
            
            if not cust_row:
                ins_cust = "INSERT INTO kirana_oltp.customer(name, phone, store_id) VALUES(:n, :p, :sid) RETURNING customer_id"
                customer_id = conn.execute(text(ins_cust), {"n": customer_name, "p": phone, "sid": store_id}).scalar()
            else:
                customer_id = cust_row["customer_id"]
            
            # 2. Create khata entry
            # Note: Using 'pending' as status per request, though 'open' was the previous convention
            ins_khata = """
            INSERT INTO kirana_oltp.khata(customer_id, store_id, amount, amount_paid, issue_date, due_date, status)
            VALUES(:cid, :sid, :amt, 0, CURRENT_DATE, CURRENT_DATE + INTERVAL '30 days', 'pending')
            RETURNING khata_id, customer_id, amount, amount_paid, status, issue_date::text AS date_taken
            """
            khata = conn.execute(text(ins_khata), {
                "cid": customer_id, "sid": store_id, "amt": amount
            }).mappings().first()
            
            conn.commit()
            
        res = dict(khata)
        res.update({
            "customer_name": customer_name,
            "phone": phone,
            "balance": float(khata["amount"]) - float(khata["amount_paid"]),
        })
        return res

    def sync_customers(self, store_id: int, contacts: list[dict]) -> int:
        if not contacts:
            return 0
        insert_sql = """
        INSERT INTO kirana_oltp.customer (name, phone, store_id)
        SELECT :n, :p, :sid
        WHERE NOT EXISTS (
            SELECT 1 FROM kirana_oltp.customer
            WHERE store_id = :sid AND phone = :p
        )
        """
        params = [{"n": c["name"], "p": c["phone"], "sid": store_id} for c in contacts]
        with self._conn() as conn:
            conn.execute(text(insert_sql), params)
            conn.commit()
        return len(params)

    # ── Cashflow Requests ─────────────────────────────────────────────────────

    def create_cashflow_request(self, store_id: int, user_id: int,
                                amount: float, selected_bank: str | None) -> dict:
        store = self.get_store(store_id)
        sql = """
        INSERT INTO kirana_oltp.cashflow_requests
            (store_id, user_id, amount_requested, selected_bank,
             store_name, location, avg_footfall)
        VALUES (:sid, :uid, :amt, :bank, :sname, :loc, :ff)
        RETURNING request_id, status, created_at
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {
                "sid": store_id, "uid": user_id, "amt": amount,
                "bank": selected_bank,
                "sname": store.get("name"),
                "loc": store.get("location"),
                "ff": store.get("footfall"),
            }).mappings().first()
            conn.commit()
        return dict(row)

    def get_cashflow_status(self, store_id: int) -> dict:
        sql = """
        SELECT request_id, status, amount_requested, selected_bank, created_at
        FROM kirana_oltp.cashflow_requests
        WHERE store_id = :sid
        ORDER BY created_at DESC
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        if not row:
            return {"has_request": False}
        return {
            "has_request": True,
            "request_id": row["request_id"],
            "status": row["status"],
            "amount": float(row["amount_requested"]),
            "selected_bank": row["selected_bank"],
            "created_at": str(row["created_at"]),
        }

    # ── Referral System ────────────────────────────────────────────────────────────

    def create_referral_campaign(self, store_id: int, name: str,
                                  referral_discount_pct: float,
                                  milestone_every_n: int,
                                  milestone_reward_pct: float,
                                  max_referrals_per_referrer: int = 50) -> dict:
        sql = """
        INSERT INTO kirana_oltp.referral_campaigns
            (store_id, name, referral_discount_pct, milestone_every_n,
             milestone_reward_pct, max_referrals_per_referrer)
        VALUES (:sid, :name, :rdp, :men, :mrp, :maxr)
        RETURNING *
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {
                "sid": store_id, "name": name, "rdp": referral_discount_pct,
                "men": milestone_every_n, "mrp": milestone_reward_pct,
                "maxr": max_referrals_per_referrer,
            }).mappings().first()
            conn.commit()
        return dict(row)

    def list_referral_campaigns(self, store_id: int) -> list[dict]:
        sql = """
        SELECT
            c.*,
            COALESCE(tok.token_count, 0)  AS token_count,
            COALESCE(ref.total_referrals, 0) AS total_referrals
        FROM kirana_oltp.referral_campaigns c
        LEFT JOIN (
            SELECT campaign_id, COUNT(*) AS token_count
            FROM kirana_oltp.referral_tokens
            GROUP BY campaign_id
        ) tok ON tok.campaign_id = c.campaign_id
        LEFT JOIN (
            SELECT t.campaign_id, COUNT(*) AS total_referrals
            FROM kirana_oltp.referrals r
            JOIN kirana_oltp.referral_tokens t ON r.token_id = t.token_id
            WHERE r.status = 'rewarded'
            GROUP BY t.campaign_id
        ) ref ON ref.campaign_id = c.campaign_id
        WHERE c.store_id = :sid
        ORDER BY c.created_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def toggle_referral_campaign(self, campaign_id: int, is_active: bool) -> dict:
        sql = """
        UPDATE kirana_oltp.referral_campaigns SET is_active = :active
        WHERE campaign_id = :cid RETURNING *
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"cid": campaign_id, "active": is_active}).mappings().first()
            conn.commit()
        return dict(row) if row else {}

    def get_or_create_referral_token(self, store_id: int,
                                       referrer_customer_id: int,
                                       campaign_id: int) -> dict:
        check_sql = """
        SELECT token_id, token_hash FROM kirana_oltp.referral_tokens
        WHERE referrer_customer_id = :cid AND campaign_id = :camp
        """
        with self._conn() as conn:
            row = conn.execute(text(check_sql), {
                "cid": referrer_customer_id, "camp": campaign_id
            }).mappings().first()
            if row:
                return {"token_id": row["token_id"], "token_hash": row["token_hash"], "is_new": False}

            token_hash = secrets.token_hex(24)
            ins_sql = """
            INSERT INTO kirana_oltp.referral_tokens
                (store_id, referrer_customer_id, campaign_id, token_hash)
            VALUES (:sid, :cid, :camp, :tok)
            RETURNING token_id, token_hash
            """
            row = conn.execute(text(ins_sql), {
                "sid": store_id, "cid": referrer_customer_id,
                "camp": campaign_id, "tok": token_hash,
            }).mappings().first()
            conn.commit()
        return {"token_id": row["token_id"], "token_hash": row["token_hash"], "is_new": True}

    def get_token_info(self, token_hash: str) -> dict | None:
        sql = """
        SELECT t.token_id, t.store_id, t.referrer_customer_id, t.campaign_id,
               cu.name AS referrer_name, cu.phone AS referrer_phone,
               cu.referral_count,
               c.name AS campaign_name, c.referral_discount_pct,
               c.milestone_every_n, c.milestone_reward_pct, c.is_active,
               c.max_referrals_per_referrer
        FROM kirana_oltp.referral_tokens t
        JOIN kirana_oltp.customer cu ON cu.customer_id = t.referrer_customer_id
        JOIN kirana_oltp.referral_campaigns c ON c.campaign_id = t.campaign_id
        WHERE t.token_hash = :tok
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"tok": token_hash}).mappings().first()
        return dict(row) if row else None

    def process_referral(self, token_hash: str, new_phone: str, new_name: str, order_id: int | None = None) -> dict:
        info = self.get_token_info(token_hash)
        if not info:
            raise ValueError("Invalid or expired referral QR code")
        if not info["is_active"]:
            raise ValueError("This referral campaign is no longer active")

        store_id     = info["store_id"]
        referrer_id  = info["referrer_customer_id"]
        campaign_id  = info["campaign_id"]
        discount_pct = float(info["referral_discount_pct"])

        # ── Referral cap check ────────────────────────────────────────────────
        max_refs = info.get("max_referrals_per_referrer", 50)
        current_count = int(info.get("referral_count", 0))
        if max_refs is not None and current_count >= int(max_refs):
            raise ValueError(
                f"{info['referrer_name']} has reached the referral limit "
                f"({int(max_refs)} referrals) for this campaign."
            )

        with self._conn() as conn:
            cust_row = conn.execute(text("""
                SELECT customer_id FROM kirana_oltp.customer
                WHERE phone = :phone AND store_id = :sid
            """), {"phone": new_phone, "sid": store_id}).mappings().first()

            if cust_row:
                conn.execute(text("""
                    INSERT INTO kirana_oltp.referrals (token_id, new_customer_id, order_id, discount_applied, status)
                    VALUES (:tid, :ncid, :oid, 0, 'skipped_existing')
                """), {"tid": info["token_id"], "ncid": cust_row["customer_id"], "oid": order_id})
                conn.commit()
                return {
                    "status": "existing_customer",
                    "referrer_name": info["referrer_name"],
                    "campaign_name": info["campaign_name"],
                    "new_customer_id": cust_row["customer_id"],
                    "discount_pct": 0,
                    "voucher_earned": False,
                    "message": f"{new_phone} is already a customer. No referral reward.",
                }

            new_cust = conn.execute(text("""
                INSERT INTO kirana_oltp.customer (name, phone, store_id)
                VALUES (:name, :phone, :sid) RETURNING customer_id
            """), {"name": new_name or new_phone, "phone": new_phone, "sid": store_id}).mappings().first()
            new_customer_id = new_cust["customer_id"]

            conn.execute(text("""
                INSERT INTO kirana_oltp.referrals (token_id, new_customer_id, order_id, discount_applied, status)
                VALUES (:tid, :ncid, :oid, :disc, 'rewarded')
            """), {"tid": info["token_id"], "ncid": new_customer_id, "oid": order_id, "disc": discount_pct})

            ref_count_row = conn.execute(text("""
                UPDATE kirana_oltp.customer SET referral_count = referral_count + 1
                WHERE customer_id = :cid RETURNING referral_count
            """), {"cid": referrer_id}).mappings().first()
            new_count = ref_count_row["referral_count"]

            milestone_n      = info["milestone_every_n"]
            milestone_reward = float(info["milestone_reward_pct"])
            voucher_earned   = False

            if new_count > 0 and new_count % milestone_n == 0:
                conn.execute(text("""
                    INSERT INTO kirana_oltp.referral_vouchers (customer_id, store_id, campaign_id, discount_pct)
                    VALUES (:cid, :sid, :camp, :disc)
                """), {"cid": referrer_id, "sid": store_id, "camp": campaign_id, "disc": milestone_reward})
                voucher_earned = True

            conn.commit()

        return {
            "status": "new_customer",
            "referrer_name": info["referrer_name"],
            "campaign_name": info["campaign_name"],
            "new_customer_id": new_customer_id,
            "new_customer_name": new_name or new_phone,
            "discount_pct": discount_pct,
            "referrer_total_referrals": new_count,
            "voucher_earned": voucher_earned,
            "milestone_reward_pct": milestone_reward if voucher_earned else None,
            "message": f"New customer added! Apply {discount_pct}% discount on this order.",
        }

    def get_pending_vouchers(self, customer_id: int, store_id: int) -> list[dict]:
        sql = """
        SELECT v.*, c.name AS campaign_name
        FROM kirana_oltp.referral_vouchers v
        JOIN kirana_oltp.referral_campaigns c ON c.campaign_id = v.campaign_id
        WHERE v.customer_id = :cid AND v.store_id = :sid AND v.status = 'pending'
        ORDER BY v.earned_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"cid": customer_id, "sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def use_voucher(self, voucher_id: int, order_id: int | None = None) -> bool:
        sql = """
        UPDATE kirana_oltp.referral_vouchers
        SET status = 'used', used_at = NOW(), used_on_order_id = :oid
        WHERE voucher_id = :vid AND status = 'pending'
        RETURNING voucher_id
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"vid": voucher_id, "oid": order_id}).first()
            conn.commit()
        return row is not None

    # ── Store associations ─────────────────────────────────────────────────────

    def list_associations(self, store_id: int) -> list[dict]:
        sql = """
        SELECT association_id, store_id, name, area_type,
               estimated_households, notes, is_active, created_at
        FROM kirana_oltp.store_association
        WHERE store_id = :sid
        ORDER BY created_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result

    def add_association(self, store_id: int, name: str, area_type: str,
                        estimated_households: int | None, notes: str | None) -> dict:
        sql = """
        INSERT INTO kirana_oltp.store_association
            (store_id, name, area_type, estimated_households, notes)
        VALUES (:sid, :name, :atype, :hh, :notes)
        RETURNING *
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {
                "sid": store_id, "name": name, "atype": area_type,
                "hh": estimated_households, "notes": notes,
            }).mappings().first()
            conn.commit()
        d = dict(row)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        return d

    def update_association(self, association_id: int, store_id: int, **fields) -> dict | None:
        allowed = {"name", "area_type", "estimated_households", "notes", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        sql = f"""
        UPDATE kirana_oltp.store_association
        SET {set_clause}
        WHERE association_id = :aid AND store_id = :sid
        RETURNING *
        """
        params = {**updates, "aid": association_id, "sid": store_id}
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        if not row:
            return None
        d = dict(row)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        return d

    def delete_association(self, association_id: int, store_id: int) -> bool:
        sql = """
        DELETE FROM kirana_oltp.store_association
        WHERE association_id = :aid AND store_id = :sid
        """
        with self._conn() as conn:
            result = conn.execute(text(sql), {"aid": association_id, "sid": store_id})
            conn.commit()
        return result.rowcount > 0

    def get_association_heatmap(self, store_id: int) -> list[dict]:
        """Per-association sales metrics derived from customer purchase history."""
        sql = """
        SELECT
            a.association_id,
            a.name                  AS area_name,
            a.area_type,
            a.estimated_households,
            COUNT(DISTINCT c.customer_id)               AS customer_count,
            COUNT(o.order_id)                           AS total_orders,
            COALESCE(SUM(o.total_amount), 0)::float     AS total_revenue,
            COALESCE(AVG(o.total_amount), 0)::float     AS avg_order_value,
            MAX(o.order_date)                           AS last_order_at
        FROM kirana_oltp.store_association a
        LEFT JOIN kirana_oltp.customer c
            ON c.association_id = a.association_id
        LEFT JOIN kirana_oltp.orders o
            ON o.customer_id = c.customer_id
           AND o.store_id = :sid
           AND o.order_date >= NOW() - INTERVAL '90 days'
        WHERE a.store_id = :sid AND a.is_active = TRUE
        GROUP BY a.association_id, a.name, a.area_type, a.estimated_households
        ORDER BY total_revenue DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("last_order_at"):
                d["last_order_at"] = d["last_order_at"].isoformat()
            result.append(d)
        return result

    # ── KPI tier config ────────────────────────────────────────────────────────

    def get_kpi_tier_config(self) -> dict[str, str]:
        """Returns {kpi_id: required_tier} for all configured KPIs."""
        sql = "SELECT kpi_id, required_tier FROM kirana_oltp.kpi_tier_config"
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return {r["kpi_id"]: r["required_tier"] for r in rows}

    def upsert_kpi_tier_config(self, configs: list[dict]) -> None:
        """Bulk upsert [{kpi_id, required_tier}]. Replaces all existing entries."""
        if not configs:
            return
        sql = """
        INSERT INTO kirana_oltp.kpi_tier_config (kpi_id, required_tier, updated_at)
        VALUES (:kpi_id, :required_tier, NOW())
        ON CONFLICT (kpi_id) DO UPDATE
            SET required_tier = EXCLUDED.required_tier,
                updated_at    = NOW()
        """
        with self._conn() as conn:
            conn.execute(text(sql), configs)
            conn.commit()

    # ── Baskets ───────────────────────────────────────────────────────────────

    def get_baskets(self, store_id: int) -> list[dict]:
        sql = """
            SELECT b.basket_id, b.name, b.description, b.price,
                   b.valid_from::text, b.valid_to::text, b.is_active, b.created_at::text,
                   COALESCE(
                     json_agg(json_build_object(
                       'id', bi.id, 'product_id', bi.product_id,
                       'product_name', bi.product_name, 'qty', bi.qty
                     )) FILTER (WHERE bi.id IS NOT NULL), '[]'
                   ) AS items
            FROM kirana_oltp.basket b
            LEFT JOIN kirana_oltp.basket_item bi ON bi.basket_id = b.basket_id
            WHERE b.store_id = :sid AND b.is_active = TRUE
            GROUP BY b.basket_id
            ORDER BY b.created_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def create_basket(self, store_id: int, data: dict) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.basket(store_id, name, description, price, valid_from, valid_to)
                VALUES(:sid, :name, :desc, :price, :vf, :vt)
                RETURNING basket_id, name, description, price,
                          valid_from::text, valid_to::text, is_active, created_at::text
            """), {
                "sid": store_id, "name": data["name"],
                "desc": data.get("description"), "price": data.get("price"),
                "vf": data.get("valid_from"), "vt": data.get("valid_to"),
            }).mappings().first()
            basket_id = row["basket_id"]
            items = data.get("items", [])
            if items:
                conn.execute(text("""
                    INSERT INTO kirana_oltp.basket_item(basket_id, product_id, product_name, qty)
                    VALUES(:bid, :pid, :pname, :qty)
                """), [{"bid": basket_id, "pid": item["product_id"],
                        "pname": item.get("product_name"), "qty": item.get("qty", 1)}
                       for item in items])
            conn.commit()
        return dict(row)

    def delete_basket(self, store_id: int, basket_id: int) -> bool:
        with self._conn() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.basket SET is_active = FALSE WHERE basket_id = :bid AND store_id = :sid"
            ), {"bid": basket_id, "sid": store_id})
            conn.commit()
        return True
