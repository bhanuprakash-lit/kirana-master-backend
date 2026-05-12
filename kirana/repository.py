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


class KiranaRepository:
    def __init__(self, engine):
        self._engine = engine
        self._ensure_schema()

    def _conn(self):
        return self._engine.connect()

    # ── Schema bootstrap ──────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Idempotently extend kirana_oltp with auth + app columns, then migrate."""
        with self._conn() as conn:
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
                    revoked_at   TIMESTAMPTZ
                )
            """))

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

            # kirana_oltp.store — add app-metadata columns
            for ddl in [
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS store_type   VARCHAR(100) DEFAULT 'kirana'",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS footfall     INT",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS budget       NUMERIC",
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS daily_budget NUMERIC",
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

            # kirana_oltp.purchases — extensions for Distributor Payments
            for ddl in [
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS total_amount   NUMERIC(12,2)",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS due_date       DATE",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS payment_status VARCHAR(20) DEFAULT 'unpaid'",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS notes          VARCHAR(255)",
            ]:
                conn.execute(text(ddl))

            # kirana_oltp.customer — add store_id for multi-tenancy
            conn.execute(text(
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS store_id BIGINT "
                "REFERENCES kirana_oltp.store(store_id)"
            ))

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

    def create_session(self, user_id: int) -> str:
        token = secrets.token_hex(32)
        sql   = "INSERT INTO kirana_oltp.user_sessions(user_id, access_token) VALUES(:uid, :tok)"
        with self._conn() as conn:
            conn.execute(text(sql), {"uid": user_id, "tok": token})
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
                INSERT INTO kirana_oltp.store(name, location, region, store_type, footfall)
                VALUES(:sn, :location, :region, :st, :fp)
                RETURNING store_id, name, location, region, store_type, footfall
            """), {"sn": store_name, "location": location, "region": region,
                   "st": store_type, "fp": footfall}).mappings().first()
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
        count = 0
        with self._conn() as conn:
            for item in items:
                conn.execute(text(sql), {
                    "d": snapshot_date, "sid": store_id,
                    "skuid": item.get("sku_id"),   "us": item.get("units_sold"),
                    "st": item.get("stock"),        "rev": item.get("revenue"),
                    "prof": item.get("profit"),     "price": item.get("price"),
                    "pf": item.get("promo_flag"),
                })
                count += 1
            conn.commit()
        return count

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
        WHERE s.store_id = :sid AND s.snapshot_date = :snap_date::date
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
        count = 0
        with self._conn() as conn:
            for contact in contacts:
                name = contact["name"]
                phone = contact["phone"]
                
                # 1. Ensure customer exists (scoped to store_id)
                cust_sql = "SELECT customer_id FROM kirana_oltp.customer WHERE phone = :p AND store_id = :sid"
                cust_row = conn.execute(text(cust_sql), {"p": phone, "sid": store_id}).mappings().first()
                
                if not cust_row:
                    ins_cust = "INSERT INTO kirana_oltp.customer(name, phone, store_id) VALUES(:n, :p, :sid) RETURNING customer_id"
                    conn.execute(text(ins_cust), {"n": name, "p": phone, "sid": store_id})
                
                count += 1
            conn.commit()
        return count
