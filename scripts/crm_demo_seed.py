#!/usr/bin/env python3
"""
Seed light demo activity into Azure stores that currently have zero inventory,
so they don't look completely dormant: a slice of store 25's catalogue
(price held constant, stock varied), some customers, daily-ish orders since
each store's registration date, daily footfall, and a couple of CRM brand
deals.

Every row this script creates (or, for pricing rows that already existed on
stores 22/23/24, every value it overwrites) is recorded in a manifest JSON
file. crm_demo_rollback.py reads that manifest and undoes exactly this run --
nothing else in the database is touched.

Usage:
    python scripts/crm_demo_seed.py
    AZURE_DB_URL="postgresql://..." python scripts/crm_demo_seed.py
"""

import json
import os
import random
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get(
    "AZURE_DB_URL",
    "host=psql-lohiya-kirana.postgres.database.azure.com port=5432 "
    "dbname=db-kirana-dev user=psqladmin password=Lohiya@2026 sslmode=require",
)

SOURCE_STORE_ID = 25
TARGET_STORE_IDS = [22, 23, 24, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 48, 50]
END_DATE = date(2026, 6, 23)

PRODUCTS_PER_STORE = (50, 60)
CUSTOMERS_PER_STORE = (10, 20)
ORDERS_PER_STORE = (30, 40)
CRM_DEALS_PER_STORE = (1, 2)

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "crm_demo_manifest.json")

FIRST_NAMES = [
    "Ravi", "Suresh", "Anita", "Lakshmi", "Venkat", "Priya", "Mahesh", "Sunita",
    "Krishna", "Padma", "Rajesh", "Geeta", "Srinivas", "Kavya", "Naveen",
    "Swathi", "Praveen", "Divya", "Ramesh", "Sandhya", "Kiran", "Bhavana",
    "Sai Kumar", "Manjula", "Vijay", "Pooja", "Ashok", "Rekha", "Harish",
    "Sneha",
]
LAST_NAMES = [
    "Reddy", "Rao", "Naidu", "Sharma", "Goud", "Kumar", "Chowdary", "Prasad",
    "Varma", "Yadav", "Murthy", "Pillai", "Iyer", "Devi", "Naik",
]
BRANDS = [
    "Britannia", "Parle", "ITC", "Coca-Cola", "Dabur", "Amul", "Nestle",
    "Hindustan Unilever",
]
DEAL_TYPES = ["listing", "shelf-display", "promotion", "volume-discount"]
DEAL_STAGES = ["active", "closed-won", "negotiating"]


def rand_phone():
    return "9" + "".join(str(random.randint(0, 9)) for _ in range(9))


