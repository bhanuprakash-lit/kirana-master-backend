"""
ensure_full_schema.py — Single authoritative schema script for kirana-master-backend.

Combines all schema from:
  - master_db_generation_script.py
  - upgrade_lit_db.py
  - v6_schema_extensions.py
  - kirana/repository.py (_ensure_schema)
  - pos/models.py (KiranaProduct, KiranaOrder)

Fully IDEMPOTENT — safe to run against a fresh DB or an existing one.
All statements use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

Usage (Azure) — set the DB_* environment variables first (never commit the
real password to source):
    $env:DB_HOST     = "psql-lohiya-kirana.postgres.database.azure.com"
    $env:DB_USER     = "psqladmin"
    $env:DB_PASSWORD = "<from Azure Key Vault>"
    $env:DB_NAME     = "db-kirana-dev"
    $env:DB_PORT     = "5432"
    python db_generation/ensure_full_schema.py
"""
import os
import sys
import psycopg2

DB_HOST     = os.environ.get("DB_HOST",     "localhost")
DB_USER     = os.environ.get("DB_USER",     "postgres")
# Local-dev fallback reads PGPASSWORD; never hardcode a password (SAST F01).
DB_PASSWORD = os.environ.get("DB_PASSWORD", os.environ.get("PGPASSWORD", ""))
DB_NAME     = os.environ.get("DB_NAME",     "lit_db")
DB_PORT     = os.environ.get("DB_PORT",     "5432")


# ── Each entry is (label, sql) ─────────────────────────────────────────────────
# Executed in order; if one fails it prints the error and continues.
STEPS = []

def step(label, sql):
    STEPS.append((label, sql.strip()))


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

step("schema:kirana_oltp", "CREATE SCHEMA IF NOT EXISTS kirana_oltp")
step("schema:kirana_olap", "CREATE SCHEMA IF NOT EXISTS kirana_olap")


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  BASE TABLES  (in FK-dependency order)
# ═══════════════════════════════════════════════════════════════════════════════

step("table:store", """
CREATE TABLE IF NOT EXISTS kirana_oltp.store (
    store_id     BIGSERIAL PRIMARY KEY,
    name         VARCHAR(150) NOT NULL,
    location     VARCHAR(255),
    region       VARCHAR(100),
    store_type   VARCHAR(100) DEFAULT 'kirana',
    footfall     INT,
    budget       NUMERIC,
    daily_budget NUMERIC,
    latitude     NUMERIC(10,7),
    longitude    NUMERIC(10,7),
    include_in_director BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted   BOOLEAN DEFAULT FALSE
)
""")

step("table:category", """
CREATE TABLE IF NOT EXISTS kirana_oltp.category (
    category_id        BIGSERIAL PRIMARY KEY,
    parent_category_id BIGINT REFERENCES kirana_oltp.category(category_id),
    name               VARCHAR(150) NOT NULL
)
""")

