import os
import psycopg2

DB_NAME = os.environ.get("DB_NAME", "lit_db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "123456")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")


def upgrade():
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )
    cur = conn.cursor()

    # =========================
    # 0. PRODUCT TABLE UPGRADE
    # =========================
    cur.execute("""
    DO $$
    BEGIN
        -- brand
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='product'
            AND column_name='brand'
        ) THEN
            ALTER TABLE kirana_oltp.product ADD COLUMN brand VARCHAR(100);
        END IF;

        -- unit (kg, g, ml, pcs)
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='product'
            AND column_name='unit'
        ) THEN
            ALTER TABLE kirana_oltp.product ADD COLUMN unit VARCHAR(20);
        END IF;

        -- weight
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='product'
            AND column_name='weight'
        ) THEN
            ALTER TABLE kirana_oltp.product ADD COLUMN weight NUMERIC(10,2);
        END IF;

        -- loose flag
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='product'
            AND column_name='is_loose'
        ) THEN
            ALTER TABLE kirana_oltp.product ADD COLUMN is_loose BOOLEAN DEFAULT FALSE;
        END IF;

        -- perishable flag
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='product'
            AND column_name='is_perishable'
        ) THEN
            ALTER TABLE kirana_oltp.product ADD COLUMN is_perishable BOOLEAN DEFAULT FALSE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='supplier'
            AND column_name='store_id'
        ) THEN
            ALTER TABLE kirana_oltp.supplier
            ADD COLUMN store_id BIGINT REFERENCES kirana_oltp.store(store_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='supplier'
            AND column_name='phone'
        ) THEN
            ALTER TABLE kirana_oltp.supplier ADD COLUMN phone VARCHAR(20);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='supplier'
            AND column_name='category'
        ) THEN
            ALTER TABLE kirana_oltp.supplier ADD COLUMN category VARCHAR(100);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='pricing'
            AND column_name='mrp'
        ) THEN
            ALTER TABLE kirana_oltp.pricing ADD COLUMN mrp NUMERIC(10,2);
        END IF;

    END $$;
    """)
    cur.execute("""
    ALTER TABLE kirana_oltp.product
    ALTER COLUMN name SET NOT NULL;
    """)

    cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'chk_weight_positive'
        ) THEN
            ALTER TABLE kirana_oltp.product
            ADD CONSTRAINT chk_weight_positive
            CHECK (weight IS NULL OR weight > 0);
        END IF;
    END $$;
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_product_category
    ON kirana_oltp.product(category_id);
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_product_brand
    ON kirana_oltp.product(brand);
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_product_loose
    ON kirana_oltp.product(is_loose);
    """)
    # =========================
    # 1. PARTITIONED TABLE (OLAP)
    # =========================
    cur.execute("""
    DO $$
    DECLARE
        is_partitioned BOOLEAN;
    BEGIN
        SELECT c.relkind = 'p'
        INTO is_partitioned
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'kirana_olap'
          AND c.relname = 'daily_store_sku_metrics';

        IF is_partitioned IS FALSE THEN
            DROP TABLE kirana_olap.daily_store_sku_metrics CASCADE;
        END IF;
    END $$;
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_olap.daily_store_sku_metrics (
        date DATE NOT NULL,
        store_id BIGINT,
        product_id BIGINT,
        units_sold INT,
        revenue NUMERIC(12,2),
        profit NUMERIC(12,2),
        stock_on_hand INT,
        lost_sales INT,
        price NUMERIC(10,2),
        discount NUMERIC(5,2),
        promo_flag BOOLEAN,
        avg_selling_price NUMERIC(10,2),
        margin NUMERIC(5,2),
        weather_temp NUMERIC(5,2),
        rain_flag BOOLEAN,
        PRIMARY KEY (date, store_id, product_id)
    ) PARTITION BY RANGE (date);
    """)

    cur.execute("""
    CREATE OR REPLACE FUNCTION kirana_olap.ensure_daily_metrics_partition(target_date DATE)
    RETURNS VOID AS $$
    DECLARE
        month_start DATE := date_trunc('month', target_date)::date;
        month_end DATE := (date_trunc('month', target_date) + INTERVAL '1 month')::date;
        partition_name TEXT := format(
            'daily_metrics_%s',
            to_char(month_start, 'YYYY_MM')
        );
    BEGIN
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS kirana_olap.%I
             PARTITION OF kirana_olap.daily_store_sku_metrics
             FOR VALUES FROM (%L) TO (%L)',
            partition_name,
            month_start,
            month_end
        );
    END;
    $$ LANGUAGE plpgsql;
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_olap.daily_metrics_default
    PARTITION OF kirana_olap.daily_store_sku_metrics DEFAULT;
    """)

    # =========================
    # 2. INVENTORY TRIGGER
    # =========================
    cur.execute("""
    CREATE OR REPLACE FUNCTION kirana_oltp.update_inventory_on_sale()
    RETURNS TRIGGER AS $$
    DECLARE
        order_store_id BIGINT;
        current_stock INT;
    BEGIN
        SELECT store_id
        INTO order_store_id
        FROM kirana_oltp.orders
        WHERE order_id = NEW.order_id;

        SELECT quantity
        INTO current_stock
        FROM kirana_oltp.inventory
        WHERE store_id = order_store_id
          AND product_id = NEW.product_id
        FOR UPDATE;

        IF current_stock IS NULL THEN
            RAISE EXCEPTION 'Inventory row missing for store %, product %', order_store_id, NEW.product_id;
        END IF;

        IF current_stock < NEW.quantity THEN
            RAISE EXCEPTION 'Insufficient stock for store %, product %: available %, requested %',
                order_store_id, NEW.product_id, current_stock, NEW.quantity;
        END IF;

        UPDATE kirana_oltp.inventory
        SET quantity = quantity - NEW.quantity
        WHERE store_id = order_store_id
          AND product_id = NEW.product_id;

        INSERT INTO kirana_oltp.inventory_movements (
            store_id, product_id, change_quantity, reason, reference_id
        )
        VALUES (
            order_store_id,
            NEW.product_id,
            -NEW.quantity,
            'sale',
            NEW.order_id
        );

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    cur.execute("""
    DROP TRIGGER IF EXISTS trg_inventory_on_sale ON kirana_oltp.order_item;

    CREATE TRIGGER trg_inventory_on_sale
    BEFORE INSERT ON kirana_oltp.order_item
    FOR EACH ROW
    EXECUTE FUNCTION kirana_oltp.update_inventory_on_sale();
    """)

    # =========================
    # 3. MATERIALIZED VIEW (DASHBOARD)
    # =========================
    cur.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS kirana_olap.mv_store_daily_summary AS
    SELECT
        date,
        store_id,
        SUM(revenue) AS total_revenue,
        SUM(profit) AS total_profit,
        SUM(units_sold) AS total_units
    FROM kirana_olap.daily_store_sku_metrics
    GROUP BY date, store_id;
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_mv_store_date
    ON kirana_olap.mv_store_daily_summary(store_id, date);
    """)

    # =========================
    # 4. ETL FUNCTION (BUILD DAILY METRICS)
    # =========================
    cur.execute("""
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
            DATE(o.order_date) AS date,
            o.store_id,
            oi.product_id,

            SUM(oi.quantity) AS units_sold,
            SUM(oi.quantity * oi.unit_price) AS revenue,
            SUM((oi.unit_price - oi.cost_price) * oi.quantity) AS profit,

            COALESCE(i.quantity, 0) AS stock_on_hand,
            AVG(oi.unit_price) AS price,
            AVG(oi.unit_price) AS avg_selling_price,
            AVG((oi.unit_price - oi.cost_price)) AS margin

        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        LEFT JOIN kirana_oltp.inventory i
            ON i.product_id = oi.product_id AND i.store_id = o.store_id

        WHERE DATE(o.order_date) = target_date

        GROUP BY DATE(o.order_date), o.store_id, oi.product_id, i.quantity
        ON CONFLICT (date, store_id, product_id)
        DO UPDATE SET
            units_sold = EXCLUDED.units_sold,
            revenue = EXCLUDED.revenue,
            profit = EXCLUDED.profit,
            stock_on_hand = EXCLUDED.stock_on_hand,
            price = EXCLUDED.price,
            avg_selling_price = EXCLUDED.avg_selling_price,
            margin = EXCLUDED.margin;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # =========================
    # 5. USER_PREFS — subscribed_kpis column
    # =========================
    cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='user_prefs'
            AND column_name='subscribed_kpis'
        ) THEN
            ALTER TABLE kirana_oltp.user_prefs ADD COLUMN subscribed_kpis TEXT;
        END IF;
    END $$;
    """)

    # =========================
    # 6. SUBSCRIPTION TABLE UPGRADE
    # =========================
    cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='subscription'
            AND column_name='is_trial'
        ) THEN
            ALTER TABLE kirana_oltp.subscription ADD COLUMN is_trial BOOLEAN NOT NULL DEFAULT FALSE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='kirana_oltp'
            AND table_name='subscription'
            AND column_name='trial_ends_at'
        ) THEN
            ALTER TABLE kirana_oltp.subscription ADD COLUMN trial_ends_at TIMESTAMP;
        END IF;
    END $$;
    """)

    # =========================
    # BARCODE INDEX
    # =========================
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_product_barcode
        ON kirana_oltp.product (barcode)
        WHERE barcode IS NOT NULL;
    """)

    # =========================
    # KPI TIER CONFIG TABLE
    # =========================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.kpi_tier_config (
        kpi_id       TEXT        PRIMARY KEY,
        required_tier TEXT       NOT NULL DEFAULT 'basic'
            CHECK (required_tier IN ('basic', 'pro')),
        updated_at   TIMESTAMP   NOT NULL DEFAULT NOW()
    );
    """)

    # =========================
    # STORE ASSOCIATIONS
    # =========================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.store_association (
        association_id      SERIAL PRIMARY KEY,
        store_id            INTEGER NOT NULL
            REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
        name                TEXT NOT NULL,
        area_type           TEXT NOT NULL
            CHECK (area_type IN ('apartment','hostel','school','office','colony')),
        estimated_households INTEGER,
        notes               TEXT,
        is_active           BOOLEAN NOT NULL DEFAULT TRUE,
        created_at          TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_store_association_store
        ON kirana_oltp.store_association (store_id);
    """)

    cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'kirana_oltp'
              AND table_name   = 'customer'
              AND column_name  = 'association_id'
        ) THEN
            ALTER TABLE kirana_oltp.customer
                ADD COLUMN association_id INTEGER
                REFERENCES kirana_oltp.store_association(association_id)
                ON DELETE SET NULL;
        END IF;
    END $$;
    """)

    # =========================
    # INTELLIGENCE LOG TABLE
    # =========================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.intelligence_log (
        id              BIGSERIAL PRIMARY KEY,
        store_id        INTEGER NOT NULL,
        user_id         INTEGER,
        trigger_type    VARCHAR(50) NOT NULL,
        title           TEXT NOT NULL,
        body            TEXT NOT NULL,
        payload         JSONB NOT NULL DEFAULT '{}',
        sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        opened_at       TIMESTAMPTZ,
        status          VARCHAR(20) NOT NULL DEFAULT 'sent'
            CHECK (status IN ('sent', 'failed', 'opened', 'skipped'))
    );

    CREATE INDEX IF NOT EXISTS idx_intel_log_store
        ON kirana_oltp.intelligence_log (store_id, sent_at DESC);

    CREATE INDEX IF NOT EXISTS idx_intel_log_trigger
        ON kirana_oltp.intelligence_log (trigger_type, sent_at DESC);
    """)

    # =========================
    # CART SESSION TABLE
    # Tracks active POS cart state pushed from the Flutter app.
    # Used by abandoned-cart detection.
    # =========================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.cart_session (
        store_id        INTEGER PRIMARY KEY,
        item_count      INTEGER NOT NULL DEFAULT 0,
        cart_data       JSONB NOT NULL DEFAULT '[]',
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        notified_at     TIMESTAMPTZ,
        converted_at    TIMESTAMPTZ
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

    print("Upgrade complete: intelligence_log, cart_session added.")


if __name__ == "__main__":
    upgrade()
