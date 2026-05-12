import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"  # change
DB_HOST = "localhost"
DB_PORT = "5432"


def create_database():
    conn = psycopg2.connect(
        dbname="postgres",
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    cur.execute(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'")
    if not cur.fetchone():
        cur.execute(f"CREATE DATABASE {DB_NAME}")
        print(f"Database '{DB_NAME}' created.")
    else:
        print(f"Database '{DB_NAME}' already exists.")

    cur.close()
    conn.close()


def setup_schema():
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )
    cur = conn.cursor()

    # Schemas
    cur.execute("CREATE SCHEMA IF NOT EXISTS kirana_oltp;")
    cur.execute("CREATE SCHEMA IF NOT EXISTS kirana_olap;")

    # =========================
    # OLTP TABLES
    # =========================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.store (
        store_id BIGSERIAL PRIMARY KEY,
        name VARCHAR(150) NOT NULL,
        location VARCHAR(255),
        region VARCHAR(100),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_deleted BOOLEAN DEFAULT FALSE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.users (
        user_id BIGSERIAL PRIMARY KEY,
        username VARCHAR(100) UNIQUE NOT NULL,
        email VARCHAR(150),
        role VARCHAR(50),
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_deleted BOOLEAN DEFAULT FALSE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.customer (
        customer_id BIGSERIAL PRIMARY KEY,
        name VARCHAR(150),
        phone VARCHAR(20),
        email VARCHAR(150),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.category (
        category_id BIGSERIAL PRIMARY KEY,
        parent_category_id BIGINT REFERENCES kirana_oltp.category(category_id),
        name VARCHAR(150) NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.product (
        product_id BIGSERIAL PRIMARY KEY,
        category_id BIGINT NOT NULL REFERENCES kirana_oltp.category(category_id),
        name VARCHAR(200) NOT NULL,
        brand VARCHAR(100),
        unit VARCHAR(20),
        weight NUMERIC(10,2),
        is_loose BOOLEAN DEFAULT FALSE,
        is_perishable BOOLEAN DEFAULT FALSE,
        sku VARCHAR(100) UNIQUE,
        barcode VARCHAR(100) UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.supplier (
        supplier_id BIGSERIAL PRIMARY KEY,
        name VARCHAR(150),
        contact VARCHAR(150),
        store_id BIGINT REFERENCES kirana_oltp.store(store_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.product_supplier (
        id BIGSERIAL PRIMARY KEY,
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        supplier_id BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
        cost_price NUMERIC(10,2) CHECK (cost_price >= 0),
        lead_time_days INT CHECK (lead_time_days >= 0)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.pricing (
        pricing_id BIGSERIAL PRIMARY KEY,
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        price NUMERIC(10,2) CHECK (price >= 0),
        mrp NUMERIC(10,2) CHECK (mrp >= 0),
        valid_from TIMESTAMP NOT NULL,
        valid_to TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.promotion (
        promotion_id BIGSERIAL PRIMARY KEY,
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        discount_percent NUMERIC(5,2) CHECK (discount_percent >= 0),
        start_date TIMESTAMP,
        end_date TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.orders (
        order_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        user_id BIGINT REFERENCES kirana_oltp.users(user_id),
        customer_id BIGINT REFERENCES kirana_oltp.customer(customer_id),
        order_status VARCHAR(50),
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        total_amount NUMERIC(12,2) CHECK (total_amount >= 0)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.order_item (
        order_item_id BIGSERIAL PRIMARY KEY,
        order_id BIGINT NOT NULL REFERENCES kirana_oltp.orders(order_id) ON DELETE CASCADE,
        product_id BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
        quantity INT CHECK (quantity > 0),
        unit_price NUMERIC(10,2) CHECK (unit_price >= 0),
        cost_price NUMERIC(10,2) CHECK (cost_price >= 0)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.payments (
        payment_id BIGSERIAL PRIMARY KEY,
        order_id BIGINT REFERENCES kirana_oltp.orders(order_id),
        amount NUMERIC(10,2),
        payment_method VARCHAR(50),
        status VARCHAR(50),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.purchases (
        purchase_id BIGSERIAL PRIMARY KEY,
        supplier_id BIGINT REFERENCES kirana_oltp.supplier(supplier_id),
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        order_date TIMESTAMP,
        arrival_date TIMESTAMP,
        status VARCHAR(50)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.purchase_items (
        purchase_item_id BIGSERIAL PRIMARY KEY,
        purchase_id BIGINT REFERENCES kirana_oltp.purchases(purchase_id) ON DELETE CASCADE,
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        quantity INT CHECK (quantity > 0),
        cost_price NUMERIC(10,2)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.inventory (
        inventory_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        quantity INT DEFAULT 0 CHECK (quantity >= 0),
        UNIQUE (store_id, product_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_movements (
        movement_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        change_quantity INT,
        reason VARCHAR(50),
        reference_id BIGINT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_snapshots (
        snapshot_date DATE,
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        stock_on_hand INT CHECK (stock_on_hand >= 0),
        PRIMARY KEY (snapshot_date, store_id, product_id)
    );
    """)

    # =========================
    # OLAP TABLES
    # =========================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_olap.daily_store_sku_metrics (
        date DATE,
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
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kirana_olap.sku_store_features (
        date DATE,
        store_id BIGINT,
        product_id BIGINT,
        rolling_7d_sales INT,
        rolling_30d_sales INT,
        avg_stock INT,
        stockout_rate NUMERIC(5,2),
        avg_margin NUMERIC(5,2),
        price_elasticity NUMERIC(5,2),
        promo_effectiveness NUMERIC(5,2),
        PRIMARY KEY (date, store_id, product_id)
    );
    """)

    # =========================
    # INDEXES
    # =========================

    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_store_date ON kirana_oltp.orders(store_id, order_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_order_item_product ON kirana_oltp.order_item(product_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_lookup ON kirana_oltp.inventory(store_id, product_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_movements_product ON kirana_oltp.inventory_movements(product_id, created_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pricing_lookup ON kirana_oltp.pricing(product_id, store_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_metrics_store_product ON kirana_olap.daily_store_sku_metrics(store_id, product_id);")

    conn.commit()
    cur.close()
    conn.close()

    print("Schemas, tables, constraints, and indexes created successfully.")


if __name__ == "__main__":
    create_database()
    setup_schema()