step("table:customer", """
CREATE TABLE IF NOT EXISTS kirana_oltp.customer (
    customer_id    BIGSERIAL PRIMARY KEY,
    store_id       BIGINT REFERENCES kirana_oltp.store(store_id),
    name           VARCHAR(150),
    phone          VARCHAR(20),
    email          VARCHAR(150),
    household_size INT DEFAULT 4,
    referral_count INT NOT NULL DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

step("table:users", """
CREATE TABLE IF NOT EXISTS kirana_oltp.users (
    user_id              BIGSERIAL PRIMARY KEY,
    username             VARCHAR(100) UNIQUE NOT NULL,
    email                VARCHAR(150),
    role                 VARCHAR(50),
    store_id             BIGINT REFERENCES kirana_oltp.store(store_id),
    full_name            VARCHAR(255) NOT NULL DEFAULT '',
    password_salt        VARCHAR(64),
    password_hash        VARCHAR(128),
    password_changed_at  TIMESTAMPTZ,
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    fcm_token            VARCHAR(255),
    phone_number         VARCHAR(20),
    firebase_uid         VARCHAR(128),
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted           BOOLEAN DEFAULT FALSE
)
""")

step("table:product", """
CREATE TABLE IF NOT EXISTS kirana_oltp.product (
    product_id       BIGSERIAL PRIMARY KEY,
    category_id      BIGINT NOT NULL REFERENCES kirana_oltp.category(category_id),
    name             VARCHAR(200) NOT NULL,
    brand            VARCHAR(100),
    unit             VARCHAR(20),
    weight           NUMERIC(10,2),
    is_loose         BOOLEAN DEFAULT FALSE,
    is_perishable    BOOLEAN DEFAULT FALSE,
    is_private_label BOOLEAN DEFAULT FALSE,
    sku              VARCHAR(100) UNIQUE,
    barcode          VARCHAR(100) UNIQUE,
    image_url        VARCHAR(500),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

step("table:supplier", """
CREATE TABLE IF NOT EXISTS kirana_oltp.supplier (
    supplier_id BIGSERIAL PRIMARY KEY,
    name        VARCHAR(150),
    contact     VARCHAR(150),
    phone       VARCHAR(20),
    category    VARCHAR(100),
    store_id    BIGINT REFERENCES kirana_oltp.store(store_id)
)
""")

step("table:product_supplier", """
CREATE TABLE IF NOT EXISTS kirana_oltp.product_supplier (
    id           BIGSERIAL PRIMARY KEY,
    product_id   BIGINT REFERENCES kirana_oltp.product(product_id),
    supplier_id  BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
    cost_price   NUMERIC(10,2) CHECK (cost_price >= 0),
    lead_time_days INT CHECK (lead_time_days >= 0)
)
""")

step("table:pricing", """
CREATE TABLE IF NOT EXISTS kirana_oltp.pricing (
    pricing_id BIGSERIAL PRIMARY KEY,
    product_id BIGINT REFERENCES kirana_oltp.product(product_id),
    store_id   BIGINT REFERENCES kirana_oltp.store(store_id),
    price      NUMERIC(10,2) CHECK (price >= 0),
    mrp        NUMERIC(10,2) CHECK (mrp >= 0),
    valid_from TIMESTAMP NOT NULL,
    valid_to   TIMESTAMP
)
""")

step("table:promotion", """
CREATE TABLE IF NOT EXISTS kirana_oltp.promotion (
    promotion_id     BIGSERIAL PRIMARY KEY,
    product_id       BIGINT REFERENCES kirana_oltp.product(product_id),
    store_id         BIGINT REFERENCES kirana_oltp.store(store_id),
    discount_percent NUMERIC(5,2) CHECK (discount_percent >= 0),
    start_date       TIMESTAMP,
    end_date         TIMESTAMP
)
""")

step("table:inventory", """
CREATE TABLE IF NOT EXISTS kirana_oltp.inventory (
    inventory_id BIGSERIAL PRIMARY KEY,
    store_id     BIGINT REFERENCES kirana_oltp.store(store_id),
    product_id   BIGINT REFERENCES kirana_oltp.product(product_id),
    variant_id   BIGINT REFERENCES kirana_oltp.product_variant(variant_id),
    quantity     INT DEFAULT 0 CHECK (quantity >= 0)
)
""")
# F2 — stock is unique per (store, product, variant). COALESCE(variant_id, 0)
# folds grocery's NULL/implicit variant into one bucket, so single-variant
# products still dedupe while real variants each get their own row. Functional
# index works on every PG version (no NULLS NOT DISTINCT dependency).
step("index:inventory_store_product_variant", """
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_store_product_variant
ON kirana_oltp.inventory (store_id, product_id, COALESCE(variant_id, 0))
""")

step("table:inventory_movements", """
CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_movements (
    movement_id     BIGSERIAL PRIMARY KEY,
    store_id        BIGINT REFERENCES kirana_oltp.store(store_id),
    product_id      BIGINT REFERENCES kirana_oltp.product(product_id),
    change_quantity INT,
    reason          VARCHAR(50),
    reference_id    BIGINT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

step("table:inventory_snapshots", """
CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_snapshots (
    snapshot_date DATE,
    store_id      BIGINT REFERENCES kirana_oltp.store(store_id),
    product_id    BIGINT REFERENCES kirana_oltp.product(product_id),
    stock_on_hand INT CHECK (stock_on_hand >= 0),
    upserted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, store_id, product_id)
)
""")

step("table:orders", """
CREATE TABLE IF NOT EXISTS kirana_oltp.orders (
    order_id      BIGSERIAL PRIMARY KEY,
    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    user_id       BIGINT REFERENCES kirana_oltp.users(user_id),
    customer_id   BIGINT REFERENCES kirana_oltp.customer(customer_id),
    order_status  VARCHAR(50) DEFAULT 'completed',
    order_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_amount  NUMERIC(12,2) CHECK (total_amount >= 0),
    udhaar_amount NUMERIC(12,2),
    cash_paid     NUMERIC(12,2),
    order_channel VARCHAR(20) DEFAULT 'walk_in',
    basket_id      BIGINT,
    basket_name    VARCHAR(255),
    basket_gross   NUMERIC(12,2),
    basket_savings NUMERIC(12,2)
)
""")

step("table:order_item", """
CREATE TABLE IF NOT EXISTS kirana_oltp.order_item (
    order_item_id BIGSERIAL PRIMARY KEY,
    order_id      BIGINT NOT NULL REFERENCES kirana_oltp.orders(order_id) ON DELETE CASCADE,
    product_id    BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
    quantity      NUMERIC,
    unit_price    NUMERIC(10,2) CHECK (unit_price >= 0),
    cost_price    NUMERIC(10,2) CHECK (cost_price >= 0)
)
""")

step("table:payments", """
CREATE TABLE IF NOT EXISTS kirana_oltp.payments (
    payment_id     BIGSERIAL PRIMARY KEY,
    order_id       BIGINT REFERENCES kirana_oltp.orders(order_id),
    amount         NUMERIC(10,2),
    payment_method VARCHAR(50),
    status         VARCHAR(50) DEFAULT 'paid',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

step("table:purchases", """
CREATE TABLE IF NOT EXISTS kirana_oltp.purchases (
    purchase_id    BIGSERIAL PRIMARY KEY,
    supplier_id    BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
    store_id       BIGINT REFERENCES kirana_oltp.store(store_id),
    order_date     TIMESTAMP,
    arrival_date   TIMESTAMP,
    status         VARCHAR(50),
    total_amount   NUMERIC(12,2),
    due_date       DATE,
    payment_status VARCHAR(20) DEFAULT 'unpaid',
    notes          VARCHAR(255)
)
""")

step("table:purchase_items", """
CREATE TABLE IF NOT EXISTS kirana_oltp.purchase_items (
    purchase_item_id BIGSERIAL PRIMARY KEY,
    purchase_id      BIGINT REFERENCES kirana_oltp.purchases(purchase_id) ON DELETE CASCADE,
    product_id       BIGINT REFERENCES kirana_oltp.product(product_id),
    quantity         INT CHECK (quantity > 0),
    cost_price       NUMERIC(10,2),
    requested_qty    INT
)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  AUTH / APP TABLES  (from repository.py)
# ═══════════════════════════════════════════════════════════════════════════════

step("table:user_sessions", """
CREATE TABLE IF NOT EXISTS kirana_oltp.user_sessions (
    session_id   BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
    access_token VARCHAR(128) UNIQUE NOT NULL,
    login_method VARCHAR(20) DEFAULT 'password',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at   TIMESTAMPTZ
)
""")

step("table:user_prefs", """
CREATE TABLE IF NOT EXISTS kirana_oltp.user_prefs (
    user_id                   BIGINT PRIMARY KEY REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
    forecast_horizon_days     INT     NOT NULL DEFAULT 7,
    alert_stockout_threshold  REAL    NOT NULL DEFAULT 0.5,
    alert_min_velocity        REAL    NOT NULL DEFAULT 0.3,
    alert_reorder_days        INT     NOT NULL DEFAULT 3,
    alert_dead_stock_days     INT     NOT NULL DEFAULT 21,
    alert_expiry_days         INT     NOT NULL DEFAULT 7,
    notify_whatsapp           BOOLEAN NOT NULL DEFAULT FALSE,
    notify_in_app             BOOLEAN NOT NULL DEFAULT TRUE,
    quiet_hours_start         INT     NOT NULL DEFAULT 22,
    quiet_hours_end           INT     NOT NULL DEFAULT 7,
    subscribed_kpis           TEXT,
    allow_social_marketing    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")

step("table:user_fcm_tokens", """
CREATE TABLE IF NOT EXISTS kirana_oltp.user_fcm_tokens (
    token_id   BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
    fcm_token  VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_fcm_tokens_token UNIQUE (fcm_token)
)
""")

step("table:app_activity", """
CREATE TABLE IF NOT EXISTS kirana_oltp.app_activity (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
    event        VARCHAR(20) NOT NULL,
    duration_sec INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")

step("table:issue_report", """
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

step("table:cashflow_requests", """
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

step("table:basket", """
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

step("table:basket_item", """
CREATE TABLE IF NOT EXISTS kirana_oltp.basket_item (
    id           BIGSERIAL PRIMARY KEY,
    basket_id    BIGINT NOT NULL REFERENCES kirana_oltp.basket(basket_id) ON DELETE CASCADE,
    product_id   BIGINT NOT NULL,
    product_name VARCHAR(255),
    qty          NUMERIC NOT NULL DEFAULT 1
)
""")

step("table:vision_session", """
CREATE TABLE IF NOT EXISTS kirana_oltp.vision_session (
    session_id    BIGSERIAL PRIMARY KEY,
    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    session_type  VARCHAR(20) NOT NULL,
    session_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    image_url     TEXT,
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    total_skus    INT NOT NULL DEFAULT 0,
    total_units   INT NOT NULL DEFAULT 0,
    unknown_count INT NOT NULL DEFAULT 0,
    error         TEXT,
    committed_at  TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
step("col:vision_session.committed_at",
     "ALTER TABLE kirana_oltp.vision_session ADD COLUMN IF NOT EXISTS committed_at TIMESTAMPTZ")
step("col:vision_session.finished_at",
     "ALTER TABLE kirana_oltp.vision_session ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ")

step("table:vision_item", """
CREATE TABLE IF NOT EXISTS kirana_oltp.vision_item (
    item_id              BIGSERIAL PRIMARY KEY,
    session_id           BIGINT NOT NULL REFERENCES kirana_oltp.vision_session(session_id) ON DELETE CASCADE,
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
step("col:vision_item.image_index",
     "ALTER TABLE kirana_oltp.vision_item ADD COLUMN IF NOT EXISTS image_index SMALLINT NOT NULL DEFAULT 0")
step("col:vision_item.detector_source",
     "ALTER TABLE kirana_oltp.vision_item ADD COLUMN IF NOT EXISTS detector_source VARCHAR(16) NOT NULL DEFAULT 'gemini'")

step("table:counter_session", """
CREATE TABLE IF NOT EXISTS kirana_oltp.counter_session (
    session_id   BIGSERIAL PRIMARY KEY,
    store_id     BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    client_uid   VARCHAR(64) NOT NULL,
    session_date DATE NOT NULL DEFAULT CURRENT_DATE,
    device_label VARCHAR(120),
    source       VARCHAR(30) NOT NULL DEFAULT 'on_device',
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    total_units  INT NOT NULL DEFAULT 0,
    total_skus   INT NOT NULL DEFAULT 0,
    unknown_count INT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, client_uid)
)
""")

step("table:counter_item", """
CREATE TABLE IF NOT EXISTS kirana_oltp.counter_item (
    item_id        BIGSERIAL PRIMARY KEY,
    session_id     BIGINT NOT NULL REFERENCES kirana_oltp.counter_session(session_id) ON DELETE CASCADE,
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

# ── Call center / tele-calling ────────────────────────────────────────────────
step("table:call_executive", """
CREATE TABLE IF NOT EXISTS kirana_oltp.call_executive (
    executive_id  BIGSERIAL PRIMARY KEY,
    username      VARCHAR(100) UNIQUE NOT NULL,
    full_name     VARCHAR(255) NOT NULL,
    phone         VARCHAR(20),
    email         VARCHAR(255),
    role          VARCHAR(20) NOT NULL DEFAULT 'call_executive',
    password_salt VARCHAR(64),
    password_hash VARCHAR(128),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
step("table:call_executive_session", """
CREATE TABLE IF NOT EXISTS kirana_oltp.call_executive_session (
    session_id   BIGSERIAL PRIMARY KEY,
    executive_id BIGINT NOT NULL REFERENCES kirana_oltp.call_executive(executive_id) ON DELETE CASCADE,
    access_token VARCHAR(128) UNIQUE NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at   TIMESTAMPTZ
)
""")
step("idx:call_exec_session_token",
     "CREATE INDEX IF NOT EXISTS idx_call_exec_session_token "
     "ON kirana_oltp.call_executive_session(access_token)")
step("table:store_assignment", """
CREATE TABLE IF NOT EXISTS kirana_oltp.store_assignment (
    assignment_id BIGSERIAL PRIMARY KEY,
    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    executive_id  BIGINT NOT NULL REFERENCES kirana_oltp.call_executive(executive_id) ON DELETE CASCADE,
    assigned_by   BIGINT,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status        VARCHAR(20) NOT NULL DEFAULT 'active',
    priority      SMALLINT NOT NULL DEFAULT 0
)
""")
step("idx:store_assignment_active",
     "CREATE UNIQUE INDEX IF NOT EXISTS uidx_store_assignment_active "
     "ON kirana_oltp.store_assignment(store_id) WHERE status = 'active'")
step("idx:store_assignment_exec",
     "CREATE INDEX IF NOT EXISTS idx_store_assignment_exec "
     "ON kirana_oltp.store_assignment(executive_id, status)")
step("table:call_log", """
CREATE TABLE IF NOT EXISTS kirana_oltp.call_log (
    call_id          BIGSERIAL PRIMARY KEY,
    store_id         BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    executive_id     BIGINT NOT NULL REFERENCES kirana_oltp.call_executive(executive_id),
    called_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered         BOOLEAN,
    disposition      VARCHAR(24) NOT NULL,
    app_usage_status VARCHAR(24),
    feedback_text    TEXT,
    sentiment        VARCHAR(12),
    rating           SMALLINT,
    next_action      VARCHAR(16),
    callback_at      TIMESTAMPTZ,
    duration_sec     INT,
    recording_url    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
step("idx:call_log_store",
     "CREATE INDEX IF NOT EXISTS idx_call_log_store ON kirana_oltp.call_log(store_id, called_at DESC)")
step("idx:call_log_exec",
     "CREATE INDEX IF NOT EXISTS idx_call_log_exec ON kirana_oltp.call_log(executive_id, called_at DESC)")
step("idx:call_log_callback",
     "CREATE INDEX IF NOT EXISTS idx_call_log_callback "
     "ON kirana_oltp.call_log(callback_at) WHERE next_action = 'callback'")
step("table:call_feedback_tag", """
CREATE TABLE IF NOT EXISTS kirana_oltp.call_feedback_tag (
    id      BIGSERIAL PRIMARY KEY,
    call_id BIGINT NOT NULL REFERENCES kirana_oltp.call_log(call_id) ON DELETE CASCADE,
    tag     VARCHAR(24) NOT NULL
)
""")
step("idx:call_feedback_tag_call",
     "CREATE INDEX IF NOT EXISTS idx_call_feedback_tag_call ON kirana_oltp.call_feedback_tag(call_id)")

step("table:ai_usage", """
CREATE TABLE IF NOT EXISTS kirana_oltp.ai_usage (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
    store_id    BIGINT NOT NULL DEFAULT 0,
    feature     VARCHAR(20) NOT NULL,
    usage_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    count       INT NOT NULL DEFAULT 0,
    UNIQUE (user_id, store_id, feature, usage_date)
)
""")

step("table:ai_credits", """
CREATE TABLE IF NOT EXISTS kirana_oltp.ai_credits (
    id       BIGSERIAL PRIMARY KEY,
    user_id  BIGINT NOT NULL REFERENCES kirana_oltp.users(user_id) ON DELETE CASCADE,
    feature  VARCHAR(20) NOT NULL,
    balance  INT NOT NULL DEFAULT 0 CHECK (balance >= 0),
    UNIQUE (user_id, feature)
)
""")

step("table:kpi_tier_config", """
CREATE TABLE IF NOT EXISTS kirana_oltp.kpi_tier_config (
    kpi_id        TEXT PRIMARY KEY,
    required_tier TEXT NOT NULL DEFAULT 'basic'
        CHECK (required_tier IN ('basic', 'pro')),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  SUBSCRIPTION  (unified — from v6 + repository additions)
# ═══════════════════════════════════════════════════════════════════════════════

step("table:subscription", """
CREATE TABLE IF NOT EXISTS kirana_oltp.subscription (
    subscription_id BIGSERIAL PRIMARY KEY,
    store_id        BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    tier            VARCHAR(40) NOT NULL,
    monthly_price   NUMERIC(10,2) NOT NULL DEFAULT 0,
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMP,
    renewal_count   INT NOT NULL DEFAULT 0,
    savings_to_date NUMERIC(12,2) NOT NULL DEFAULT 0,
    is_trial        BOOLEAN NOT NULL DEFAULT FALSE,
    trial_ends_at   TIMESTAMP,
    trial_tier      VARCHAR(40),
    requested_tier  VARCHAR(40),
    UNIQUE (store_id)
)
""")

# Segment-wise subscription pricing — keyed by store.store_type (the granular
# onboarding dropdown), NOT vertical_code. '__default__' is the fallback row
# for any store_type with no dedicated price. Mirrors the table created in
# kirana/repositories/base.py:_ensure_schema() (the live boot path) — kept in
# sync here for manual runs against Azure.
step("table:segment_pricing", """
CREATE TABLE IF NOT EXISTS kirana_oltp.segment_pricing (
    store_type   TEXT PRIMARY KEY,
    basic_price  NUMERIC(10,2) NOT NULL,
    pro_price    NUMERIC(10,2) NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")

step("seed:segment_pricing", """
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


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  INTELLIGENCE / CART
# ═══════════════════════════════════════════════════════════════════════════════

step("table:intelligence_log", """
CREATE TABLE IF NOT EXISTS kirana_oltp.intelligence_log (
    id           BIGSERIAL PRIMARY KEY,
    store_id     INTEGER NOT NULL,
    user_id      INTEGER,
    trigger_type VARCHAR(50) NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    payload      JSONB NOT NULL DEFAULT '{}',
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opened_at    TIMESTAMPTZ,
    status       VARCHAR(20) NOT NULL DEFAULT 'sent'
        CHECK (status IN ('sent','failed','opened','skipped','internal'))
)
""")

# Migration: existing DBs were created with a status CHECK that predates the
# 'internal' status (in-app-only nudges past the daily FCM cap). Widen the
# constraint in place so those inserts stop failing.
step("migrate:intelligence_log_status_internal", """
ALTER TABLE kirana_oltp.intelligence_log
    DROP CONSTRAINT IF EXISTS intelligence_log_status_check;
ALTER TABLE kirana_oltp.intelligence_log
    ADD CONSTRAINT intelligence_log_status_check
    CHECK (status IN ('sent','failed','opened','skipped','internal'));
""")

step("table:cart_session", """
CREATE TABLE IF NOT EXISTS kirana_oltp.cart_session (
    store_id     INTEGER PRIMARY KEY,
    item_count   INTEGER NOT NULL DEFAULT 0,
    cart_data    JSONB NOT NULL DEFAULT '[]',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notified_at  TIMESTAMPTZ,
    converted_at TIMESTAMPTZ
)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  STORE ASSOCIATIONS
# ═══════════════════════════════════════════════════════════════════════════════

step("table:store_association", """
CREATE TABLE IF NOT EXISTS kirana_oltp.store_association (
    association_id       SERIAL PRIMARY KEY,
    store_id             INTEGER NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    area_type            TEXT NOT NULL
        CHECK (area_type IN ('apartment','hostel','school','office','colony')),
    estimated_households INTEGER,
    notes                TEXT,
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMP NOT NULL DEFAULT NOW()
)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  KHATA (udhaar / credit) — must precede khata_payments
# ═══════════════════════════════════════════════════════════════════════════════

step("table:khata", """
CREATE TABLE IF NOT EXISTS kirana_oltp.khata (
    khata_id    BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    order_id    BIGINT REFERENCES kirana_oltp.orders(order_id),
    amount      NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    amount_paid NUMERIC(12,2) NOT NULL DEFAULT 0,
    issue_date  DATE NOT NULL,
    due_date    DATE NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'open'
)
""")

step("table:khata_payments", """
CREATE TABLE IF NOT EXISTS kirana_oltp.khata_payments (
    payment_id BIGSERIAL PRIMARY KEY,
    khata_id   BIGINT NOT NULL REFERENCES kirana_oltp.khata(khata_id) ON DELETE CASCADE,
    store_id   BIGINT NOT NULL,
    amount     NUMERIC NOT NULL,
    paid_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes      TEXT
)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  REFERRAL SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

step("table:referral_campaigns", """
CREATE TABLE IF NOT EXISTS kirana_oltp.referral_campaigns (
    campaign_id                BIGSERIAL PRIMARY KEY,
    store_id                   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    name                       VARCHAR(100) NOT NULL,
    referral_discount_pct      NUMERIC(5,2) NOT NULL DEFAULT 10,
    milestone_every_n          INT NOT NULL DEFAULT 10,
    milestone_reward_pct       NUMERIC(5,2) NOT NULL DEFAULT 5,
    max_referrals_per_referrer INT NOT NULL DEFAULT 50,
    is_active                  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")

step("table:referral_tokens", """
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

step("table:referrals", """
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

step("table:referral_vouchers", """
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


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  KPI EXTENSION TABLES  (from v6_schema_extensions.py)
# ═══════════════════════════════════════════════════════════════════════════════

step("table:footfall", """
CREATE TABLE IF NOT EXISTS kirana_oltp.footfall (
    footfall_id BIGSERIAL PRIMARY KEY,
    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    ts          TIMESTAMP NOT NULL,
    hour        INT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    visitors    INT NOT NULL CHECK (visitors >= 0),
    UNIQUE (store_id, ts)
)
""")

step("table:scheme", """
CREATE TABLE IF NOT EXISTS kirana_oltp.scheme (
    scheme_id   BIGSERIAL PRIMARY KEY,
    supplier_id BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
    product_id  BIGINT REFERENCES kirana_oltp.product(product_id),
    name        VARCHAR(150) NOT NULL,
    scheme_type VARCHAR(40) NOT NULL,
    value       NUMERIC(12,2) NOT NULL DEFAULT 0,
    min_qty     INT NOT NULL DEFAULT 1,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL
)
""")

step("table:scheme_claim", """
CREATE TABLE IF NOT EXISTS kirana_oltp.scheme_claim (
    claim_id    BIGSERIAL PRIMARY KEY,
    scheme_id   BIGINT NOT NULL REFERENCES kirana_oltp.scheme(scheme_id) ON DELETE CASCADE,
    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    purchase_id BIGINT REFERENCES kirana_oltp.purchases(purchase_id),
    claim_date  DATE NOT NULL,
    amount_saved NUMERIC(12,2) NOT NULL DEFAULT 0,
    status      VARCHAR(20) NOT NULL DEFAULT 'claimed'
)
""")

step("table:calendar", """
CREATE TABLE IF NOT EXISTS kirana_oltp.calendar (
    cal_date DATE PRIMARY KEY,
    festival VARCHAR(100),
    weight   NUMERIC(4,2) NOT NULL DEFAULT 1.0
)
""")

step("table:inventory_batch", """
CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_batch (
    batch_id          BIGSERIAL PRIMARY KEY,
    store_id          BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    product_id        BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
    batch_no          VARCHAR(60),
    manufactured_date DATE,
    expiry_date       DATE NOT NULL,
    qty_in_stock      INT NOT NULL DEFAULT 0 CHECK (qty_in_stock >= 0),
    markdown_pct      NUMERIC(5,2) DEFAULT 0,
    recovered_units   INT DEFAULT 0,
    wasted_units      INT DEFAULT 0,
    UNIQUE (store_id, product_id, batch_no)
)
""")

step("table:shelf_planogram", """
CREATE TABLE IF NOT EXISTS kirana_oltp.shelf_planogram (
    plano_id   BIGSERIAL PRIMARY KEY,
    store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    product_id BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
    shelf_id   VARCHAR(40) NOT NULL,
    sq_ft      NUMERIC(6,2) NOT NULL CHECK (sq_ft > 0),
    eye_level  BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (store_id, product_id)
)
""")

step("table:opex", """
CREATE TABLE IF NOT EXISTS kirana_oltp.opex (
    opex_id     BIGSERIAL PRIMARY KEY,
    store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    month_start DATE NOT NULL,
    electricity NUMERIC(12,2) DEFAULT 0,
    rent        NUMERIC(12,2) DEFAULT 0,
    staff       NUMERIC(12,2) DEFAULT 0,
    other       NUMERIC(12,2) DEFAULT 0,
    UNIQUE (store_id, month_start)
)
""")

step("table:return_to_vendor", """
CREATE TABLE IF NOT EXISTS kirana_oltp.return_to_vendor (
    rtv_id           BIGSERIAL PRIMARY KEY,
    store_id         BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
    supplier_id      BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
    product_id       BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
    return_date      DATE NOT NULL,
    qty_returned     INT NOT NULL CHECK (qty_returned > 0),
    unit_cost        NUMERIC(10,2) NOT NULL DEFAULT 0,
    recovery_pct     NUMERIC(5,2) NOT NULL DEFAULT 0,
    amount_recovered NUMERIC(12,2) NOT NULL DEFAULT 0,
    reason           VARCHAR(60)
)
""")

step("table:marketing_spend", """
CREATE TABLE IF NOT EXISTS kirana_oltp.marketing_spend (
    spend_id             BIGSERIAL PRIMARY KEY,
    store_id             BIGINT REFERENCES kirana_oltp.store(store_id),
    spend_date           DATE NOT NULL,
    channel              VARCHAR(40) NOT NULL,
    amount               NUMERIC(12,2) NOT NULL,
    attributed_customers INT NOT NULL DEFAULT 0
)
""")



# ═══════════════════════════════════════════════════════════════════════════════
# 10.  OLAP TABLES
# ═══════════════════════════════════════════════════════════════════════════════

step("olap:daily_store_sku_metrics", """
CREATE TABLE IF NOT EXISTS kirana_olap.daily_store_sku_metrics (
    date              DATE NOT NULL,
    store_id          BIGINT,
    product_id        BIGINT,
    units_sold        INT,
    revenue           NUMERIC(12,2),
    profit            NUMERIC(12,2),
    stock_on_hand     INT,
    lost_sales        INT,
    price             NUMERIC(10,2),
    discount          NUMERIC(5,2),
    promo_flag        BOOLEAN,
    avg_selling_price NUMERIC(10,2),
    margin            NUMERIC(5,2),
    weather_temp      NUMERIC(5,2),
    rain_flag         BOOLEAN,
    PRIMARY KEY (date, store_id, product_id)
) PARTITION BY RANGE (date)
""")

step("olap:daily_metrics_default_partition", """
CREATE TABLE IF NOT EXISTS kirana_olap.daily_metrics_default
PARTITION OF kirana_olap.daily_store_sku_metrics DEFAULT
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 11.  ADD MISSING COLUMNS TO EXISTING TABLES (idempotent ALTER TABLE)
# ═══════════════════════════════════════════════════════════════════════════════

# These cover cases where the table already existed in Azure DB without some columns.

column_patches = [
    ("product.image_url",         "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)"),
    ("product.is_private_label",  "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS is_private_label BOOLEAN DEFAULT FALSE"),
    ("product.brand",             "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS brand VARCHAR(100)"),
    ("product.unit",              "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS unit VARCHAR(20)"),
    ("product.weight",            "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS weight NUMERIC(10,2)"),
    ("product.is_loose",          "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS is_loose BOOLEAN DEFAULT FALSE"),
    ("product.is_perishable",     "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS is_perishable BOOLEAN DEFAULT FALSE"),

    ("orders.order_status",       "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS order_status VARCHAR(50) DEFAULT 'completed'"),
    ("orders.customer_id",        "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS customer_id BIGINT"),
    ("orders.udhaar_amount",      "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS udhaar_amount NUMERIC(12,2)"),
    ("orders.cash_paid",          "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS cash_paid NUMERIC(12,2)"),
    ("orders.order_channel",      "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS order_channel VARCHAR(20) DEFAULT 'walk_in'"),
    ("orders.basket_id",          "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_id BIGINT"),
    ("orders.basket_name",        "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_name VARCHAR(255)"),
    ("orders.basket_gross",       "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_gross NUMERIC(12,2)"),
    ("orders.basket_savings",     "ALTER TABLE kirana_oltp.orders ADD COLUMN IF NOT EXISTS basket_savings NUMERIC(12,2)"),

    ("users.full_name",           "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS full_name VARCHAR(255) NOT NULL DEFAULT ''"),
    ("users.password_salt",       "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS password_salt VARCHAR(64)"),
    ("users.password_hash",       "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128)"),
    ("users.password_changed_at", "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ"),
    ("users.is_active",           "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"),
    ("users.fcm_token",           "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS fcm_token VARCHAR(255)"),
    ("users.phone_number",        "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20)"),
    ("users.firebase_uid",        "ALTER TABLE kirana_oltp.users ADD COLUMN IF NOT EXISTS firebase_uid VARCHAR(128)"),

    ("store.store_type",          "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS store_type VARCHAR(100) DEFAULT 'kirana'"),
    ("store.footfall",            "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS footfall INT"),
    ("store.budget",              "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS budget NUMERIC"),
    ("store.daily_budget",        "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS daily_budget NUMERIC"),
    ("store.latitude",            "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS latitude NUMERIC(10,7)"),
    ("store.longitude",           "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS longitude NUMERIC(10,7)"),
    ("store.include_in_director", "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS include_in_director BOOLEAN NOT NULL DEFAULT TRUE"),

    ("supplier.store_id",         "ALTER TABLE kirana_oltp.supplier ADD COLUMN IF NOT EXISTS store_id BIGINT REFERENCES kirana_oltp.store(store_id)"),
    ("supplier.phone",            "ALTER TABLE kirana_oltp.supplier ADD COLUMN IF NOT EXISTS phone VARCHAR(20)"),
    ("supplier.category",         "ALTER TABLE kirana_oltp.supplier ADD COLUMN IF NOT EXISTS category VARCHAR(100)"),

    ("pricing.mrp",               "ALTER TABLE kirana_oltp.pricing ADD COLUMN IF NOT EXISTS mrp NUMERIC(10,2)"),

    ("purchases.total_amount",    "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS total_amount NUMERIC(12,2)"),
    ("purchases.due_date",        "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS due_date DATE"),
    ("purchases.payment_status",  "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS payment_status VARCHAR(20) DEFAULT 'unpaid'"),
    ("purchases.notes",           "ALTER TABLE kirana_oltp.purchases ADD COLUMN IF NOT EXISTS notes VARCHAR(255)"),

    ("purchase_items.requested_qty", "ALTER TABLE kirana_oltp.purchase_items ADD COLUMN IF NOT EXISTS requested_qty INT"),

    ("customer.store_id",         "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS store_id BIGINT REFERENCES kirana_oltp.store(store_id)"),
    ("customer.household_size",   "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS household_size INT DEFAULT 4"),
    ("customer.referral_count",   "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS referral_count INT NOT NULL DEFAULT 0"),
    ("customer.association_id",   "ALTER TABLE kirana_oltp.customer ADD COLUMN IF NOT EXISTS association_id INTEGER REFERENCES kirana_oltp.store_association(association_id) ON DELETE SET NULL"),

    ("subscription.is_trial",     "ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT FALSE"),
    ("subscription.trial_ends_at","ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP"),
    ("subscription.trial_tier",   "ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS trial_tier VARCHAR(40)"),
    ("subscription.requested_tier","ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS requested_tier VARCHAR(40)"),
    ("subscription.monthly_price","ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS monthly_price NUMERIC(10,2) NOT NULL DEFAULT 0"),
    ("subscription.renewal_count","ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS renewal_count INT NOT NULL DEFAULT 0"),
    ("subscription.savings_to_date","ALTER TABLE kirana_oltp.subscription ADD COLUMN IF NOT EXISTS savings_to_date NUMERIC(12,2) NOT NULL DEFAULT 0"),

    ("inventory_snapshots.upserted_at", "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS upserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"),

    ("user_sessions.login_method","ALTER TABLE kirana_oltp.user_sessions ADD COLUMN IF NOT EXISTS login_method VARCHAR(20) DEFAULT 'password'"),

    ("user_prefs.subscribed_kpis","ALTER TABLE kirana_oltp.user_prefs ADD COLUMN IF NOT EXISTS subscribed_kpis TEXT"),
    ("user_prefs.allow_social_marketing","ALTER TABLE kirana_oltp.user_prefs ADD COLUMN IF NOT EXISTS allow_social_marketing BOOLEAN NOT NULL DEFAULT FALSE"),
    ("user_prefs.alert_expiry_days","ALTER TABLE kirana_oltp.user_prefs ADD COLUMN IF NOT EXISTS alert_expiry_days INT NOT NULL DEFAULT 7"),

    ("referral_campaigns.max_referrals","ALTER TABLE kirana_oltp.referral_campaigns ADD COLUMN IF NOT EXISTS max_referrals_per_referrer INT NOT NULL DEFAULT 50"),

    ("khata.order_id",           "ALTER TABLE kirana_oltp.khata ADD COLUMN IF NOT EXISTS order_id BIGINT REFERENCES kirana_oltp.orders(order_id)"),
    ("khata.due_date",           "ALTER TABLE kirana_oltp.khata ADD COLUMN IF NOT EXISTS due_date DATE"),
    ("khata.notes",              "ALTER TABLE kirana_oltp.khata ADD COLUMN IF NOT EXISTS notes TEXT"),

    ("order_item.quantity_numeric","ALTER TABLE kirana_oltp.order_item ALTER COLUMN quantity TYPE NUMERIC USING quantity::NUMERIC"),
]

for label, sql in column_patches:
    step(f"alter:{label}", sql)


# ═══════════════════════════════════════════════════════════════════════════════
# 12.  UNIQUE CONSTRAINTS & INDEXES
# ═══════════════════════════════════════════════════════════════════════════════

step("idx:users_phone", """
CREATE UNIQUE INDEX IF NOT EXISTS uidx_users_phone
ON kirana_oltp.users(phone_number)
WHERE phone_number IS NOT NULL
""")

step("idx:subscription_store", """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'subscription_store_id_key'
    ) THEN
        ALTER TABLE kirana_oltp.subscription ADD CONSTRAINT subscription_store_id_key UNIQUE (store_id);
    END IF;
END $$
""")

step("idx:product_category",      "CREATE INDEX IF NOT EXISTS idx_product_category ON kirana_oltp.product(category_id)")
step("idx:product_brand",         "CREATE INDEX IF NOT EXISTS idx_product_brand ON kirana_oltp.product(brand)")
step("idx:product_loose",         "CREATE INDEX IF NOT EXISTS idx_product_loose ON kirana_oltp.product(is_loose)")
step("idx:product_barcode",       "CREATE INDEX IF NOT EXISTS idx_product_barcode ON kirana_oltp.product(barcode) WHERE barcode IS NOT NULL")
step("idx:orders_store_date",     "CREATE INDEX IF NOT EXISTS idx_orders_store_date ON kirana_oltp.orders(store_id, order_date DESC)")
step("idx:inventory_store_product","CREATE INDEX IF NOT EXISTS idx_inventory_store_product ON kirana_oltp.inventory_snapshots(store_id, product_id)")
step("idx:customer_store_id",     "CREATE INDEX IF NOT EXISTS idx_customer_store_id ON kirana_oltp.customer(store_id)")
step("idx:customer_store_phone",  "CREATE INDEX IF NOT EXISTS idx_customer_store_phone ON kirana_oltp.customer(store_id, phone)")
step("idx:user_fcm_tokens_user",  "CREATE INDEX IF NOT EXISTS idx_user_fcm_tokens_user_id ON kirana_oltp.user_fcm_tokens(user_id)")
step("idx:app_activity_user",     "CREATE INDEX IF NOT EXISTS idx_app_activity_user_id ON kirana_oltp.app_activity(user_id, created_at)")
step("idx:khata_payments_khata",  "CREATE INDEX IF NOT EXISTS idx_khata_payments_khata_id ON kirana_oltp.khata_payments(khata_id)")
step("idx:store_association_store","CREATE INDEX IF NOT EXISTS idx_store_association_store ON kirana_oltp.store_association(store_id)")
step("idx:intel_log_store",       "CREATE INDEX IF NOT EXISTS idx_intel_log_store ON kirana_oltp.intelligence_log(store_id, sent_at DESC)")
step("idx:intel_log_trigger",     "CREATE INDEX IF NOT EXISTS idx_intel_log_trigger ON kirana_oltp.intelligence_log(trigger_type, sent_at DESC)")
step("idx:footfall_store_ts",     "CREATE INDEX IF NOT EXISTS idx_footfall_store_ts ON kirana_oltp.footfall(store_id, ts)")
step("idx:scheme_dates",          "CREATE INDEX IF NOT EXISTS idx_scheme_dates ON kirana_oltp.scheme(start_date, end_date)")
step("idx:scheme_claim_store",    "CREATE INDEX IF NOT EXISTS idx_scheme_claim_store ON kirana_oltp.scheme_claim(store_id, claim_date)")
step("idx:khata_store_status",    "CREATE INDEX IF NOT EXISTS idx_khata_store_status ON kirana_oltp.khata(store_id, status)")
step("idx:khata_due",             "CREATE INDEX IF NOT EXISTS idx_khata_due ON kirana_oltp.khata(due_date)")
step("idx:batch_store_expiry",    "CREATE INDEX IF NOT EXISTS idx_batch_store_expiry ON kirana_oltp.inventory_batch(store_id, expiry_date)")
step("idx:rtv_store_date",        "CREATE INDEX IF NOT EXISTS idx_rtv_store_date ON kirana_oltp.return_to_vendor(store_id, return_date)")
step("idx:subscription_active",   "CREATE INDEX IF NOT EXISTS idx_subscription_store_active ON kirana_oltp.subscription(store_id) WHERE ended_at IS NULL")
step("idx:mv_store_date",         "CREATE INDEX IF NOT EXISTS idx_mv_store_date ON kirana_olap.mv_store_daily_summary(store_id, date)")


# ═══════════════════════════════════════════════════════════════════════════════
# 13.  FUNCTIONS, TRIGGERS, MATERIALIZED VIEW
# ═══════════════════════════════════════════════════════════════════════════════

step("fn:ensure_daily_metrics_partition", """
CREATE OR REPLACE FUNCTION kirana_olap.ensure_daily_metrics_partition(target_date DATE)
RETURNS VOID AS $$
DECLARE
    month_start DATE := date_trunc('month', target_date)::date;
    month_end   DATE := (date_trunc('month', target_date) + INTERVAL '1 month')::date;
    part_name   TEXT := format('daily_metrics_%s', to_char(month_start, 'YYYY_MM'));
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS kirana_olap.%I
         PARTITION OF kirana_olap.daily_store_sku_metrics
         FOR VALUES FROM (%L) TO (%L)',
        part_name, month_start, month_end
    );
END;
$$ LANGUAGE plpgsql
""")

step("fn:populate_daily_metrics", """
CREATE OR REPLACE FUNCTION kirana_olap.populate_daily_metrics(target_date DATE)
RETURNS VOID AS $$
BEGIN
    PERFORM kirana_olap.ensure_daily_metrics_partition(target_date);
    INSERT INTO kirana_olap.daily_store_sku_metrics (
        date, store_id, product_id,
        units_sold, revenue, profit,
        stock_on_hand, price, avg_selling_price, margin
    )
    SELECT
        DATE(o.order_date), o.store_id, oi.product_id,
        SUM(oi.quantity), SUM(oi.quantity * oi.unit_price),
        SUM((oi.unit_price - oi.cost_price) * oi.quantity),
        COALESCE(i.quantity, 0),
        AVG(oi.unit_price), AVG(oi.unit_price),
        AVG(oi.unit_price - oi.cost_price)
    FROM kirana_oltp.orders o
    JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
    LEFT JOIN kirana_oltp.inventory i ON i.product_id = oi.product_id AND i.store_id = o.store_id
    WHERE DATE(o.order_date) = target_date
    GROUP BY DATE(o.order_date), o.store_id, oi.product_id, i.quantity
    ON CONFLICT (date, store_id, product_id) DO UPDATE SET
        units_sold = EXCLUDED.units_sold,
        revenue    = EXCLUDED.revenue,
        profit     = EXCLUDED.profit,
        stock_on_hand = EXCLUDED.stock_on_hand,
        price = EXCLUDED.price,
        avg_selling_price = EXCLUDED.avg_selling_price,
        margin = EXCLUDED.margin;
END;
$$ LANGUAGE plpgsql
""")

step("fn:update_inventory_on_sale", """
CREATE OR REPLACE FUNCTION kirana_oltp.update_inventory_on_sale()
RETURNS TRIGGER AS $$
DECLARE
    order_store_id BIGINT;
    current_stock  INT;
    is_real_variant BOOLEAN := FALSE;
    is_service_row  BOOLEAN := FALSE;
BEGIN
    -- V2: services sell as flagged product rows but carry no stock —
    -- skip inventory validation/decrement entirely.
    SELECT COALESCE(is_service, FALSE) INTO is_service_row
    FROM kirana_oltp.product WHERE product_id = NEW.product_id;
    IF is_service_row THEN
        RETURN NEW;
    END IF;

    SELECT store_id INTO order_store_id
    FROM kirana_oltp.orders WHERE order_id = NEW.order_id;

    -- F2: real (non-implicit) variants are decremented at the application
    -- level (pos/crud.py), together with product_variant.stock, scoped to
    -- the exact variant sold. Skip here to avoid double-decrementing and to
    -- avoid this product-only (not variant-scoped) UPDATE clobbering every
    -- sibling variant's inventory row.
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

step("trigger:inventory_on_sale", """
DROP TRIGGER IF EXISTS trg_inventory_on_sale ON kirana_oltp.order_item;
CREATE TRIGGER trg_inventory_on_sale
BEFORE INSERT ON kirana_oltp.order_item
FOR EACH ROW EXECUTE FUNCTION kirana_oltp.update_inventory_on_sale()
""")

step("matview:mv_store_daily_summary", """
CREATE MATERIALIZED VIEW IF NOT EXISTS kirana_olap.mv_store_daily_summary AS
SELECT date, store_id, SUM(revenue) AS total_revenue,
       SUM(profit) AS total_profit, SUM(units_sold) AS total_units
FROM kirana_olap.daily_store_sku_metrics
GROUP BY date, store_id
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 14.  VIEWS
# ═══════════════════════════════════════════════════════════════════════════════

step("view:product_catalog", """
CREATE OR REPLACE VIEW kirana_oltp.product_catalog AS
SELECT * FROM kirana_oltp.product
WHERE (barcode IS NOT NULL OR is_loose = TRUE)
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 14b. ADMIN SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

step("admin_settings:table", """
CREATE TABLE IF NOT EXISTS kirana_oltp.admin_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT         NOT NULL,
    updated_at TIMESTAMPTZ  DEFAULT NOW()
)
""")

step("admin_settings:auto_approve_trial", """
INSERT INTO kirana_oltp.admin_settings (key, value)
VALUES ('auto_approve_trial', 'false')
ON CONFLICT (key) DO NOTHING
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 15.  PERMISSIONS  (grant to the app DB user)
# ═══════════════════════════════════════════════════════════════════════════════

DB_APP_USER = DB_USER  # psqladmin owns everything on Azure — same user

step("perm:oltp_schema", f"GRANT USAGE ON SCHEMA kirana_oltp TO {DB_APP_USER}")
step("perm:olap_schema", f"GRANT USAGE ON SCHEMA kirana_olap TO {DB_APP_USER}")
step("perm:oltp_tables", f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kirana_oltp TO {DB_APP_USER}")
step("perm:olap_tables",  f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kirana_olap TO {DB_APP_USER}")
step("perm:oltp_seqs",    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA kirana_oltp TO {DB_APP_USER}")
step("perm:olap_seqs",    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA kirana_olap TO {DB_APP_USER}")


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run():
    print(f"Connecting to {DB_NAME}@{DB_HOST}:{DB_PORT} as {DB_USER} ...")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
    )
    conn.autocommit = True
    cur = conn.cursor()

    ok = 0
    failed = 0
    for label, sql in STEPS:
        try:
            cur.execute(sql)
            print(f"  OK   {label}")
            ok += 1
        except Exception as exc:
            print(f"  SKIP {label}: {exc}", file=sys.stderr)
            failed += 1

    cur.close()
    conn.close()
    print(f"\nDone: {ok} OK, {failed} skipped/errors.")


if __name__ == "__main__":
    run()