def to_date(value):
    return value.date() if hasattr(value, "date") else value


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    manifest = {
        "source_store_id": SOURCE_STORE_ID,
        "end_date": END_DATE.isoformat(),
        "target_stores": {},
        "inventory_movement_ids": [],
    }

    try:
        cur.execute(
            """
            SELECT inv.product_id, pr.price, pr.mrp
            FROM kirana_oltp.inventory inv
            JOIN kirana_oltp.pricing pr
              ON pr.product_id = inv.product_id
             AND pr.store_id = inv.store_id
             AND pr.valid_to IS NULL
            WHERE inv.store_id = %s AND inv.quantity > 0
            """,
            (SOURCE_STORE_ID,),
        )
        source_catalogue = cur.fetchall()
        if len(source_catalogue) < 10:
            raise RuntimeError("source store has too few priced, in-stock products")

        cur.execute(
            "SELECT store_id, created_at FROM kirana_oltp.store WHERE store_id = ANY(%s)",
            (TARGET_STORE_IDS,),
        )
        store_created = {r["store_id"]: to_date(r["created_at"]) for r in cur.fetchall()}

        for store_id in TARGET_STORE_IDS:
            start_date = store_created[store_id]
            if start_date > END_DATE:
                start_date = END_DATE
            span_days = max((END_DATE - start_date).days, 1)

            sm = {
                "pricing_inserts": [],   # [pricing_id, ...]              -> delete on rollback
                "pricing_updates": [],   # [{pricing_id, old_price, old_mrp}] -> restore on rollback
                "inventory_ids": [],
                "customer_ids": [],
                "order_ids": [],
                "footfall_ids": [],
                "crm_deal_ids": [],
            }

            # 1. products: pick a slice of store 25's catalogue, carry price over
            #    unchanged, give this store its own (random) stock level.
            n_products = min(random.randint(*PRODUCTS_PER_STORE), len(source_catalogue))
            chosen = random.sample(source_catalogue, n_products)
            product_price = {}
            for row in chosen:
                pid, price, mrp = row["product_id"], row["price"], row["mrp"]
                product_price[pid] = float(price)

                cur.execute(
                    """
                    SELECT pricing_id, price, mrp FROM kirana_oltp.pricing
                    WHERE store_id = %s AND product_id = %s AND valid_to IS NULL
                    """,
                    (store_id, pid),
                )
                existing = cur.fetchone()
                if existing:
                    sm["pricing_updates"].append(
                        {
                            "pricing_id": existing["pricing_id"],
                            "old_price": str(existing["price"]),
                            "old_mrp": str(existing["mrp"]),
                        }
                    )
                    cur.execute(
                        "UPDATE kirana_oltp.pricing SET price = %s, mrp = %s WHERE pricing_id = %s",
                        (price, mrp, existing["pricing_id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO kirana_oltp.pricing (product_id, store_id, price, mrp, valid_from, valid_to)
                        VALUES (%s, %s, %s, %s, %s, NULL) RETURNING pricing_id
                        """,
                        (pid, store_id, price, mrp, start_date),
                    )
                    sm["pricing_inserts"].append(cur.fetchone()["pricing_id"])

                qty = random.randint(40, 250)
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.inventory (store_id, product_id, quantity)
                    VALUES (%s, %s, %s) RETURNING inventory_id
                    """,
                    (store_id, pid, qty),
                )
                sm["inventory_ids"].append(cur.fetchone()["inventory_id"])

            # 2. customers
            n_customers = random.randint(*CUSTOMERS_PER_STORE)
            customer_ids = []
            for _ in range(n_customers):
                name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.customer (name, phone, store_id, created_at)
                    VALUES (%s, %s, %s, %s) RETURNING customer_id
                    """,
                    (name, rand_phone(), store_id, start_date),
                )
                cid = cur.fetchone()["customer_id"]
                customer_ids.append(cid)
                sm["customer_ids"].append(cid)

            # 3. orders + order_items, spread from registration date to END_DATE
            n_orders = random.randint(*ORDERS_PER_STORE)
            product_ids = list(product_price.keys())
            for _ in range(n_orders):
                order_day = start_date + timedelta(days=random.randint(0, span_days))
                order_dt = datetime.combine(order_day, datetime.min.time()) + timedelta(
                    hours=random.randint(9, 20), minutes=random.randint(0, 59)
                )
                customer_id = random.choice(customer_ids) if random.random() < 0.7 else None
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.orders
                        (store_id, order_date, total_amount, order_channel, order_status,
                         customer_id, udhaar_amount, cash_paid)
                    VALUES (%s, %s, 0, 'walk_in', 'completed', %s, 0, 0)
                    RETURNING order_id
                    """,
                    (store_id, order_dt, customer_id),
                )
                order_id = cur.fetchone()["order_id"]
                sm["order_ids"].append(order_id)

                n_lines = random.randint(1, min(4, len(product_ids)))
                line_products = random.sample(product_ids, n_lines)
                total = 0.0
                for pid in line_products:
                    price = product_price[pid]
                    qty = random.randint(1, 5)
                    cost = round(price * 0.75, 2)
                    cur.execute(
                        """
                        INSERT INTO kirana_oltp.order_item
                            (order_id, product_id, quantity, unit_price, cost_price)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (order_id, pid, qty, price, cost),
                    )
                    total += price * qty
                cur.execute(
                    "UPDATE kirana_oltp.orders SET total_amount = %s, cash_paid = %s WHERE order_id = %s",
                    (round(total, 2), round(total, 2), order_id),
                )

            # 4. footfall -- one representative daily entry
            for d in range(span_days + 1):
                day = start_date + timedelta(days=d)
                ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=12)
                visitors = random.randint(15, 60)
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.footfall (store_id, ts, hour, visitors)
                    VALUES (%s, %s, 12, %s)
                    ON CONFLICT (store_id, ts) DO NOTHING
                    RETURNING footfall_id
                    """,
                    (store_id, ts, visitors),
                )
                row = cur.fetchone()
                if row:
                    sm["footfall_ids"].append(row["footfall_id"])

            # 5. a couple of small CRM brand deals
            n_deals = random.randint(*CRM_DEALS_PER_STORE)
            for _ in range(n_deals):
                stage = random.choice(DEAL_STAGES)
                opened = start_date + timedelta(days=random.randint(0, span_days))
                closed = None
                if stage == "closed-won":
                    closed = min(opened + timedelta(days=random.randint(3, 20)), END_DATE)
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.crm_deals
                        (store_id, brand_name, deal_type, deal_value, stage, opened_at, closed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING deal_id
                    """,
                    (
                        store_id,
                        random.choice(BRANDS),
                        random.choice(DEAL_TYPES),
                        round(random.uniform(2000, 15000), 2),
                        stage,
                        opened,
                        closed,
                    ),
                )
                sm["crm_deal_ids"].append(cur.fetchone()["deal_id"])

            manifest["target_stores"][str(store_id)] = sm
            print(
                f"store {store_id}: {len(sm['pricing_inserts'])} new prices, "
                f"{len(sm['pricing_updates'])} prices aligned, "
                f"{len(sm['inventory_ids'])} stocked products, "
                f"{len(sm['customer_ids'])} customers, {len(sm['order_ids'])} orders, "
                f"{len(sm['footfall_ids'])} footfall days, {len(sm['crm_deal_ids'])} CRM deals"
            )

        # the sale trigger writes inventory_movements rows with no FK back to
        # orders -- capture them now so rollback can remove them too.
        all_order_ids = [
            oid for sm in manifest["target_stores"].values() for oid in sm["order_ids"]
        ]
        if all_order_ids:
            cur.execute(
                """
                SELECT movement_id FROM kirana_oltp.inventory_movements
                WHERE reason = 'sale' AND reference_id = ANY(%s)
                """,
                (all_order_ids,),
            )
            manifest["inventory_movement_ids"] = [r["movement_id"] for r in cur.fetchall()]

        conn.commit()
        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"\nDone. Manifest written to {MANIFEST_PATH}")
        print("Run crm_demo_rollback.py against this manifest to undo.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
