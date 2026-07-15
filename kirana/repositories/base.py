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
                # Bill-level discounts, persisted so order history can explain
                # the paid total (coupon / points value / custom bill discount).
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS coupon_discount NUMERIC(10,2)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS redeem_value    NUMERIC(10,2)",
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS manual_discount NUMERIC(10,2)",
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
                    finished_at   TIMESTAMPTZ,
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
            # session_type='onboarding' reuses this table for bulk stock-in; committed_at
            # is stamped when the owner's reviewed quantities are written to inventory.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.vision_session "
                    "ADD COLUMN IF NOT EXISTS committed_at TIMESTAMPTZ"
                )
            )
            # finished_at = when background analysis finalized (or failed) the session;
            # finished_at - created_at is the processing latency shown in analytics.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.vision_session "
                    "ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ"
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
                    image_index          SMALLINT NOT NULL DEFAULT 0,
                    detector_source      VARCHAR(16) NOT NULL DEFAULT 'gemini',
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
            # image_index = which of the session's photos this detection came from
            # (the session's image_url is a JSON array). Lets the review screen crop
            # the detection's bbox out of the right source photo for a visual thumbnail.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.vision_item "
                    "ADD COLUMN IF NOT EXISTS image_index SMALLINT NOT NULL DEFAULT 0"
                )
            )
            # detector_source = 'yolo' (our custom model) | 'gemini' (fallback); rows
            # from before the column existed default to 'gemini' — YOLO shipped later.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.vision_item "
                    "ADD COLUMN IF NOT EXISTS detector_source VARCHAR(16) NOT NULL DEFAULT 'gemini'"
                )
            )

            # kirana_oltp.counter_session — one sale-area COUNTER run (on-device YOLO
            # at the billing counter). Distinct from vision_session (shelf photos):
            # detection + line-crossing tally happen ON THE DEVICE; the app syncs the
            # finalized per-product tally here. client_uid = on-device UUID so a retry
            # upserts the same session instead of duplicating it.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.counter_session (
                    session_id   BIGSERIAL PRIMARY KEY,
                    store_id     BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    client_uid   VARCHAR(64) NOT NULL,       -- idempotency key from the device
                    session_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    device_label VARCHAR(120),
                    source       VARCHAR(30) NOT NULL DEFAULT 'on_device',
                    started_at   TIMESTAMPTZ,
                    ended_at     TIMESTAMPTZ,
                    total_units  INT NOT NULL DEFAULT 0,     -- items counted across all products
                    total_skus   INT NOT NULL DEFAULT 0,     -- distinct products counted
                    unknown_count INT NOT NULL DEFAULT 0,    -- units whose class didn't match the catalog
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (store_id, client_uid)
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_counter_session_store_date "
                    "ON kirana_oltp.counter_session(store_id, session_date)"
                )
            )

            # kirana_oltp.counter_item — one product tally in a counter run. class_name
            # is the on-device model's label; product_id is resolved server-side via the
            # shared CatalogMatcher (null ⇒ unknown, same convention as vision_item).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.counter_item (
                    item_id        BIGSERIAL PRIMARY KEY,
                    session_id     BIGINT NOT NULL
                                       REFERENCES kirana_oltp.counter_session(session_id) ON DELETE CASCADE,
                    product_id     BIGINT,
                    class_name     VARCHAR(255) NOT NULL,
                    display_name   VARCHAR(255),
                    qty            INT NOT NULL DEFAULT 1,
                    match_score    REAL NOT NULL DEFAULT 0,
                    is_unknown     BOOLEAN NOT NULL DEFAULT TRUE,
                    avg_confidence REAL,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_counter_item_session "
                    "ON kirana_oltp.counter_item(session_id)"
                )
            )

            # ── Call center / tele-calling ──────────────────────────────────
            # kirana_oltp.call_executive — a tele-calling agent or their manager.
            # Own credentials (not app users): they log into the admin panel with a
            # username+password session (role gates the panel), so every call is
            # attributed to a real person.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.call_executive (
                    executive_id  BIGSERIAL PRIMARY KEY,
                    username      VARCHAR(100) UNIQUE NOT NULL,
                    full_name     VARCHAR(255) NOT NULL,
                    phone         VARCHAR(20),
                    email         VARCHAR(255),
                    role          VARCHAR(20) NOT NULL DEFAULT 'call_executive',  -- | 'call_manager'
                    password_salt VARCHAR(64),
                    password_hash VARCHAR(128),
                    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            # kirana_oltp.call_executive_session — bearer token per login (mirrors
            # user_sessions; token in Authorization: Bearer for the panel).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.call_executive_session (
                    session_id   BIGSERIAL PRIMARY KEY,
                    executive_id BIGINT NOT NULL
                                     REFERENCES kirana_oltp.call_executive(executive_id) ON DELETE CASCADE,
                    access_token VARCHAR(128) UNIQUE NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    revoked_at   TIMESTAMPTZ
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_call_exec_session_token "
                    "ON kirana_oltp.call_executive_session(access_token)"
                )
            )
            # kirana_oltp.store_assignment — which executive owns which store.
            # status lets us unassign without losing history.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.store_assignment (
                    assignment_id BIGSERIAL PRIMARY KEY,
                    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    executive_id  BIGINT NOT NULL
                                      REFERENCES kirana_oltp.call_executive(executive_id) ON DELETE CASCADE,
                    assigned_by   BIGINT,      -- executive_id of the manager (NULL = admin key)
                    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    status        VARCHAR(20) NOT NULL DEFAULT 'active',  -- active | unassigned
                    priority      SMALLINT NOT NULL DEFAULT 0
                )
            """)
            )
            # One ACTIVE assignment per store (a store belongs to one exec at a time).
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uidx_store_assignment_active "
                    "ON kirana_oltp.store_assignment(store_id) WHERE status = 'active'"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_store_assignment_exec "
                    "ON kirana_oltp.store_assignment(executive_id, status)"
                )
            )
            # kirana_oltp.call_log — one row per call attempt (the heart of it).
            # duration_sec / recording_url are reserved for future click-to-call.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.call_log (
                    call_id          BIGSERIAL PRIMARY KEY,
                    store_id         BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    executive_id     BIGINT NOT NULL REFERENCES kirana_oltp.call_executive(executive_id),
                    called_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    answered         BOOLEAN,
                    disposition      VARCHAR(24) NOT NULL,   -- answered|no_answer|busy|switched_off|wrong_number|invalid_number
                    app_usage_status VARCHAR(24),            -- using_active|using_rare|stopped|never_started|needs_training
                    feedback_text    TEXT,
                    sentiment        VARCHAR(12),            -- positive|neutral|negative
                    rating           SMALLINT,               -- 1..5
                    next_action      VARCHAR(16),            -- callback|escalate|done|do_not_call
                    callback_at      TIMESTAMPTZ,
                    duration_sec     INT,
                    recording_url    TEXT,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_call_log_store "
                    "ON kirana_oltp.call_log(store_id, called_at DESC)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_call_log_exec "
                    "ON kirana_oltp.call_log(executive_id, called_at DESC)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_call_log_callback "
                    "ON kirana_oltp.call_log(callback_at) WHERE next_action = 'callback'"
                )
            )
            # kirana_oltp.call_feedback_tag — multi-tag a call for the feedback digest.
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.call_feedback_tag (
                    id      BIGSERIAL PRIMARY KEY,
                    call_id BIGINT NOT NULL REFERENCES kirana_oltp.call_log(call_id) ON DELETE CASCADE,
                    tag     VARCHAR(24) NOT NULL   -- bug|feature_request|pricing|training|happy|churn_risk
                )
            """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_call_feedback_tag_call "
                    "ON kirana_oltp.call_feedback_tag(call_id)"
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
                # Whether this store's data is shown in the director analytics
                # dashboard. Default TRUE (real stores show); admins switch OFF
                # dev/test/internal stores from the admin panel.
                "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS include_in_director BOOLEAN NOT NULL DEFAULT TRUE",
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
            # V0.5 — GST registration is a store-level fact (a registered
            # kirana files GST too), not a vertical trait. Drives whether the
            # app shows the GST report; defaults off for grocery-family
            # stores, and the app treats non-grocery verticals as enabled
            # regardless for back-compat.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.store "
                    "ADD COLUMN IF NOT EXISTS gst_enabled BOOLEAN NOT NULL DEFAULT FALSE"
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
                ON CONFLICT (vertical_code) DO UPDATE SET
                    features    = EXCLUDED.features,
                    unit_set    = EXCLUDED.unit_set,
                    ml_profile  = EXCLUDED.ml_profile,
                    tax_profile = EXCLUDED.tax_profile
                -- copy_pack intentionally NOT overwritten (may hold custom wording).
                -- Self-heals rows seeded before unit_set / the variants/serial/
                -- warranty feature keys existed; the old DO NOTHING left those
                -- stale, which surfaced as grocery units (ml/L) and stray variant
                -- UI in every vertical.
            """)
            )
            # Backfill feature keys added to the seed AFTER the rows were first
            # inserted (e.g. F4 added `vision`/`appointments`). ON CONFLICT DO
            # NOTHING above never updates existing rows, so without this the
            # `vision` key is absent and Vision shows for every vertical. We merge
            # the canonical flags UNDER the existing JSON (`built || features`), so
            # any present key wins and only missing keys are filled. Idempotent.
            conn.execute(
                text("""
                UPDATE kirana_oltp.vertical_config SET features =
                    jsonb_build_object(
                        'vision',       vertical_code = 'grocery',
                        'appointments', vertical_code IN ('optical','services')
                    ) || features
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

            # ── Segment-wise subscription pricing ─────────────────────────────
            # Pricing varies per store_type (the granular 16-value dropdown from
            # onboarding — distinct from vertical_code above, which only drives
            # features/units/tax). '__default__' is the fallback row used for any
            # store_type with no dedicated price (e.g. fruits_vegetables, other).
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.segment_pricing (
                    store_type   TEXT PRIMARY KEY,
                    basic_price  NUMERIC(10,2) NOT NULL,
                    pro_price    NUMERIC(10,2) NOT NULL,
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            )
            # Seed once — DO NOTHING so a price edited later (SQL/admin) is never
            # silently reverted on the next boot.
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.segment_pricing (store_type, basic_price, pro_price) VALUES
                    ('kirana',           200, 500),
                    ('supermarket',      1300, 1700),
                    ('mini_supermarket', 600, 1000),
                    ('mono_brand',       600, 1000),
                    ('apparel',          500, 900),
                    ('boutique',         400, 800),
                    ('salon',            400, 600),
                    ('fancy_gift',       300, 500),
                    ('sports_fitness',   500, 900),
                    ('electronics',      700, 1100),
                    ('footwear',         400, 600),
                    ('optical',          400, 800),
                    ('bakery',           300, 500),
                    ('stationery',       200, 400),
                    ('__default__',      200, 500)
                ON CONFLICT (store_type) DO NOTHING
            """)
            )

            # ── Vertical-scoped categories ────────────────────────────────────
            # Each category belongs to a vertical so a mobile store doesn't see
            # grocery categories (and vice-versa). NULL = shared/all verticals.
            conn.execute(text(
                "ALTER TABLE kirana_oltp.category "
                "ADD COLUMN IF NOT EXISTS vertical_code TEXT"))
            # One-time backfill: the entire existing category set is grocery seed
            # data. Guard on "nothing tagged yet" so it runs ONCE — afterwards a
            # store's custom NULL category is never force-tagged grocery.
            conn.execute(text("""
                UPDATE kirana_oltp.category SET vertical_code = 'grocery'
                WHERE vertical_code IS NULL
                  AND NOT EXISTS (SELECT 1 FROM kirana_oltp.category
                                  WHERE vertical_code IS NOT NULL)
            """))
            # Seed a starter category set per non-grocery vertical (idempotent by
            # name+vertical). Owners can still add their own.
            conn.execute(text("""
                INSERT INTO kirana_oltp.category (name, vertical_code)
                SELECT v.name, v.vc FROM (VALUES
                    ('Mobiles','electronics'), ('Laptops & Computers','electronics'),
                    ('Audio','electronics'), ('Wearables','electronics'),
                    ('Cables & Chargers','electronics'), ('Power Banks','electronics'),
                    ('Memory & Storage','electronics'), ('Accessories','electronics'),
                    ('TV & Appliances','electronics'), ('Cameras','electronics'),
                    ('Men','apparel'), ('Women','apparel'), ('Kids','apparel'),
                    ('Innerwear','apparel'), ('Ethnic Wear','apparel'),
                    ('Winter Wear','apparel'), ('Accessories','apparel'),
                    ('Men''s Footwear','footwear'), ('Women''s Footwear','footwear'),
                    ('Kids'' Footwear','footwear'), ('Sports Shoes','footwear'),
                    ('Sandals & Slippers','footwear'), ('Formal Shoes','footwear'),
                    ('Eyeglasses','optical'), ('Sunglasses','optical'),
                    ('Contact Lenses','optical'), ('Lens Solutions','optical'),
                    ('Frames','optical'), ('Reading Glasses','optical'),
                    ('Hair','services'), ('Skin','services'), ('Spa','services'),
                    ('Nails','services'), ('Grooming','services'),
                    ('Gifts','general'), ('Stationery','general'), ('Toys','general'),
                    ('Home & Decor','general'), ('Party Supplies','general')
                ) AS v(name, vc)
                WHERE NOT EXISTS (
                    SELECT 1 FROM kirana_oltp.category c
                    WHERE c.name = v.name AND c.vertical_code = v.vc
                )
            """))
            # Starter products per non-grocery vertical, linked to the vertical's
            # categories, so a new store has a relevant catalog to pick from on
            # day one. Global catalog (no store scope). Idempotent by name+category.
            conn.execute(text("""
                INSERT INTO kirana_oltp.product (category_id, name, brand, unit)
                SELECT c.category_id, v.pname, v.brand, v.unit
                FROM (VALUES
                    ('electronics','Mobiles','Smartphone 64GB','Generic','pcs'),
                    ('electronics','Mobiles','Smartphone 128GB','Generic','pcs'),
                    ('electronics','Audio','Wired Earphones','Generic','pcs'),
                    ('electronics','Audio','Bluetooth Earbuds','Generic','pcs'),
                    ('electronics','Cables & Chargers','USB-C Cable 1m','Generic','pcs'),
                    ('electronics','Cables & Chargers','Fast Charger 20W','Generic','pcs'),
                    ('electronics','Power Banks','Power Bank 10000mAh','Generic','pcs'),
                    ('electronics','Accessories','Tempered Glass','Generic','pcs'),
                    ('electronics','Accessories','Phone Back Cover','Generic','pcs'),
                    ('electronics','Memory & Storage','microSD 64GB','Generic','pcs'),
                    ('apparel','Men','Mens T-Shirt','Generic','pcs'),
                    ('apparel','Men','Mens Jeans','Generic','pcs'),
                    ('apparel','Men','Mens Formal Shirt','Generic','pcs'),
                    ('apparel','Women','Womens Kurti','Generic','pcs'),
                    ('apparel','Women','Womens Leggings','Generic','pcs'),
                    ('apparel','Kids','Kids T-Shirt','Generic','pcs'),
                    ('apparel','Innerwear','Mens Vest','Generic','pcs'),
                    ('apparel','Winter Wear','Sweater','Generic','pcs'),
                    ('footwear','Sports Shoes','Running Shoes','Generic','pair'),
                    ('footwear','Sports Shoes','Casual Sneakers','Generic','pair'),
                    ('footwear','Sandals & Slippers','Flip Flops','Generic','pair'),
                    ('footwear','Sandals & Slippers','Sandals','Generic','pair'),
                    ('footwear','Formal Shoes','Formal Black Shoes','Generic','pair'),
                    ('optical','Eyeglasses','Single Vision Eyeglasses','Generic','pcs'),
                    ('optical','Sunglasses','Sunglasses UV400','Generic','pcs'),
                    ('optical','Contact Lenses','Monthly Contact Lenses','Generic','pair'),
                    ('optical','Lens Solutions','Lens Cleaning Solution 120ml','Generic','pcs'),
                    ('optical','Frames','Metal Frame','Generic','pcs'),
                    ('optical','Reading Glasses','Reading Glasses +1.5','Generic','pcs'),
                    ('services','Hair','Shampoo 200ml','Generic','pcs'),
                    ('services','Hair','Hair Serum 100ml','Generic','pcs'),
                    ('services','Skin','Face Wash 100ml','Generic','pcs'),
                    ('services','Grooming','Beard Oil 50ml','Generic','pcs'),
                    ('general','Gifts','Gift Box','Generic','pcs'),
                    ('general','Stationery','Notebook','Generic','pcs'),
                    ('general','Stationery','Pen Pack','Generic','pcs'),
                    ('general','Toys','Toy Car','Generic','pcs'),
                    ('general','Home & Decor','Wall Clock','Generic','pcs'),
                    ('general','Party Supplies','Balloons Pack','Generic','pcs')
                ) AS v(vc, catname, pname, brand, unit)
                JOIN kirana_oltp.category c ON c.name = v.catname AND c.vertical_code = v.vc
                WHERE NOT EXISTS (
                    SELECT 1 FROM kirana_oltp.product p
                    WHERE p.name = v.pname AND p.category_id = c.category_id
                )
            """))
            # Per-vertical UI copy (copyPack). Merge so existing keys are kept and
            # new keys backfilled. The app reads these with a fallback, so wording
            # adapts (e.g. "Search devices" instead of "Search products").
            conn.execute(text("""
                UPDATE kirana_oltp.vertical_config vc SET copy_pack = vc.copy_pack || d.cp::jsonb
                FROM (VALUES
                    ('grocery',    '{"add_title":"Add Product","search_hint":"Search products","item_plural":"products","empty_inventory":"No products in inventory"}'),
                    ('electronics','{"add_title":"Add Item","search_hint":"Search devices & accessories","item_plural":"items","empty_inventory":"No items in inventory"}'),
                    ('apparel',    '{"add_title":"Add Item","search_hint":"Search garments","item_plural":"items","empty_inventory":"No items in inventory"}'),
                    ('footwear',   '{"add_title":"Add Item","search_hint":"Search footwear","item_plural":"items","empty_inventory":"No items in inventory"}'),
                    ('optical',    '{"add_title":"Add Item","search_hint":"Search frames & lenses","item_plural":"items","empty_inventory":"No items in inventory"}'),
                    ('services',   '{"add_title":"Add Item","search_hint":"Search products","item_plural":"items","empty_inventory":"No items in inventory"}'),
                    ('general',    '{"add_title":"Add Item","search_hint":"Search products","item_plural":"items","empty_inventory":"No items in inventory"}')
                ) AS d(vertical_code, cp)
                WHERE vc.vertical_code = d.vertical_code
            """))
            # Trigram index for catalog search (name ILIKE '%q%' can't use a btree).
            # Fully guarded: CREATE EXTENSION needs privilege on managed PG, so we
            # swallow any failure — search still works (just without the index).
            conn.execute(text("""
                DO $$ BEGIN
                    CREATE EXTENSION IF NOT EXISTS pg_trgm;
                    CREATE INDEX IF NOT EXISTS idx_product_name_trgm
                        ON kirana_oltp.product USING gin (name gin_trgm_ops);
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
            """))

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
            # Tester #1 — axes can be scoped to a category so e.g. electronics
            # asks "Storage" for phones/laptops but "Capacity (mAh)" for power
            # banks and "Connectivity" for audio. '' (empty) = applies to the
            # whole vertical (model/colour). We widen the uniqueness from
            # (vertical, attr) to (vertical, attr, category) so the same attr_code
            # can exist once per category. Mirrors the inventory constraint
            # migration: drop the legacy 2-col unique by introspection, then add
            # the 3-col one. Idempotent.
            conn.execute(text(
                "ALTER TABLE kirana_oltp.product_attribute_def "
                "ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT ''"))
            conn.execute(text("""
                DO $$
                DECLARE c text;
                BEGIN
                    SELECT con.conname INTO c
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    JOIN pg_namespace ns ON ns.oid = rel.relnamespace
                    WHERE ns.nspname = 'kirana_oltp'
                      AND rel.relname = 'product_attribute_def'
                      AND con.contype = 'u'
                      AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text)
                           FROM unnest(con.conkey) k
                           JOIN pg_attribute att
                             ON att.attrelid = con.conrelid AND att.attnum = k)
                          = ARRAY['attr_code','vertical_code'];
                    IF c IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE kirana_oltp.product_attribute_def DROP CONSTRAINT %I', c);
                    END IF;
                END $$;
            """))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_attr_def_vertical_attr_category "
                "ON kirana_oltp.product_attribute_def (vertical_code, attr_code, category)"))
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
            # Migration: give every existing product one implicit variant
            # (grocery = one implicit variant rule). The implicit variant row
            # itself is bookkeeping only — per the comment above, inventory/
            # order_item must keep variant_id NULL for implicit products so
            # the update_inventory_on_sale trigger and the per-product stock
            # LATERAL (pos/crud.py) match them correctly. An earlier version
            # of this migration pointed inventory.variant_id/order_item.variant_id
            # AT the implicit variant's id instead of leaving them NULL, which
            # broke every sale of an implicit-variant product ("Inventory row
            # missing" — the trigger looks up variant_id IS NULL, found nothing).
            # The self-heal block below repairs any rows already corrupted by
            # that bug; do not reintroduce the old UPDATE ... SET variant_id =
            # v.variant_id (is_implicit) pattern.
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
                SET variant_id = NULL
                FROM kirana_oltp.product_variant v
                WHERE v.variant_id = inv.variant_id
                  AND v.is_implicit = TRUE
            """)
            )
            conn.execute(
                text("""
                UPDATE kirana_oltp.order_item oi
                SET variant_id = NULL
                FROM kirana_oltp.product_variant v
                WHERE v.variant_id = oi.variant_id
                  AND v.is_implicit = TRUE
            """)
            )
            # F2 — widen inventory uniqueness from (store_id, product_id) to
            # (store_id, product_id, variant_id) so each variant tracks its own
            # stock. COALESCE(variant_id, 0) keeps grocery's NULL/implicit rows
            # deduped. Drop the legacy 2-col constraint (the oltp upsert no longer
            # references it by name) and add a version-agnostic functional index.
            # Drop the legacy 2-col unique constraint regardless of its generated
            # name (find the UNIQUE constraint on exactly (store_id, product_id)).
            conn.execute(text("""
                DO $$
                DECLARE c text;
                BEGIN
                    SELECT con.conname INTO c
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    JOIN pg_namespace ns ON ns.oid = rel.relnamespace
                    WHERE ns.nspname = 'kirana_oltp' AND rel.relname = 'inventory'
                      AND con.contype = 'u'
                      AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text)
                           FROM unnest(con.conkey) k
                           JOIN pg_attribute att
                             ON att.attrelid = con.conrelid AND att.attnum = k)
                          = ARRAY['product_id','store_id'];
                    IF c IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE kirana_oltp.inventory DROP CONSTRAINT %I', c);
                    END IF;
                END $$;
            """))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_store_product_variant "
                "ON kirana_oltp.inventory (store_id, product_id, COALESCE(variant_id, 0))"))
            # Seed a per-variant inventory row for every real (non-implicit)
            # variant, taking the store from the product's existing inventory and
            # the qty from product_variant.stock. Idempotent (skips existing rows).
            conn.execute(text("""
                INSERT INTO kirana_oltp.inventory (store_id, product_id, variant_id, quantity)
                SELECT DISTINCT inv.store_id, v.product_id, v.variant_id,
                       COALESCE(v.stock, 0)::int
                FROM kirana_oltp.product_variant v
                JOIN kirana_oltp.inventory inv ON inv.product_id = v.product_id
                WHERE v.is_implicit = FALSE
                  AND NOT EXISTS (
                      SELECT 1 FROM kirana_oltp.inventory i2
                      WHERE i2.store_id = inv.store_id
                        AND i2.product_id = v.product_id
                        AND i2.variant_id = v.variant_id
                  )
            """))
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
                    ('electronics','colour','Colour','enum','["Black","White","Grey","Blue","Red","Green","Gold","Silver","Rose Gold","Graphite"]',TRUE,3),
                    ('optical','frame_size','Frame Size','text',NULL,TRUE,1),
                    ('optical','lens_type','Lens Type','enum','["single_vision","bifocal","progressive"]',TRUE,2),
                    ('optical','colour','Colour','enum','["Black","Brown","Blue","Gold","Silver","Grey","Tortoise","Rose Gold"]',TRUE,3)
                ON CONFLICT (vertical_code, attr_code, category) DO NOTHING
            """)
            )
            # Tester #1 — electronics axes that depend on the product category.
            # Storage applies to phones/laptops/memory; power banks ask mAh; audio
            # asks connectivity. Category is matched by name (the seeded category
            # names below). Idempotent via the 3-col unique index.
            conn.execute(text("""
                INSERT INTO kirana_oltp.product_attribute_def
                    (vertical_code, attr_code, label, type, options, is_variant_axis, sort, category)
                VALUES
                    ('electronics','storage','Storage','enum','["64GB","128GB","256GB","512GB","1TB"]',TRUE,2,'Mobiles'),
                    ('electronics','storage','Storage','enum','["128GB","256GB","512GB","1TB","2TB"]',TRUE,2,'Laptops & Computers'),
                    ('electronics','storage','Storage','enum','["64GB","128GB","256GB","512GB","1TB","2TB"]',TRUE,2,'Memory & Storage'),
                    ('electronics','capacity','Capacity (mAh)','enum','["5000mAh","10000mAh","20000mAh","30000mAh"]',TRUE,2,'Power Banks'),
                    ('electronics','connectivity','Connectivity','enum','["Wired","Bluetooth","TWS"]',TRUE,2,'Audio')
                ON CONFLICT (vertical_code, attr_code, category) DO NOTHING
            """))
            # Drop the legacy vertical-wide electronics 'storage' (category '')
            # now that storage is category-scoped, so phones don't also inherit a
            # second blanket Storage axis. Runs once; no-op thereafter.
            conn.execute(text("""
                DELETE FROM kirana_oltp.product_attribute_def
                WHERE vertical_code = 'electronics' AND attr_code = 'storage'
                  AND category = ''
            """))
            # Tester feedback (#6): colour was free-text, so the add-variant grid
            # showed a typing field instead of a drill-down. Upgrade every colour
            # axis still stored as plain text to an enum with a standard palette so
            # the FE renders a dropdown. Idempotent — only touches text rows with
            # no options, so a store that later customises the list is left alone.
            conn.execute(text("""
                UPDATE kirana_oltp.product_attribute_def
                SET type = 'enum',
                    options = '["Black","White","Grey","Blue","Red","Green","Yellow","Pink","Purple","Brown","Beige","Maroon","Navy","Orange","Gold","Silver"]'::jsonb
                WHERE attr_code = 'colour'
                  AND (type <> 'enum' OR options IS NULL)
            """))

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
            # Tester #11 — per-product warranty length (months). 0/NULL = none.
            # At sale, a serial's warranty_until is derived from this + the
            # purchase date; optical (no serial) shows it computed from the order.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS "
                    "warranty_months INT"
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

            # ── V2: services sellable at POS ───────────────────────────────
            # A service sells through the normal order pipeline as a flagged
            # product row (order_item FK, revenue, KPIs all just work), but it
            # carries no stock: the sale trigger skips inventory for
            # is_service products (see the CREATE OR REPLACE below).
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.product "
                    "ADD COLUMN IF NOT EXISTS is_service BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.service "
                    "ADD COLUMN IF NOT EXISTS product_id BIGINT "
                    "REFERENCES kirana_oltp.product(product_id)"
                )
            )
            # product.category_id is NOT NULL — service-linked products live
            # under one shared 'Services' category (vertical_code NULL =
            # visible to all verticals).
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.category (name, vertical_code)
                SELECT 'Services', NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM kirana_oltp.category
                    WHERE name = 'Services' AND vertical_code IS NULL
                )
            """)
            )
            # Re-assert the sale trigger fn WITH the is_service skip.
            # CREATE OR REPLACE is idempotent per boot; the canonical copy in
            # db_generation/ensure_full_schema.py is kept in sync.
            conn.execute(
                text("""
                CREATE OR REPLACE FUNCTION kirana_oltp.update_inventory_on_sale()
                RETURNS TRIGGER AS $$
                DECLARE
                    order_store_id BIGINT;
                    current_stock  INT;
                    is_real_variant BOOLEAN := FALSE;
                    is_service_row  BOOLEAN := FALSE;
                BEGIN
                    -- V2: services have no stock — skip inventory entirely.
                    SELECT COALESCE(is_service, FALSE) INTO is_service_row
                    FROM kirana_oltp.product WHERE product_id = NEW.product_id;
                    IF is_service_row THEN
                        RETURN NEW;
                    END IF;

                    SELECT store_id INTO order_store_id
                    FROM kirana_oltp.orders WHERE order_id = NEW.order_id;

                    -- F2: real (non-implicit) variants are decremented at the
                    -- application level (pos/crud.py), scoped to the exact
                    -- variant sold. Skip here to avoid double-decrementing.
                    IF NEW.variant_id IS NOT NULL THEN
                        SELECT NOT is_implicit INTO is_real_variant
                        FROM kirana_oltp.product_variant WHERE variant_id = NEW.variant_id;
                    END IF;

                    IF is_real_variant THEN
                        RETURN NEW;
                    END IF;

                    SELECT quantity INTO current_stock
                    FROM kirana_oltp.inventory
                    WHERE store_id = order_store_id AND product_id = NEW.product_id
                      AND variant_id IS NOT DISTINCT FROM NEW.variant_id
                    FOR UPDATE;

                    IF current_stock IS NULL THEN
                        RAISE EXCEPTION 'Inventory row missing for store %, product %', order_store_id, NEW.product_id;
                    END IF;

                    IF current_stock < NEW.quantity THEN
                        RAISE EXCEPTION 'Insufficient stock: available %, requested %', current_stock, NEW.quantity;
                    END IF;

                    UPDATE kirana_oltp.inventory
                    SET quantity = quantity - NEW.quantity
                    WHERE store_id = order_store_id AND product_id = NEW.product_id
                      AND variant_id IS NOT DISTINCT FROM NEW.variant_id;

                    INSERT INTO kirana_oltp.inventory_movements (store_id, product_id, change_quantity, reason, reference_id)
                    VALUES (order_store_id, NEW.product_id, -NEW.quantity, 'sale', NEW.order_id);

                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            )
            # Backfill: link legacy services to products. Self-limiting via
            # the product_id IS NULL guard, safe every boot; new services get
            # their linked product at creation time (services repo).
            conn.execute(
                text("""
                DO $$
                DECLARE r RECORD; pid BIGINT; cat BIGINT;
                BEGIN
                    SELECT category_id INTO cat FROM kirana_oltp.category
                    WHERE name = 'Services' AND vertical_code IS NULL LIMIT 1;
                    FOR r IN SELECT service_id, name FROM kirana_oltp.service
                             WHERE product_id IS NULL LOOP
                        INSERT INTO kirana_oltp.product (category_id, name, unit, is_service)
                        VALUES (cat, r.name, 'service', TRUE)
                        RETURNING product_id INTO pid;
                        UPDATE kirana_oltp.service SET product_id = pid
                        WHERE service_id = r.service_id;
                    END LOOP;
                END $$
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

            # ── Multi-store ownership: one user → many stores ─────────────────
            # users.store_id stays as the ACTIVE store pointer (read live by
            # get_user_by_token), so switching = updating that column. store_user
            # is the membership list the owner picks/switches/adds from.
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.store_user (
                    user_id    BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
                    store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
                    role       VARCHAR(20) NOT NULL DEFAULT 'owner',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, store_id)
                )
            """))
            # Backfill: every existing single-store user becomes a member of their
            # store. Idempotent (PK + ON CONFLICT). Safe every boot.
            conn.execute(text("""
                INSERT INTO kirana_oltp.store_user (user_id, store_id, role)
                SELECT user_id, store_id, 'owner'
                FROM kirana_oltp.users
                WHERE store_id IS NOT NULL
                ON CONFLICT (user_id, store_id) DO NOTHING
            """))

            # Auto-group multi-store owners (idempotent). An owner who runs 2+
            # stores gets one store_group; their ungrouped stores are assigned to
            # it. This powers the app's Store Comparison rollup without the admin
            # having to create groups by hand. New stores trigger the same via
            # store.ensure_owner_group().
            conn.execute(text("""
                INSERT INTO kirana_oltp.store_group (name, owner_user_id)
                SELECT COALESCE(u.full_name, u.username) || '''s stores', su.user_id
                FROM kirana_oltp.store_user su
                JOIN kirana_oltp.users u ON u.user_id = su.user_id
                WHERE su.role = 'owner'
                GROUP BY su.user_id, u.full_name, u.username
                HAVING COUNT(*) >= 2
                   AND NOT EXISTS (
                       SELECT 1 FROM kirana_oltp.store_group g
                       WHERE g.owner_user_id = su.user_id)
            """))
            conn.execute(text("""
                UPDATE kirana_oltp.store s SET group_id = g.group_id
                FROM kirana_oltp.store_group g
                JOIN kirana_oltp.store_user su
                  ON su.user_id = g.owner_user_id AND su.role = 'owner'
                WHERE su.store_id = s.store_id
                  AND s.group_id IS NULL AND NOT COALESCE(s.is_deleted, FALSE)
            """))

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
                # M5 — which staff member billed this order (optional), so sales +
                # commission can be attributed per staff member.
                "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS staff_id BIGINT",
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
            # Per-item detail for a return, written by the SAME transaction that
            # restocks / RTVs stock (inventory.record_return) — one source of truth,
            # so the Returns history shows what came back and where it went.
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.sales_return_item (
                    id          BIGSERIAL PRIMARY KEY,
                    return_id   BIGINT NOT NULL
                                    REFERENCES kirana_oltp.sales_return(return_id) ON DELETE CASCADE,
                    product_id  BIGINT,
                    name        VARCHAR(200),
                    qty         NUMERIC NOT NULL DEFAULT 1,
                    resaleable  BOOLEAN NOT NULL DEFAULT TRUE
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_sales_return_item_return "
                "ON kirana_oltp.sales_return_item(return_id)"
            ))

            # ── Module M3: Multi-location / multi-rack stock ──────────────────
            # Racks are first-class rows: the owner can pre-create shelf labels,
            # rename and merge them. label_key is the normalized identity
            # (case/space/punctuation-insensitive) so "A1", "a 1" and "A-1" all
            # name the same rack; label keeps the canonical display spelling.
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.rack (
                    rack_id    BIGSERIAL PRIMARY KEY,
                    store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    label      VARCHAR(60) NOT NULL,
                    label_key  VARCHAR(60) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (store_id, label_key)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_location (
                    id          BIGSERIAL PRIMARY KEY,
                    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
                    product_id  BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
                    variant_id  BIGINT,
                    rack        VARCHAR(60) NOT NULL,
                    rack_id     BIGINT REFERENCES kirana_oltp.rack(rack_id),
                    quantity    NUMERIC NOT NULL DEFAULT 0
                )
            """))
            conn.execute(text(
                "ALTER TABLE kirana_oltp.inventory_location "
                "ADD COLUMN IF NOT EXISTS rack_id BIGINT REFERENCES kirana_oltp.rack(rack_id)"))

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
                # F4 V_OP_1 — structured prescription dates so the renewal-due KPI
                # can compute who's due (optical eye-test / lens recall).
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS prescription_date DATE",
                "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS prescription_valid_months INT DEFAULT 12",
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
            # M9 POS deep-link: bill a finished job card into a sale.
            conn.execute(text(
                "ALTER TABLE kirana_oltp.job_card "
                "ADD COLUMN IF NOT EXISTS order_id BIGINT"))
            # Tester feedback: capture a ready-by TIME alongside the date so the
            # shopkeeper can promise "today 5pm", not just a calendar day.
            conn.execute(text(
                "ALTER TABLE kirana_oltp.job_card "
                "ADD COLUMN IF NOT EXISTS promised_time TIME"))

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

            # Racks become first-class (runs once): build rack rows from the
            # legacy free-text placement labels, folding case/space/punctuation
            # variants ("A1" / "a 1" / "A-1") into one rack, then merge the
            # duplicate placements the fold exposes. The SQL normalization here
            # must stay in sync with rack_label_key() in stocklocations.py.
            _racks_key = "racks_first_class_v1"
            already = conn.execute(
                text("SELECT 1 FROM kirana_oltp.app_migrations WHERE key = :k"),
                {"k": _racks_key},
            ).first()
            if not already:
                # One rack per normalized key; the first-seen spelling
                # (canonicalized to trimmed/single-spaced/uppercase) is kept
                # as the display label.
                conn.execute(text("""
                    INSERT INTO kirana_oltp.rack (store_id, label, label_key)
                    SELECT DISTINCT ON (store_id, label_key)
                           store_id, label, label_key
                    FROM (
                        SELECT store_id, id,
                               UPPER(REGEXP_REPLACE(BTRIM(rack), '\\s+', ' ', 'g')) AS label,
                               UPPER(REGEXP_REPLACE(rack, '[^[:alnum:]]+', '', 'g')) AS label_key
                        FROM kirana_oltp.inventory_location
                    ) t
                    WHERE label_key <> ''
                    ORDER BY store_id, label_key, id
                    ON CONFLICT (store_id, label_key) DO NOTHING
                """))
                conn.execute(text("""
                    UPDATE kirana_oltp.inventory_location il
                    SET rack_id = r.rack_id, rack = r.label
                    FROM kirana_oltp.rack r
                    WHERE il.rack_id IS NULL
                      AND r.store_id = il.store_id
                      AND r.label_key = UPPER(REGEXP_REPLACE(il.rack, '[^[:alnum:]]+', '', 'g'))
                """))
                # Labels that normalize to nothing (e.g. "--") land in UNSORTED.
                conn.execute(text("""
                    INSERT INTO kirana_oltp.rack (store_id, label, label_key)
                    SELECT DISTINCT store_id, 'UNSORTED', 'UNSORTED'
                    FROM kirana_oltp.inventory_location
                    WHERE rack_id IS NULL
                    ON CONFLICT (store_id, label_key) DO NOTHING
                """))
                conn.execute(text("""
                    UPDATE kirana_oltp.inventory_location il
                    SET rack_id = r.rack_id, rack = r.label
                    FROM kirana_oltp.rack r
                    WHERE il.rack_id IS NULL
                      AND r.store_id = il.store_id
                      AND r.label_key = 'UNSORTED'
                """))
                # Folding can leave the same product twice in one rack: keep the
                # oldest row with the summed quantity, drop the rest.
                conn.execute(text("""
                    UPDATE kirana_oltp.inventory_location il
                    SET quantity = agg.total
                    FROM (
                        SELECT MIN(id) AS keep_id, SUM(quantity) AS total
                        FROM kirana_oltp.inventory_location
                        GROUP BY store_id, product_id, COALESCE(variant_id, 0), rack_id
                        HAVING COUNT(*) > 1
                    ) agg
                    WHERE il.id = agg.keep_id
                """))
                conn.execute(text("""
                    DELETE FROM kirana_oltp.inventory_location il
                    USING (
                        SELECT id, MIN(id) OVER (
                            PARTITION BY store_id, product_id,
                                         COALESCE(variant_id, 0), rack_id
                        ) AS keep_id
                        FROM kirana_oltp.inventory_location
                    ) d
                    WHERE il.id = d.id AND d.id <> d.keep_id
                """))
                conn.execute(
                    text(
                        "INSERT INTO kirana_oltp.app_migrations(key) VALUES (:k) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": _racks_key},
                )

            # The old 4-column UNIQUE treated NULL variant_id rows as always
            # distinct (NULL <> NULL), so the placement upsert *inserted a
            # duplicate row* instead of updating for every non-variant product.
            # Replace it with a COALESCE-based unique index on the rack FK.
            conn.execute(text(
                "ALTER TABLE kirana_oltp.inventory_location DROP CONSTRAINT IF EXISTS "
                "inventory_location_store_id_product_id_variant_id_rack_key"))
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS inventory_location_placement_uniq
                ON kirana_oltp.inventory_location
                    (store_id, product_id, COALESCE(variant_id, 0), rack_id)
            """))

            # kirana_oltp.admin_settings
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS kirana_oltp.admin_settings (
                    key        VARCHAR(100) PRIMARY KEY,
                    value      TEXT         NOT NULL,
                    updated_at TIMESTAMPTZ  DEFAULT NOW()
                )
            """)
            )
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.admin_settings (key, value)
                VALUES ('auto_approve_trial', 'false')
                ON CONFLICT (key) DO NOTHING
            """)
            )

            # kirana_oltp.intelligence_log — widen the status CHECK to allow
            # 'internal' (in-app-only nudges past the daily FCM cap). Older DBs
            # were created before this status existed, so those inserts fail.
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.intelligence_log "
                    "DROP CONSTRAINT IF EXISTS intelligence_log_status_check"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE kirana_oltp.intelligence_log "
                    "ADD CONSTRAINT intelligence_log_status_check "
                    "CHECK (status IN ('sent','failed','opened','skipped','internal'))"
                )
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
