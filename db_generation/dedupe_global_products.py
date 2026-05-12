import random
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg2

from seed_kirana_final import (
    BRANDS,
    CATEGORY_TREE,
    GLOBAL_PRODUCT_COUNT,
    PRODUCT_VARIANTS,
    get_initial_stock,
    get_margin,
    get_unit_and_weight,
)

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"
DB_HOST = "localhost"
DB_PORT = "5432"
STORE_PRODUCT_COUNT = 200
COMMON_STORE_PRODUCTS = 120

random.seed(84)


def connect():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )


def product_signature(row):
    return (row["name"].strip().lower(),)


def fetch_products(cur):
    cur.execute("""
        SELECT product_id, category_id, name, brand, unit, weight, is_loose, is_perishable
        FROM kirana_oltp.product
        ORDER BY product_id
    """)
    columns = [desc[0] for desc in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def remap_inventory(cur, old_id, new_id):
    cur.execute("""
        UPDATE kirana_oltp.inventory dst
        SET quantity = dst.quantity + src.quantity
        FROM kirana_oltp.inventory src
        WHERE src.product_id = %s
          AND dst.product_id = %s
          AND dst.store_id = src.store_id
    """, (old_id, new_id))
    cur.execute("""
        DELETE FROM kirana_oltp.inventory src
        WHERE src.product_id = %s
          AND EXISTS (
              SELECT 1
              FROM kirana_oltp.inventory dst
              WHERE dst.product_id = %s
                AND dst.store_id = src.store_id
          )
    """, (old_id, new_id))
    cur.execute("""
        UPDATE kirana_oltp.inventory
        SET product_id = %s
        WHERE product_id = %s
    """, (new_id, old_id))


def remap_inventory_snapshots(cur, old_id, new_id):
    cur.execute("""
        UPDATE kirana_oltp.inventory_snapshots dst
        SET stock_on_hand = GREATEST(dst.stock_on_hand, src.stock_on_hand)
        FROM kirana_oltp.inventory_snapshots src
        WHERE src.product_id = %s
          AND dst.product_id = %s
          AND dst.snapshot_date = src.snapshot_date
          AND dst.store_id = src.store_id
    """, (old_id, new_id))
    cur.execute("""
        DELETE FROM kirana_oltp.inventory_snapshots src
        WHERE src.product_id = %s
          AND EXISTS (
              SELECT 1
              FROM kirana_oltp.inventory_snapshots dst
              WHERE dst.product_id = %s
                AND dst.snapshot_date = src.snapshot_date
                AND dst.store_id = src.store_id
          )
    """, (old_id, new_id))
    cur.execute("""
        UPDATE kirana_oltp.inventory_snapshots
        SET product_id = %s
        WHERE product_id = %s
    """, (new_id, old_id))


def remap_daily_metrics(cur, old_id, new_id):
    cur.execute("""
        UPDATE kirana_olap.daily_store_sku_metrics dst
        SET
            units_sold = COALESCE(dst.units_sold, 0) + COALESCE(src.units_sold, 0),
            revenue = COALESCE(dst.revenue, 0) + COALESCE(src.revenue, 0),
            profit = COALESCE(dst.profit, 0) + COALESCE(src.profit, 0),
            stock_on_hand = GREATEST(COALESCE(dst.stock_on_hand, 0), COALESCE(src.stock_on_hand, 0)),
            lost_sales = COALESCE(dst.lost_sales, 0) + COALESCE(src.lost_sales, 0),
            price = COALESCE(dst.price, src.price),
            discount = GREATEST(COALESCE(dst.discount, 0), COALESCE(src.discount, 0)),
            promo_flag = COALESCE(dst.promo_flag, FALSE) OR COALESCE(src.promo_flag, FALSE),
            avg_selling_price = COALESCE(dst.avg_selling_price, src.avg_selling_price),
            margin = COALESCE(dst.margin, src.margin),
            weather_temp = COALESCE(dst.weather_temp, src.weather_temp),
            rain_flag = COALESCE(dst.rain_flag, FALSE) OR COALESCE(src.rain_flag, FALSE)
        FROM kirana_olap.daily_store_sku_metrics src
        WHERE src.product_id = %s
          AND dst.product_id = %s
          AND dst.date = src.date
          AND dst.store_id = src.store_id
    """, (old_id, new_id))
    cur.execute("""
        DELETE FROM kirana_olap.daily_store_sku_metrics src
        WHERE src.product_id = %s
          AND EXISTS (
              SELECT 1
              FROM kirana_olap.daily_store_sku_metrics dst
              WHERE dst.product_id = %s
                AND dst.date = src.date
                AND dst.store_id = src.store_id
          )
    """, (old_id, new_id))
    cur.execute("""
        UPDATE kirana_olap.daily_store_sku_metrics
        SET product_id = %s
        WHERE product_id = %s
    """, (new_id, old_id))


def remap_sku_features(cur, old_id, new_id):
    cur.execute("""
        DELETE FROM kirana_olap.sku_store_features src
        WHERE src.product_id = %s
          AND EXISTS (
              SELECT 1
              FROM kirana_olap.sku_store_features dst
              WHERE dst.product_id = %s
                AND dst.date = src.date
                AND dst.store_id = src.store_id
          )
    """, (old_id, new_id))
    cur.execute("""
        UPDATE kirana_olap.sku_store_features
        SET product_id = %s
        WHERE product_id = %s
    """, (new_id, old_id))


def remap_product_references(cur, duplicate_to_canonical):
    simple_tables = [
        ("kirana_oltp", "pricing"),
        ("kirana_oltp", "promotion"),
        ("kirana_oltp", "order_item"),
        ("kirana_oltp", "purchase_items"),
        ("kirana_oltp", "product_supplier"),
        ("kirana_oltp", "inventory_movements"),
    ]

    for old_id, new_id in duplicate_to_canonical.items():
        remap_inventory(cur, old_id, new_id)
        remap_inventory_snapshots(cur, old_id, new_id)
        remap_daily_metrics(cur, old_id, new_id)
        remap_sku_features(cur, old_id, new_id)

        for schema, table in simple_tables:
            cur.execute(
                f"UPDATE {schema}.{table} SET product_id = %s WHERE product_id = %s",
                (new_id, old_id),
            )

    if duplicate_to_canonical:
        cur.execute(
            "DELETE FROM kirana_oltp.product WHERE product_id = ANY(%s)",
            (list(duplicate_to_canonical.keys()),),
        )


def dedupe_products(cur):
    canonical_by_signature = {}
    duplicate_to_canonical = {}

    for product in fetch_products(cur):
        signature = product_signature(product)
        canonical_id = canonical_by_signature.get(signature)
        if canonical_id is None:
            canonical_by_signature[signature] = product["product_id"]
        else:
            duplicate_to_canonical[product["product_id"]] = canonical_id

    remap_product_references(cur, duplicate_to_canonical)
    return len(duplicate_to_canonical)


def get_category_ids(cur):
    leaf_categories = [child for children in CATEGORY_TREE.values() for child in children]
    cur.execute("""
        SELECT category_id, name
        FROM kirana_oltp.category
        WHERE name = ANY(%s)
    """, (leaf_categories,))
    return {name: category_id for category_id, name in cur.fetchall()}


def get_existing_names(cur):
    cur.execute("SELECT name FROM kirana_oltp.product")
    return {row[0] for row in cur.fetchall()}


def get_existing_skus(cur):
    cur.execute("SELECT sku FROM kirana_oltp.product WHERE sku IS NOT NULL")
    return {row[0] for row in cur.fetchall()}


def get_existing_barcodes(cur):
    cur.execute("SELECT barcode FROM kirana_oltp.product WHERE barcode IS NOT NULL")
    return {row[0] for row in cur.fetchall()}


def next_unique_sku(existing_skus, index):
    while True:
        sku = f"SKU{index}"
        index += 1
        if sku not in existing_skus:
            existing_skus.add(sku)
            return sku, index


def next_unique_barcode(existing_barcodes):
    while True:
        barcode = f"89{random.randint(1000000000, 9999999999)}"
        if barcode not in existing_barcodes:
            existing_barcodes.add(barcode)
            return barcode


def make_unique_product(existing_names, category_ids, index):
    attempts = 0
    while True:
        attempts += 1
        if attempts > GLOBAL_PRODUCT_COUNT * 200:
            raise RuntimeError("Could not create a new unique product name")

        subcat = random.choice(list(category_ids.keys()))
        brand = random.choice(BRANDS.get(subcat, ["Generic"]))
        is_loose = random.choice([True, False])
        unit, weight = get_unit_and_weight(subcat, is_loose)
        is_perishable = subcat in ["Milk", "Curd"]
        variant = PRODUCT_VARIANTS[(attempts + index) % len(PRODUCT_VARIANTS)]
        name = f"{brand} {variant} {subcat} {weight}{unit}"

        if name in existing_names:
            name = f"{brand} {variant} {subcat} {weight}{unit} SKU {index:03d}"

        if name in existing_names:
            continue

        existing_names.add(name)
        return {
            "category_id": category_ids[subcat],
            "subcat": subcat,
            "name": name,
            "brand": brand,
            "unit": unit,
            "weight": weight,
            "is_loose": is_loose,
            "is_perishable": is_perishable,
        }


def top_up_products(cur):
    cur.execute("SELECT COUNT(*) FROM kirana_oltp.product")
    current_count = cur.fetchone()[0]
    if current_count >= GLOBAL_PRODUCT_COUNT:
        return 0

    category_ids = get_category_ids(cur)
    existing_names = get_existing_names(cur)
    existing_skus = get_existing_skus(cur)
    existing_barcodes = get_existing_barcodes(cur)
    sku_index = 0
    added = 0

    while current_count < GLOBAL_PRODUCT_COUNT:
        product = make_unique_product(existing_names, category_ids, current_count + 1)
        sku, sku_index = next_unique_sku(existing_skus, sku_index)
        cur.execute("""
            INSERT INTO kirana_oltp.product
            (category_id, name, brand, unit, weight, is_loose, is_perishable, sku, barcode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            product["category_id"],
            product["name"],
            product["brand"],
            product["unit"],
            Decimal(str(product["weight"])),
            product["is_loose"],
            product["is_perishable"],
            sku,
            next_unique_barcode(existing_barcodes),
        ))
        current_count += 1
        added += 1

    return added


def get_store_ids(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE ORDER BY store_id")
    return [row[0] for row in cur.fetchall()]


def ensure_store_supplier(cur, store_id):
    cur.execute("SELECT supplier_id FROM kirana_oltp.supplier WHERE store_id = %s ORDER BY supplier_id LIMIT 1", (store_id,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("""
        INSERT INTO kirana_oltp.supplier (name, contact, store_id)
        VALUES (%s, %s, %s)
        RETURNING supplier_id
    """, (f"Supplier_{store_id}_default", f"9{random.randint(100000000, 999999999)}", store_id))
    return cur.fetchone()[0]


def product_category_name(cur, category_id):
    cur.execute("SELECT name FROM kirana_oltp.category WHERE category_id = %s", (category_id,))
    row = cur.fetchone()
    return row[0] if row else "Generic"


def ensure_store_catalog(cur):
    cur.execute("""
        SELECT product_id, category_id, unit, weight
        FROM kirana_oltp.product
        ORDER BY product_id
    """)
    products = cur.fetchall()
    store_ids = get_store_ids(cur)
    now = datetime.now()
    added_inventory = 0
    removed_inventory = 0
    variant_products = products[COMMON_STORE_PRODUCTS:]

    for store_id in store_ids:
        supplier_id = ensure_store_supplier(cur, store_id)
        rng = random.Random(1000 + int(store_id))
        store_products = list(products[:COMMON_STORE_PRODUCTS])
        store_products.extend(rng.sample(variant_products, STORE_PRODUCT_COUNT - COMMON_STORE_PRODUCTS))
        selected_product_ids = {row[0] for row in store_products}

        cur.execute("""
            DELETE FROM kirana_oltp.inventory
            WHERE store_id = %s
              AND product_id <> ALL(%s)
        """, (store_id, list(selected_product_ids)))
        removed_inventory += cur.rowcount

        for product_id, category_id, unit, weight in store_products:
            cur.execute("""
                SELECT 1
                FROM kirana_oltp.inventory
                WHERE store_id = %s AND product_id = %s
            """, (store_id, product_id))
            if cur.fetchone():
                continue

            subcat = product_category_name(cur, category_id)
            cost = round(random.uniform(10, 500), 2)
            selling = round(cost * get_margin(subcat), 2)
            mrp = round(selling * random.uniform(1.02, 1.08), 2)
            valid_from = now - timedelta(days=random.randint(20, 120))
            valid_to = valid_from + timedelta(days=random.randint(90, 240))

            cur.execute("""
                INSERT INTO kirana_oltp.pricing
                (product_id, store_id, price, mrp, valid_from, valid_to)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (product_id, store_id, selling, mrp, valid_from, valid_to))

            cur.execute("""
                INSERT INTO kirana_oltp.product_supplier
                (product_id, supplier_id, cost_price, lead_time_days)
                VALUES (%s, %s, %s, %s)
            """, (product_id, supplier_id, cost, random.randint(1, 4)))

            cur.execute("""
                INSERT INTO kirana_oltp.inventory
                (store_id, product_id, quantity)
                VALUES (%s, %s, %s)
            """, (store_id, product_id, get_initial_stock(subcat, unit, float(weight or 0))))
            added_inventory += 1

    return added_inventory, removed_inventory


def report(cur):
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT name) FROM kirana_oltp.product")
    product_count, unique_names = cur.fetchone()
    cur.execute("""
        SELECT store_id, COUNT(*), COUNT(DISTINCT product_id)
        FROM kirana_oltp.inventory
        GROUP BY store_id
        ORDER BY store_id
    """)
    per_store = cur.fetchall()
    return product_count, unique_names, per_store


def main():
    with connect() as conn:
        with conn.cursor() as cur:
            removed = dedupe_products(cur)
            added_products = top_up_products(cur)
            added_inventory, removed_inventory = ensure_store_catalog(cur)
            product_count, unique_names, per_store = report(cur)

        conn.commit()

    print(f"Removed duplicate product rows: {removed}")
    print(f"Added global products: {added_products}")
    print(f"Added missing store inventory rows: {added_inventory}")
    print(f"Removed non-assortment store inventory rows: {removed_inventory}")
    print(f"Global products: {product_count}; unique product names: {unique_names}")
    for store_id, rows, distinct_products in per_store:
        print(f"Store {store_id}: inventory rows={rows}, distinct_products={distinct_products}")


if __name__ == "__main__":
    main()
