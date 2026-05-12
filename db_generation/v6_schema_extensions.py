"""
v6 Schema Extensions — adds the tables/columns needed to unlock the 23
KPIs that previously returned `data_unavailable`.

This script is idempotent (uses IF NOT EXISTS / DO blocks) so it's safe to
re-run. After running it, also run `v6_seed_extensions.py` to fill the
new tables with deterministic synthetic data.

Run:
    python db_generation/v6_schema_extensions.py
"""
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"
DB_HOST = "localhost"
DB_PORT = "5432"


DDL_STATEMENTS = [
    # ── Existing-table extensions ────────────────────────────────────────────
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp' AND table_name='product'
              AND column_name='is_private_label'
        ) THEN
            ALTER TABLE kirana_oltp.product
                ADD COLUMN is_private_label BOOLEAN DEFAULT FALSE;
        END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp' AND table_name='orders'
              AND column_name='order_channel'
        ) THEN
            ALTER TABLE kirana_oltp.orders
                ADD COLUMN order_channel VARCHAR(20) DEFAULT 'walk_in';
        END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp' AND table_name='purchase_items'
              AND column_name='requested_qty'
        ) THEN
            ALTER TABLE kirana_oltp.purchase_items
                ADD COLUMN requested_qty INT;
        END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp' AND table_name='customer'
              AND column_name='household_size'
        ) THEN
            ALTER TABLE kirana_oltp.customer
                ADD COLUMN household_size INT DEFAULT 4;
        END IF;
    END $$;
    """,

    # ── K_TL_2: Walk-in to Purchase % ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.footfall (
        footfall_id BIGSERIAL PRIMARY KEY,
        store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
        ts          TIMESTAMP NOT NULL,
        hour        INT NOT NULL CHECK (hour BETWEEN 0 AND 23),
        visitors    INT NOT NULL CHECK (visitors >= 0),
        UNIQUE (store_id, ts)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_footfall_store_ts ON kirana_oltp.footfall(store_id, ts);",

    # ── K_TL_6: Scheme Benefit Capture ───────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.scheme (
        scheme_id     BIGSERIAL PRIMARY KEY,
        supplier_id   BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
        product_id    BIGINT REFERENCES kirana_oltp.product(product_id),
        name          VARCHAR(150) NOT NULL,
        scheme_type   VARCHAR(40) NOT NULL,                  -- bulk_discount, free_qty, cashback
        value         NUMERIC(12,2) NOT NULL DEFAULT 0,      -- discount % or cashback ₹
        min_qty       INT NOT NULL DEFAULT 1,
        start_date    DATE NOT NULL,
        end_date      DATE NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.scheme_claim (
        claim_id   BIGSERIAL PRIMARY KEY,
        scheme_id  BIGINT NOT NULL REFERENCES kirana_oltp.scheme(scheme_id) ON DELETE CASCADE,
        store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        purchase_id BIGINT REFERENCES kirana_oltp.purchases(purchase_id),
        claim_date DATE NOT NULL,
        amount_saved NUMERIC(12,2) NOT NULL DEFAULT 0,
        status     VARCHAR(20) NOT NULL DEFAULT 'claimed'    -- claimed | missed
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_scheme_dates ON kirana_oltp.scheme(start_date, end_date);",
    "CREATE INDEX IF NOT EXISTS idx_scheme_claim_store ON kirana_oltp.scheme_claim(store_id, claim_date);",

    # ── K_TL_12: Festive / Seasonal Uplift ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.calendar (
        cal_date DATE PRIMARY KEY,
        festival VARCHAR(100),
        weight   NUMERIC(4,2) NOT NULL DEFAULT 1.0           -- expected demand multiplier
    );
    """,

    # ── K_BL_1 / C_12: Udhar (Credit) Recovery ───────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.khata (
        khata_id    BIGSERIAL PRIMARY KEY,
        customer_id BIGINT NOT NULL REFERENCES kirana_oltp.customer(customer_id),
        store_id    BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        order_id    BIGINT REFERENCES kirana_oltp.orders(order_id),
        amount      NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
        amount_paid NUMERIC(12,2) NOT NULL DEFAULT 0,
        issue_date  DATE NOT NULL,
        due_date    DATE NOT NULL,
        status      VARCHAR(20) NOT NULL DEFAULT 'open'      -- open | settled | overdue | written_off
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_khata_store_status ON kirana_oltp.khata(store_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_khata_due ON kirana_oltp.khata(due_date);",

    # ── K_BL_2 / K_BL_16: Batch-level expiry ─────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_batch (
        batch_id     BIGSERIAL PRIMARY KEY,
        store_id     BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        product_id   BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
        batch_no     VARCHAR(60),
        manufactured_date DATE,
        expiry_date  DATE NOT NULL,
        qty_in_stock INT NOT NULL DEFAULT 0 CHECK (qty_in_stock >= 0),
        markdown_pct NUMERIC(5,2) DEFAULT 0,                 -- % discount applied for clearance
        recovered_units INT DEFAULT 0,                       -- sold under markdown
        wasted_units INT DEFAULT 0,                          -- expired and discarded
        UNIQUE (store_id, product_id, batch_no)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_batch_store_expiry ON kirana_oltp.inventory_batch(store_id, expiry_date);",

    # ── K_BL_7: Shelf Space Productivity ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.shelf_planogram (
        plano_id   BIGSERIAL PRIMARY KEY,
        store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        product_id BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
        shelf_id   VARCHAR(40) NOT NULL,
        sq_ft      NUMERIC(6,2) NOT NULL CHECK (sq_ft > 0),
        eye_level  BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (store_id, product_id)
    );
    """,

    # ── K_BL_10 / C_10: Operating Expenses (Electricity / Rent / Staff) ──────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.opex (
        opex_id    BIGSERIAL PRIMARY KEY,
        store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        month_start DATE NOT NULL,
        electricity NUMERIC(12,2) DEFAULT 0,
        rent        NUMERIC(12,2) DEFAULT 0,
        staff       NUMERIC(12,2) DEFAULT 0,
        other       NUMERIC(12,2) DEFAULT 0,
        UNIQUE (store_id, month_start)
    );
    """,

    # ── K_BL_13: Return-to-Vendor Recovery ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.return_to_vendor (
        rtv_id     BIGSERIAL PRIMARY KEY,
        store_id   BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        supplier_id BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
        product_id BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
        return_date DATE NOT NULL,
        qty_returned INT NOT NULL CHECK (qty_returned > 0),
        unit_cost  NUMERIC(10,2) NOT NULL DEFAULT 0,
        recovery_pct NUMERIC(5,2) NOT NULL DEFAULT 0,        -- % of cost the vendor reimbursed
        amount_recovered NUMERIC(12,2) NOT NULL DEFAULT 0,
        reason     VARCHAR(60)                               -- damaged | expired | unsold
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_rtv_store_date ON kirana_oltp.return_to_vendor(store_id, return_date);",

    # ── C_2 / C_3 / C_11: Subscription / Billing ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.subscription (
        subscription_id BIGSERIAL PRIMARY KEY,
        store_id        BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        tier            VARCHAR(40) NOT NULL,                -- basic | pro | enterprise
        monthly_price   NUMERIC(10,2) NOT NULL,
        started_at      TIMESTAMP NOT NULL,
        ended_at        TIMESTAMP,                           -- NULL = active
        renewal_count   INT NOT NULL DEFAULT 0,
        savings_to_date NUMERIC(12,2) NOT NULL DEFAULT 0     -- AI-attributed cumulative savings
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_subscription_store_active ON kirana_oltp.subscription(store_id) WHERE ended_at IS NULL;",

    # ── C_6: Brand Co-invest Conversion ──────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.crm_deals (
        deal_id     BIGSERIAL PRIMARY KEY,
        store_id    BIGINT REFERENCES kirana_oltp.store(store_id),
        brand_name  VARCHAR(120) NOT NULL,
        deal_type   VARCHAR(40) NOT NULL,                    -- co_invest | trade_promo | sampling
        deal_value  NUMERIC(12,2) NOT NULL DEFAULT 0,
        stage       VARCHAR(40) NOT NULL,                    -- lead | proposal | won | lost
        opened_at   DATE NOT NULL,
        closed_at   DATE
    );
    """,

    # ── C_8: CAC Payback ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.marketing_spend (
        spend_id    BIGSERIAL PRIMARY KEY,
        store_id    BIGINT REFERENCES kirana_oltp.store(store_id),
        spend_date  DATE NOT NULL,
        channel     VARCHAR(40) NOT NULL,                    -- whatsapp | flyer | hoarding | digital
        amount      NUMERIC(12,2) NOT NULL,
        attributed_customers INT NOT NULL DEFAULT 0
    );
    """,

    # ── C_9: Working Capital Cycle ───────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.ap_ar_aging (
        aging_id   BIGSERIAL PRIMARY KEY,
        store_id   BIGINT REFERENCES kirana_oltp.store(store_id),
        snapshot_date DATE NOT NULL,
        ap_0_30    NUMERIC(12,2) NOT NULL DEFAULT 0,
        ap_31_60   NUMERIC(12,2) NOT NULL DEFAULT 0,
        ap_61_plus NUMERIC(12,2) NOT NULL DEFAULT 0,
        ar_0_30    NUMERIC(12,2) NOT NULL DEFAULT 0,
        ar_31_60   NUMERIC(12,2) NOT NULL DEFAULT 0,
        ar_61_plus NUMERIC(12,2) NOT NULL DEFAULT 0,
        avg_inventory_value NUMERIC(12,2) NOT NULL DEFAULT 0,
        UNIQUE (store_id, snapshot_date)
    );
    """,

    # ── C_14: Process Automation Rate ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.process_events (
        event_id    BIGSERIAL PRIMARY KEY,
        store_id    BIGINT REFERENCES kirana_oltp.store(store_id),
        ts          TIMESTAMP NOT NULL,
        process     VARCHAR(60) NOT NULL,                    -- reorder | bill | scheme_claim | reconcile
        mode        VARCHAR(20) NOT NULL CHECK (mode IN ('manual','automated')),
        latency_ms  INT,
        success     BOOLEAN NOT NULL DEFAULT TRUE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_pe_store_ts ON kirana_oltp.process_events(store_id, ts);",
]


def run():
    print(f"Connecting to {DB_NAME}@{DB_HOST}:{DB_PORT} ...")
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        host=DB_HOST, port=DB_PORT,
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    for stmt in DDL_STATEMENTS:
        s = stmt.strip()
        if not s:
            continue
        cur.execute(s)
        first_line = s.split("\n", 1)[0][:80]
        print(f"  OK  {first_line}")
    cur.close()
    conn.close()
    print("\nv6 schema extensions applied successfully.")


if __name__ == "__main__":
    run()
