from __future__ import annotations
import hashlib
import logging
from sqlalchemy import text, inspect as sa_inspect

logger = logging.getLogger("kirana.repository")

_schema_initialized: bool = False

class BaseRepositoryMixin:
    def __init__(self, engine):
        self._engine = engine
        global _schema_initialized
        if not _schema_initialized:
            self._ensure_schema()
            _schema_initialized = True

    def _conn(self):
        return self._engine.connect()

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
            conn.execute(
                text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uidx_users_phone
                ON kirana_oltp.users(phone_number)
                WHERE phone_number IS NOT NULL
            """)
            )

            # kirana_oltp.user_sessions
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.user_sessions (
                    session_id   BIGSERIAL PRIMARY KEY,
                    user_id      BIGINT NOT NULL
                                     REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    access_token VARCHAR(128) UNIQUE NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    revoked_at   TIMESTAMPTZ,
                    login_method VARCHAR(20) DEFAULT 'password',
                    device_brand VARCHAR(50),
                    device_model VARCHAR(100),
                    os_name      VARCHAR(50),
                    os_version   VARCHAR(50),
                    ip_address   VARCHAR(45)
                )
            """)
            )
            for ddl in [
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS login_method VARCHAR(20) DEFAULT 'password'",
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS device_brand VARCHAR(50)",
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS device_model VARCHAR(100)",
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS os_name      VARCHAR(50)",
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS os_version   VARCHAR(50)",
                "ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS ip_address   VARCHAR(45)",
            ]:
                conn.execute(text(ddl))

            # kirana_oltp.issue_report
            conn.execute(
                text("""
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
            """)
            )

            # kirana_oltp.user_fcm_tokens — multi-device FCM token storage
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.user_fcm_tokens (
                    token_id    BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    fcm_token   VARCHAR(255) NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_user_fcm_tokens_token UNIQUE (fcm_token)
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_user_fcm_tokens_user_id ON kirana_oltp.user_fcm_tokens(user_id)"
                )
            )

            # kirana_oltp.app_activity — foreground/background lifecycle events
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.app_activity (
                    id              BIGSERIAL PRIMARY KEY,
                    user_id         BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    event           VARCHAR(20) NOT NULL,   -- 'foreground' | 'background'
                    duration_sec    INT,                    -- seconds in foreground (set on background event)
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_app_activity_user_id ON kirana_oltp.app_activity(user_id, created_at)"
                )
            )

            # kirana_oltp.khata_payments — recovery history log
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.khata_payments (
                    payment_id  BIGSERIAL PRIMARY KEY,
                    khata_id    BIGINT NOT NULL REFERENCES kirana_oltp.khata(khata_id) ON DELETE CASCADE,
                    store_id    BIGINT NOT NULL,
                    amount      NUMERIC NOT NULL,
                    paid_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    notes       TEXT
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_khata_payments_khata_id ON kirana_oltp.khata_payments(khata_id)"
                )
            )

            # kirana_oltp.basket — product bundles / combo deals
            conn.execute(
                text("""
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
            """)
            )
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.basket_item (
                    id          BIGSERIAL PRIMARY KEY,
                    basket_id   BIGINT NOT NULL REFERENCES kirana_oltp.basket(basket_id) ON DELETE CASCADE,
                    product_id  BIGINT NOT NULL,
                    product_name VARCHAR(255),
                    qty         NUMERIC NOT NULL DEFAULT 1
                )
            """)
            )
            # basket — tier/auto-discount + lifecycle columns
            for ddl in [
                "ALTER TABLE kirana_oltp.basket ADD COLUMN IF NOT EXISTS tier         VARCHAR(20)",
                "ALTER TABLE kirana_oltp.basket ADD COLUMN IF NOT EXISTS gross_total  NUMERIC",
                "ALTER TABLE kirana_oltp.basket ADD COLUMN IF NOT EXISTS discount_pct NUMERIC",
                "ALTER TABLE kirana_oltp.basket ADD COLUMN IF NOT EXISTS archived_at  TIMESTAMPTZ",
                "ALTER TABLE kirana_oltp.basket ADD COLUMN IF NOT EXISTS last_alerted_at TIMESTAMPTZ",
                # orders — basket attribution snapshot (which basket a sale came
                # from + the bundle value/savings, frozen at sale time so order
                # history stays accurate after a basket is edited/archived/deleted).
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_id      BIGINT",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_name    VARCHAR(255)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_gross   NUMERIC(12,2)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_savings NUMERIC(12,2)",
            ]:
                conn.execute(text(ddl))
            # per-store basket tier config (ranges + discount %); NULL row ⇒ defaults
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.basket_tier_config (
                    store_id   BIGINT PRIMARY KEY REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    config     JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            # per-customer pinned product price (customer-specific pricing)
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.customer_product_price (
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id) ON DELETE CASCADE,
                    product_id  BIGINT NOT NULL,
                    price       NUMERIC NOT NULL,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (store_id, customer_id, product_id)
                )
            """)
            )

            # kirana_oltp.vision_session — one shelf-scan (morning/evening) photo + its run
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.vision_session (
                    session_id    BIGSERIAL PRIMARY KEY,
                    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    session_type  VARCHAR(20) NOT NULL,          -- 'morning' | 'evening'
                    session_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                    image_url     TEXT,
                    status        VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending | done | failed
                    total_skus    INT NOT NULL DEFAULT 0,
                    total_units   INT NOT NULL DEFAULT 0,
                    unknown_count INT NOT NULL DEFAULT 0,
                    error         TEXT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_vision_session_store_date "
                    "ON kirana_oltp.vision_session(store_id, session_date)"
                )
            )

            # kirana_oltp.vision_item — one detected product in a session (product_id
            # NOT FK-constrained, matching basket_item/customer_product_price house style;
            # null product_id ⇒ unknown / unmatched. corrected_product_id = owner fix → training data).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.vision_item (
                    item_id              BIGSERIAL PRIMARY KEY,
                    session_id           BIGINT NOT NULL
                                             REFERENCES kirana_oltp.vision_session(session_id) ON DELETE CASCADE,
                    sku_id               VARCHAR(64),
                    product_id           BIGINT,
                    display_name         VARCHAR(255),
                    gemini_name          VARCHAR(255) NOT NULL,
                    visible_text         TEXT,
                    count                INT NOT NULL DEFAULT 1,
                    match_score          REAL NOT NULL DEFAULT 0,
                    is_unknown           BOOLEAN NOT NULL DEFAULT TRUE,
                    bbox_json            TEXT,
                    corrected_product_id BIGINT,
                    corrected_at         TIMESTAMPTZ,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_vision_item_session "
                    "ON kirana_oltp.vision_item(session_id)"
                )
            )

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

            # kirana_oltp.vertical_config — Foundation 1: one config row per
            # vertical (the master switch the app reads for feature flags, units,
            # KPI/ML/tax profiles, and copy). Grocery = everything on.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.vertical_config (
                    vertical_code TEXT PRIMARY KEY,
                    features      JSONB NOT NULL DEFAULT '{}',
                    unit_set      JSONB,
                    attribute_set JSONB,
                    kpi_set       JSONB,
                    ml_profile    TEXT,
                    tax_profile   TEXT,
                    copy_pack     JSONB
                )
            """)
            )
            # Coarse vertical switch on the store (granular store_type stays as-is).
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.store "
                    "ADD COLUMN IF NOT EXISTS vertical_code TEXT"
                )
            )
            # Seed the known verticals (idempotent — never overwrites edits).
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.vertical_config
                    (vertical_code, features, unit_set, ml_profile, tax_profile, copy_pack)
                VALUES
                    ('grocery',
                     '{"expiry": true, "loose": true, "variants": false, "serial": false, "warranty": false, "appointments": false, "vision": true}',
                     '["pcs","kg","g","L","ml","dozen","pack","box","bundle"]',
                     'grocery', 'grocery', '{}'),
                    ('apparel',
                     '{"expiry": false, "loose": false, "variants": true, "serial": false, "warranty": false, "appointments": false, "vision": false}',
                     '["pcs","pair","set"]',
                     'apparel', 'standard', '{}'),
                    ('footwear',
                     '{"expiry": false, "loose": false, "variants": true, "serial": false, "warranty": false, "appointments": false, "vision": false}',
                     '["pair","pcs","set"]',
                     'apparel', 'standard', '{}'),
                    ('electronics',
                     '{"expiry": false, "loose": false, "variants": true, "serial": true, "warranty": true, "appointments": false, "vision": false}',
                     '["pcs","set"]',
                     'electronics', 'standard', '{}'),
                    ('optical',
                     '{"expiry": false, "loose": false, "variants": true, "serial": false, "warranty": true, "appointments": true, "vision": false}',
                     '["pcs","pair"]',
                     'apparel', 'standard', '{}'),
                    ('services',
                     '{"expiry": false, "loose": false, "variants": false, "serial": false, "warranty": false, "appointments": true, "vision": false}',
                     '["pcs","session","hour"]',
                     'services', 'standard', '{}'),
                    ('general',
                     '{"expiry": false, "loose": false, "variants": false, "serial": false, "warranty": false, "appointments": false, "vision": false}',
                     '["pcs","pack","box","set"]',
                     'grocery', 'standard', '{}')
                ON CONFLICT (vertical_code) DO NOTHING
            """)
            )
            # Backfill existing stores → grocery (all current store_types are
            # grocery-family). Self-limiting via the NULL guard, safe every boot.
            conn.execute(
                text(
                    "UPDATE kirana_oltp.store SET vertical_code = 'grocery' "
                    "WHERE vertical_code IS NULL"
                )
            )

            # ── Foundation 2: product variants + dynamic attributes ───────────
            # product_attribute_def: per-vertical attributes (size, colour, …),
            # and which of them are variant axes the add-product grid uses.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.product_attribute_def (
                    id              BIGSERIAL PRIMARY KEY,
                    vertical_code   TEXT NOT NULL,
                    attr_code       TEXT NOT NULL,
                    label           TEXT NOT NULL,
                    type            TEXT NOT NULL DEFAULT 'text',
                    options         JSONB,
                    is_variant_axis BOOLEAN NOT NULL DEFAULT FALSE,
                    sort            INT NOT NULL DEFAULT 0,
                    UNIQUE (vertical_code, attr_code)
                )
            """)
            )
            # product_variant: one sellable variant of a product. Grocery keeps
            # exactly one *implicit* variant so all existing queries keep working.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.product_variant (
                    variant_id  BIGSERIAL PRIMARY KEY,
                    product_id  BIGINT NOT NULL
                                    REFERENCES kirana_oltp.product(product_id) ON DELETE CASCADE,
                    sku         VARCHAR(100),
                    barcode     VARCHAR(100),
                    attributes  JSONB NOT NULL DEFAULT '{}',
                    price       NUMERIC(10,2),
                    mrp         NUMERIC(10,2),
                    cost        NUMERIC(10,2),
                    stock       NUMERIC(12,2) NOT NULL DEFAULT 0,
                    is_implicit BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.product_variant ADD COLUMN IF NOT EXISTS "
                    "stock NUMERIC(12,2) NOT NULL DEFAULT 0"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_product_variant_product "
                    "ON kirana_oltp.product_variant(product_id)"
                )
            )
            # Stock + sales reference a variant; NULL means the implicit one, so
            # legacy rows and grocery flows are unaffected.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.inventory ADD COLUMN IF NOT EXISTS "
                    "variant_id BIGINT REFERENCES kirana_oltp.product_variant(variant_id)"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.order_item ADD COLUMN IF NOT EXISTS "
                    "variant_id BIGINT REFERENCES kirana_oltp.product_variant(variant_id)"
                )
            )
            # Migration: give every existing product one implicit variant, then
            # point its stock + past sales at it. All guards make this a no-op
            # after the first boot (grocery = one implicit variant rule).
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.product_variant
                    (product_id, sku, barcode, is_implicit, is_active)
                SELECT p.product_id, p.sku, p.barcode, TRUE, TRUE
                FROM kirana_oltp.product p
                WHERE NOT EXISTS (
                    SELECT 1 FROM kirana_oltp.product_variant v
                    WHERE v.product_id = p.product_id
                )
            """)
            )
            conn.execute(
                text("""
                UPDATE kirana_oltp.inventory inv
                SET variant_id = v.variant_id
                FROM kirana_oltp.product_variant v
                WHERE v.product_id = inv.product_id
                  AND v.is_implicit = TRUE
                  AND inv.variant_id IS NULL
            """)
            )
            conn.execute(
                text("""
                UPDATE kirana_oltp.order_item oi
                SET variant_id = v.variant_id
                FROM kirana_oltp.product_variant v
                WHERE v.product_id = oi.product_id
                  AND v.is_implicit = TRUE
                  AND oi.variant_id IS NULL
            """)
            )
            # Seed the variant axes each vertical exposes (idempotent).
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.product_attribute_def
                    (vertical_code, attr_code, label, type, options, is_variant_axis, sort)
                VALUES
                    ('apparel','size','Size','enum','["XS","S","M","L","XL","XXL"]',TRUE,1),
                    ('apparel','colour','Colour','text',NULL,TRUE,2),
                    ('footwear','size','Size','enum','["5","6","7","8","9","10","11","12"]',TRUE,1),
                    ('footwear','colour','Colour','text',NULL,TRUE,2),
                    ('electronics','model','Model','text',NULL,TRUE,1),
                    ('electronics','storage','Storage','enum','["64GB","128GB","256GB","512GB","1TB"]',TRUE,2),
                    ('electronics','colour','Colour','text',NULL,TRUE,3),
                    ('optical','frame_size','Frame Size','text',NULL,TRUE,1),
                    ('optical','lens_type','Lens Type','enum','["single_vision","bifocal","progressive"]',TRUE,2),
                    ('optical','colour','Colour','text',NULL,TRUE,3)
                ON CONFLICT (vertical_code, attr_code) DO NOTHING
            """)
            )

            # ── Foundation 3: tax / GST ───────────────────────────────────────
            # Per-product HSN + GST rate, with store-level fallback rules by
            # category / price band. Retail prices are GST-inclusive; the tax
            # component is extracted at sale time for a compliant bill breakup.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS "
                    "hsn_code VARCHAR(20)"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS "
                    "gst_rate NUMERIC(5,2)"
                )
            )
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.tax_rule (
                    rule_id     BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT REFERENCES kirana_oltp.store(store_id),
                    category_id BIGINT,
                    hsn_code    VARCHAR(20),
                    min_price   NUMERIC(12,2),
                    max_price   NUMERIC(12,2),
                    gst_rate    NUMERIC(5,2) NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            for ddl in [
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(12,2)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS taxable_amount NUMERIC(12,2)",
                "ALTER TABLE kirana_oltp.order_item ADD COLUMN IF NOT EXISTS gst_rate NUMERIC(5,2)",
                "ALTER TABLE kirana_oltp.order_item ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(10,2)",
            ]:
                conn.execute(text(ddl))

            # ── Foundation 4: per-vertical KPI visibility (admin-controlled) ──
            # Admin overrides which KPIs are shown for each vertical. No row =>
            # fall back to the registry default (ok = shown, coming-soon = hidden).
            # Lets the admin panel switch KPIs on/off live without an app update.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.kpi_visibility_config (
                    kpi_id        TEXT NOT NULL,
                    vertical_code TEXT NOT NULL,
                    is_visible    BOOLEAN NOT NULL,
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (kpi_id, vertical_code)
                )
            """)
            )

            # ── Module M1: Loyalty & Offers (opt-in per store) ───────────────
            conn.execute(text(
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS birthday DATE"))
            conn.execute(text(
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS anniversary DATE"))
            # Per-store loyalty settings (off until the owner enables it).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.loyalty_config (
                    store_id               BIGINT PRIMARY KEY REFERENCES kirana_oltp.store(store_id),
                    is_active              BOOLEAN NOT NULL DEFAULT FALSE,
                    points_per_100         NUMERIC NOT NULL DEFAULT 1,   -- points earned per ₹100 spent
                    redeem_paise_per_point INT NOT NULL DEFAULT 100,     -- ₹ value of 1 point (100 paise = ₹1)
                    silver_threshold       INT NOT NULL DEFAULT 500,
                    gold_threshold         INT NOT NULL DEFAULT 2000,
                    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            # Points ledger — balance is SUM(points) per customer (+earn / −redeem).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.loyalty_transaction (
                    txn_id      BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
                    order_id    BIGINT,
                    points      NUMERIC NOT NULL,
                    kind        VARCHAR(20) NOT NULL,   -- earn | redeem | bonus | adjust
                    note        VARCHAR(255),
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_loyalty_txn_customer "
                "ON kirana_oltp.loyalty_transaction(customer_id)"))
            # Discount / coupon rules.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.coupon (
                    coupon_id     BIGSERIAL PRIMARY KEY,
                    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    code          VARCHAR(40) NOT NULL,
                    discount_type VARCHAR(10) NOT NULL,   -- percent | flat
                    value         NUMERIC NOT NULL,
                    min_order     NUMERIC NOT NULL DEFAULT 0,
                    max_discount  NUMERIC,
                    valid_from    DATE,
                    valid_to      DATE,
                    usage_limit   INT,
                    used_count    INT NOT NULL DEFAULT 0,
                    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (store_id, code)
                )
            """)
            )
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.coupon_redemption (
                    id          BIGSERIAL PRIMARY KEY,
                    coupon_id   BIGINT NOT NULL REFERENCES kirana_oltp.coupon(coupon_id) ON DELETE CASCADE,
                    store_id    BIGINT NOT NULL,
                    order_id    BIGINT,
                    customer_id BIGINT,
                    discount    NUMERIC NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )

            # ── Module M4: Services & Appointments (salon/fitness/optical) ────
            # Priced service catalogue per store.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.service (
                    service_id   BIGSERIAL PRIMARY KEY,
                    store_id     BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    name         VARCHAR(150) NOT NULL,
                    price        NUMERIC(10,2) NOT NULL DEFAULT 0,
                    duration_min INT NOT NULL DEFAULT 30,
                    category     VARCHAR(80),
                    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            # Appointments / bookings.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.appointment (
                    appointment_id BIGSERIAL PRIMARY KEY,
                    store_id       BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    customer_id    BIGINT REFERENCES kirana_oltp.customer(customer_id),
                    service_id     BIGINT REFERENCES kirana_oltp.service(service_id),
                    staff_user_id  BIGINT,
                    customer_name  VARCHAR(150),
                    customer_phone VARCHAR(20),
                    starts_at      TIMESTAMPTZ NOT NULL,
                    duration_min   INT NOT NULL DEFAULT 30,
                    status         VARCHAR(20) NOT NULL DEFAULT 'booked',  -- booked|completed|cancelled|no_show
                    price          NUMERIC(10,2),
                    order_id       BIGINT,
                    notes          VARCHAR(255),
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_appointment_store_day "
                "ON kirana_oltp.appointment(store_id, starts_at)"))
            # Membership / package: prepaid bundle of sessions for a customer.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.membership (
                    membership_id   BIGSERIAL PRIMARY KEY,
                    store_id        BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    customer_id     BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
                    name            VARCHAR(150) NOT NULL,
                    total_sessions  INT NOT NULL DEFAULT 0,   -- 0 = unlimited / validity-based
                    used_sessions   INT NOT NULL DEFAULT 0,
                    price           NUMERIC(10,2) NOT NULL DEFAULT 0,
                    valid_until     DATE,
                    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )

            # ── Module M2: Multi-store rollup (chains / multi-outlet owners) ──
            # A group is one owner's chain; member stores link via store.group_id.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.store_group (
                    group_id      BIGSERIAL PRIMARY KEY,
                    name          VARCHAR(150) NOT NULL,
                    owner_user_id BIGINT REFERENCES kirana_oltp.users(user_id),
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(text(
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS "
                "group_id BIGINT REFERENCES kirana_oltp.store_group(group_id)"))
            conn.execute(text(
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS city VARCHAR(100)"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_store_group ON kirana_oltp.store(group_id)"))

            # ── Module M5: Staff Operations ───────────────────────────────────
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.staff (
                    staff_id       BIGSERIAL PRIMARY KEY,
                    store_id       BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    user_id        BIGINT REFERENCES kirana_oltp.users(user_id),
                    name           VARCHAR(150) NOT NULL,
                    phone          VARCHAR(20),
                    role           VARCHAR(50),
                    commission_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
                    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.staff_attendance (
                    id        BIGSERIAL PRIMARY KEY,
                    staff_id  BIGINT NOT NULL REFERENCES kirana_oltp.staff(staff_id) ON DELETE CASCADE,
                    store_id  BIGINT NOT NULL,
                    att_date  DATE NOT NULL,
                    status    VARCHAR(12) NOT NULL DEFAULT 'present',  -- present|absent|half_day|leave
                    check_in  TIMESTAMPTZ,
                    check_out TIMESTAMPTZ,
                    UNIQUE (staff_id, att_date)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.staff_task (
                    task_id    BIGSERIAL PRIMARY KEY,
                    store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    staff_id   BIGINT REFERENCES kirana_oltp.staff(staff_id) ON DELETE SET NULL,
                    title      VARCHAR(255) NOT NULL,
                    due_date   DATE,
                    is_done    BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            # ── Module M6: Orders & Fulfilment (estimate, returns, delivery) ──
            for ddl in [
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(20)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS delivery_address VARCHAR(500)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS delivery_fee NUMERIC(10,2)",
            ]:
                conn.execute(text(ddl))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.estimate (
                    estimate_id   BIGSERIAL PRIMARY KEY,
                    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    customer_id   BIGINT,
                    customer_name VARCHAR(150),
                    total         NUMERIC(12,2) NOT NULL DEFAULT 0,
                    status        VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft|sent|converted|expired
                    valid_until   DATE,
                    order_id      BIGINT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.estimate_item (
                    id          BIGSERIAL PRIMARY KEY,
                    estimate_id BIGINT NOT NULL REFERENCES kirana_oltp.estimate(estimate_id) ON DELETE CASCADE,
                    product_id  BIGINT,
                    name        VARCHAR(200) NOT NULL,
                    quantity    NUMERIC NOT NULL DEFAULT 1,
                    unit_price  NUMERIC(10,2) NOT NULL DEFAULT 0
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.sales_return (
                    return_id     BIGSERIAL PRIMARY KEY,
                    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    order_id      BIGINT,
                    customer_id   BIGINT,
                    reason        VARCHAR(50),
                    refund_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
                    is_exchange   BOOLEAN NOT NULL DEFAULT FALSE,
                    notes         VARCHAR(255),
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            # ── Module M3: Multi-location / multi-rack stock ──────────────────
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_location (
                    id          BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    product_id  BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
                    variant_id  BIGINT,
                    rack        VARCHAR(60) NOT NULL,
                    quantity    NUMERIC NOT NULL DEFAULT 0,
                    UNIQUE (store_id, product_id, variant_id, rack)
                )
            """))

            # ── Module M7: Warranty & Serial (electronics) ────────────────────
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.product_serial (
                    serial_id   BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    product_id  BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
                    variant_id  BIGINT,
                    serial_no   VARCHAR(120) NOT NULL,
                    order_id    BIGINT,
                    customer_id BIGINT,
                    status      VARCHAR(16) NOT NULL DEFAULT 'in_stock',  -- in_stock|sold|returned
                    warranty_until DATE,
                    sold_at     TIMESTAMPTZ,
                    UNIQUE (store_id, serial_no)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.warranty_claim (
                    claim_id    BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    product_id  BIGINT,
                    serial_id   BIGINT REFERENCES kirana_oltp.product_serial(serial_id),
                    customer_id BIGINT,
                    issue       VARCHAR(255),
                    status      VARCHAR(16) NOT NULL DEFAULT 'open',  -- open|resolved|rejected
                    claim_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                    resolved_at TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            # ── Module M8: Customer 360+ (wishlist + profiles) ────────────────
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.wishlist (
                    id          BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
                    product_id  BIGINT,
                    note        VARCHAR(200),
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            for ddl in [
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS prescription TEXT",
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS style_profile VARCHAR(255)",
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS size_profile VARCHAR(120)",
            ]:
                conn.execute(text(ddl))

            # ── Module M9: Job Cards / Repair / Pre-order ─────────────────────
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.job_card (
                    job_id         BIGSERIAL PRIMARY KEY,
                    store_id       BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    customer_id    BIGINT,
                    customer_name  VARCHAR(150),
                    customer_phone VARCHAR(20),
                    job_type       VARCHAR(20) NOT NULL DEFAULT 'repair',  -- alteration|repair|preorder
                    item_desc      VARCHAR(255),
                    details        VARCHAR(500),
                    charge         NUMERIC(10,2),
                    status         VARCHAR(16) NOT NULL DEFAULT 'received',  -- received|in_progress|ready|delivered|cancelled
                    promised_date  DATE,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))

            # kirana_oltp.user_prefs
            conn.execute(
                text("""
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
            """)
            )

            # kirana_oltp.inventory_snapshots — ensure upserted_at exists
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.inventory_snapshots "
                    "ADD COLUMN IF NOT EXISTS upserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                )
            )

            # kirana_oltp.cashflow_requests — cash support requests
            conn.execute(
                text("""
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
            """)
            )

            # kirana_oltp.purchases — extensions for Distributor Payments
            for ddl in [
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS total_amount   NUMERIC(12,2)",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS due_date       DATE",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS payment_status VARCHAR(20) DEFAULT 'unpaid'",
                "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS notes          VARCHAR(255)",
            ]:
                conn.execute(text(ddl))

            # kirana_oltp.customer — add store_id for multi-tenancy + unique constraint + indexes
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS store_id BIGINT "
                    "REFERENCES kirana_oltp.store(store_id)"
                )
            )
            # Per-customer udhaar-reminder throttle (one WhatsApp reminder/day).
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS "
                    "last_udhaar_reminded_at TIMESTAMP"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS "
                    "is_deleted BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS "
                    "deleted_at TIMESTAMPTZ"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_customer_store_id "
                    "ON kirana_oltp.customer(store_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_customer_store_phone "
                    "ON kirana_oltp.customer(store_id, phone)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_customer_store_active "
                    "ON kirana_oltp.customer(store_id) WHERE is_deleted = FALSE"
                )
            )
            # Performance indexes for high-frequency queries
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_orders_store_date "
                    "ON kirana_oltp.orders(store_id, order_date DESC)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_inventory_store_product "
                    "ON kirana_oltp.inventory_snapshots(store_id, product_id)"
                )
            )

            # ── Referral System tables ────────────────────────────────────────

            conn.execute(
                text("""
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
            """)
            )

            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.referral_tokens (
                    token_id             BIGSERIAL PRIMARY KEY,
                    store_id             BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    referrer_customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
                    campaign_id          BIGINT NOT NULL REFERENCES kirana_oltp.referral_campaigns(campaign_id),
                    token_hash           VARCHAR(64) UNIQUE NOT NULL,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (referrer_customer_id, campaign_id)
                )
            """)
            )

            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.referrals (
                    referral_id      BIGSERIAL PRIMARY KEY,
                    token_id         BIGINT NOT NULL REFERENCES kirana_oltp.referral_tokens(token_id),
                    new_customer_id  BIGINT REFERENCES kirana_oltp.customer(customer_id),
                    order_id         BIGINT REFERENCES kirana_oltp.orders(order_id),
                    discount_applied NUMERIC(5,2),
                    status           VARCHAR(20) NOT NULL DEFAULT 'rewarded',
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )

            conn.execute(
                text("""
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
            """)
            )

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

            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.ai_usage (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    feature     VARCHAR(20) NOT NULL,
                    usage_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                    count       INT NOT NULL DEFAULT 0,
                    UNIQUE (user_id, feature, usage_date)
                )
            """)
            )

            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.ai_credits (
                    id       BIGSERIAL PRIMARY KEY,
                    user_id  BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    feature  VARCHAR(20) NOT NULL,
                    balance  INT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    UNIQUE (user_id, feature)
                )
            """)
            )

            # kirana_oltp.udhaar_consent — voice-consent clip per udhaar order.
            # audio_blob = Azure Blob name (durable, legal record). analysis is
            # filled asynchronously by the in-house voice model (consent extract
            # + speaker match), so status starts 'pending' and the clip uploads
            # via a persistent client queue (owner is never blocked).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.udhaar_consent (
                    consent_id     BIGSERIAL PRIMARY KEY,
                    store_id       BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    order_id       BIGINT REFERENCES kirana_oltp.orders(order_id),
                    khata_id       BIGINT REFERENCES kirana_oltp.khata(khata_id),
                    customer_id    BIGINT REFERENCES kirana_oltp.customer(customer_id),
                    audio_blob     VARCHAR(500) NOT NULL,
                    duration_sec   NUMERIC(6,2),
                    language       VARCHAR(10),
                    agreed_total   NUMERIC(12,2),
                    agreed_udhaar  NUMERIC(12,2),
                    promised_date  DATE,
                    status         VARCHAR(20) NOT NULL DEFAULT 'pending',
                    analysis       JSONB,
                    voice_match_score NUMERIC(5,4),
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    analyzed_at    TIMESTAMPTZ
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_udhaar_consent_order "
                    "ON kirana_oltp.udhaar_consent(order_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_udhaar_consent_status "
                    "ON kirana_oltp.udhaar_consent(status)"
                )
            )

            # ── One-time data backfills (guarded so they run exactly once) ──────
            # Tracks which one-off backfills have already been applied, so a
            # redeploy never re-runs them and clobbers data the user has since
            # edited (e.g. due dates they set manually).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.app_migrations (
                    key        TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )

            # Existing pending udhaars predate the due-date feature, so give them
            # a single sensible repayment deadline (30 Jun 2026). New udhaars get
            # a date chosen at sale time. Runs once; future edits are preserved.
            _due_backfill_key = "backfill_khata_due_2026_06_30"
            already = conn.execute(
                text("SELECT 1 FROM kirana_oltp.app_migrations WHERE key = :k"),
                {"k": _due_backfill_key},
            ).first()
            if not already:
                conn.execute(
                    text("""
                    UPDATE kirana_oltp.khata
                    SET due_date = DATE '2026-06-30'
                    WHERE status IN ('open', 'pending', 'overdue')
                """)
                )
                conn.execute(
                    text(
                        "INSERT INTO kirana_oltp.app_migrations(key) VALUES (:k) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": _due_backfill_key},
                )

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
                conn.execute(
                    text("""
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
                """)
                )
                # Sync sequence
                conn.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence('kirana_oltp.users','user_id'),"
                        " (SELECT COALESCE(MAX(user_id), 1) FROM kirana_oltp.users))"
                    )
                )

            if "kirana_user_sessions" in public:
                # Map old sessions to new user_ids via username
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.user_sessions
                        (user_id, access_token, created_at, revoked_at)
                    SELECT u_new.user_id, s.access_token, s.created_at, s.revoked_at
                    FROM public.kirana_user_sessions s
                    JOIN public.kirana_app_users a ON s.user_id = a.user_id
                    JOIN kirana_oltp.users u_new ON a.username = u_new.username
                    ON CONFLICT (access_token) DO NOTHING
                """)
                )
                conn.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence('kirana_oltp.user_sessions','session_id'),"
                        " (SELECT COALESCE(MAX(session_id), 1) FROM kirana_oltp.user_sessions))"
                    )
                )

            if "kirana_stores" in public:
                conn.execute(
                    text("""
                    UPDATE kirana_oltp.store s
                    SET store_type   = COALESCE(s.store_type,   ks.store_type),
                        footfall     = COALESCE(s.footfall,     ks.footfall),
                        budget       = COALESCE(s.budget,       ks.budget),
                        daily_budget = COALESCE(s.daily_budget, ks.daily_budget)
                    FROM public.kirana_stores ks
                    WHERE ks.store_id = s.store_id
                """)
                )

            # Seed deterministic footfall for legacy rows that still have nulls
            # (original seed-data rows had no footfall). footfall is also
            # recomputed from real order volume by compute_store_footfall().
            # Budget is intentionally NOT seeded — it is the owner's real
            # monthly sales target, collected at onboarding / store settings.
            conn.execute(
                text("""
                UPDATE kirana_oltp.store
                SET footfall     = COALESCE(footfall,     80 + (store_id * 17) % 80),
                    store_type   = COALESCE(store_type,   'kirana')
                WHERE COALESCE(is_deleted, FALSE) = FALSE
                  AND footfall IS NULL
            """)
            )
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('kirana_oltp.store','store_id'),"
                    " (SELECT COALESCE(MAX(store_id), 1) FROM kirana_oltp.store))"
                )
            )

            if "kirana_inventory_snapshots" in public:
                try:
                    conn.execute(
                        text("""
                        INSERT INTO kirana_oltp.inventory_snapshots
                            (snapshot_date, store_id, product_id, units_sold, stock,
                             revenue, profit, price, promo_flag)
                        SELECT snapshot_date, store_id, sku_id, units_sold, stock,
                               revenue, profit, price, promo_flag
                        FROM kirana_inventory_snapshots
                        ON CONFLICT DO NOTHING
                    """)
                    )
                except Exception as exc:
                    logger.warning("inventory_snapshots migration skipped: %s", exc)
                    conn.rollback()

            if "kirana_user_prefs" in public:
                conn.execute(
                    text("""
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
                """)
                )

            conn.commit()

    @staticmethod
    def _default_email(username: str) -> str | None:
        uname = (username or "").strip()
        return uname if "@" in uname else None

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.sha256((salt + password).encode()).hexdigest()
