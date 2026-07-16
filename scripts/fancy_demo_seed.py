#!/usr/bin/env python3
"""
Seed realistic demo data for Vani Fancy Stores (store_id=53).

Context:
  - store_type=fancy_gift, vertical_code=general
  - Registered: 2026-06-25 (5 days ago as of 2026-06-30)
  - Owner: gurumurthy upputuri (user_id=83)

What gets seeded:
  - 14 new global products (on top of 6 that already exist)
  - Pricing + inventory for all 20 products
  - 12 customers (added across the 5 days)
  - 2 staff + daily attendance
  - 3 suppliers + 2 purchases (initial stock) + 1 restock
  - 68 orders spread realistically (opening-day buzz → weekend peak → Mon drop → Tue partial)
  - Khata (udhaar) for 4 customers + partial repayments for 2
  - Loyalty config + points transactions
  - 1 launch coupon (VANI10)
  - 1 gift bundle basket (Celebration Pack)
  - 5 job cards (gift-wrapping / pre-orders)
  - Daily footfall for all 6 days
  - 2 CRM brand deals

Rollback: python scripts/fancy_demo_rollback.py

Usage:
    python scripts/fancy_demo_seed.py
    AZURE_DB_URL="postgresql://..." python scripts/fancy_demo_seed.py
"""

import json
import os
import random
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

STORE_ID = 53
USER_ID = 83  # gurumurthy upputuri
REG_DATE = date(2026, 6, 25)
TODAY = date(2026, 6, 30)

DB_URL = os.environ.get("AZURE_DB_URL") or os.environ.get("DATABASE_URL")
if not DB_URL:
    raise SystemExit("Set AZURE_DB_URL (or DATABASE_URL) — no hardcoded DB credentials.")

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "fancy_demo_manifest.json")

# ── Existing global products (general vertical) ─────────────────────────────
EXISTING_PRODUCTS = {
    12502: {"name": "Gift Box",      "price": 130.0, "mrp": 150.0, "cost": 90.0,  "stock": 120},
    12527: {"name": "Wall Clock",    "price": 299.0, "mrp": 349.0, "cost": 200.0, "stock": 40},
    12530: {"name": "Balloons Pack", "price": 35.0,  "mrp": 40.0,  "cost": 20.0,  "stock": 200},
    12538: {"name": "Notebook",      "price": 80.0,  "mrp": 90.0,  "cost": 50.0,  "stock": 150},
    12537: {"name": "Pen Pack",      "price": 45.0,  "mrp": 50.0,  "cost": 28.0,  "stock": 180},
    12512: {"name": "Toy Car",       "price": 160.0, "mrp": 180.0, "cost": 110.0, "stock": 60},
}

# ── New products to add to global catalog ───────────────────────────────────
# (category_id, name, unit, price, mrp, cost, stock)
NEW_PRODUCTS = [
    # Gifts (148)
    (148, "Gift Hamper",       "pcs", 399.0, 450.0, 270.0, 50),
    (148, "Greeting Cards",    "pcs",  25.0,  30.0,  12.0, 250),
    (148, "Gift Wrap Set",     "pcs",  70.0,  80.0,  40.0, 100),
    # Home & Decor (171)
    (171, "Photo Frame",       "pcs", 175.0, 199.0, 110.0, 60),
    (171, "Candles Set",       "pcs",  99.0, 120.0,  60.0, 80),
    (171, "Decorative Vase",   "pcs", 220.0, 249.0, 140.0, 35),
    # Party Supplies (176)
    (176, "Party Hat Set",     "pcs",  50.0,  60.0,  28.0, 120),
    (176, "Streamers Pack",    "pcs",  40.0,  50.0,  22.0, 150),
    (176, "Paper Cups Pack",   "pcs",  30.0,  35.0,  15.0, 160),
    # Stationery (182)
    (182, "Marker Set",        "pcs",  99.0, 120.0,  65.0, 80),
    (182, "Sticky Notes",      "pcs",  49.0,  60.0,  28.0, 130),
    # Toys (157)
    (157, "Puzzle Set",        "pcs", 220.0, 249.0, 150.0, 45),
    (157, "Soft Toy Bear",     "pcs", 270.0, 299.0, 180.0, 40),
    (157, "Board Game",        "pcs", 399.0, 450.0, 280.0, 30),
]

# ── Customers (added progressively across the 5 days) ───────────────────────
CUSTOMER_DATA = [
    ("Priya Reddy",     "9848012345", REG_DATE),
    ("Kavitha Sharma",  "9848023456", REG_DATE),
    ("Ravi Kumar",      "9848034567", REG_DATE + timedelta(days=1)),
    ("Sunitha Naidu",   "9848045678", REG_DATE + timedelta(days=1)),
    ("Mahesh Rao",      "9848056789", REG_DATE + timedelta(days=1)),
    ("Ananya Pillai",   "9848067890", REG_DATE + timedelta(days=2)),
    ("Vijay Chowdary",  "9848078901", REG_DATE + timedelta(days=2)),
    ("Lakshmi Devi",    "9848089012", REG_DATE + timedelta(days=2)),
    ("Srinivas Murthy", "9848090123", REG_DATE + timedelta(days=3)),
    ("Deepa Varma",     "9848001235", REG_DATE + timedelta(days=3)),
    ("Naveen Yadav",    "9848011236", REG_DATE + timedelta(days=4)),
    ("Bhavana Iyer",    "9848022347", REG_DATE + timedelta(days=4)),
]

# ── Orders per day (realistic arc) ──────────────────────────────────────────
# Thu Jun 25 opening buzz, Fri steady, Sat/Sun weekend peak, Mon drop, Tue partial
ORDERS_PER_DAY = {
    REG_DATE:                          12,
    REG_DATE + timedelta(days=1):      10,
    REG_DATE + timedelta(days=2):      16,
    REG_DATE + timedelta(days=3):      18,
    REG_DATE + timedelta(days=4):       8,
    REG_DATE + timedelta(days=5):       4,  # today — only morning
}

# ── Footfall per day ─────────────────────────────────────────────────────────
FOOTFALL_PER_DAY = {
    REG_DATE:                          42,
    REG_DATE + timedelta(days=1):      33,
    REG_DATE + timedelta(days=2):      60,
    REG_DATE + timedelta(days=3):      68,
    REG_DATE + timedelta(days=4):      27,
    REG_DATE + timedelta(days=5):      15,  # today — morning only
}

# ── Staff ────────────────────────────────────────────────────────────────────
STAFF_DATA = [
    ("Ramu Naik",    "9949001122", "cashier",   0.0),
    ("Preethi Rao",  "9949003344", "sales",     2.0),
]

# ── Suppliers ────────────────────────────────────────────────────────────────
SUPPLIER_DATA = [
    ("Sri Sai Gift Emporium",    "Suresh Babu",    "9900112233", "Gifts & Decor"),
    ("Krishna Stationery House", "Krishna Rao",    "9900223344", "Stationery & Toys"),
    ("Decor Palace",             "Ramesh Naidu",   "9900334455", "Home Decor & Party"),
]

# ── Job cards (pre-orders / gift wrapping) ───────────────────────────────────
# customer_idx refers to CUSTOMER_DATA list index
JOB_CARDS = [
    {
        "customer_idx":  0,   # Priya Reddy
        "job_type":      "pre_order",
        "item_desc":     "Gift hamper with custom message card",
        "details":       "Birthday gift for husband — gold foil wrap, personalised card, 2 chocolates inside",
        "charge":        150.0,
        "status":        "delivered",
        "day_offset":    0,
        "promised_offset": 2,
    },
    {
        "customer_idx":  6,   # Vijay Chowdary
        "job_type":      "pre_order",
        "item_desc":     "Board Game set x5 — corporate gifting",
        "details":       "5 units, company logo sticker on each box, deliver to office",
        "charge":        650.0,
        "status":        "in_progress",
        "day_offset":    2,
        "promised_offset": 8,   # Jul 3
    },
    {
        "customer_idx":  1,   # Kavitha Sharma
        "job_type":      "pre_order",
        "item_desc":     "Silver anniversary gift hamper",
        "details":       "Premium hamper — photo frame, candles set, greeting card, decorative wrap",
        "charge":        500.0,
        "status":        "delivered",
        "day_offset":    1,
        "promised_offset": 4,
    },
    {
        "customer_idx":  8,   # Srinivas Murthy
        "job_type":      "pre_order",
        "item_desc":     "Baby shower gift basket",
        "details":       "Soft toy bear + photo frame + balloons pack in a decorated basket",
        "charge":        350.0,
        "status":        "received",
        "day_offset":    3,
        "promised_offset": 7,   # Jul 2
    },
    {
        "customer_idx":  4,   # Mahesh Rao
        "job_type":      "pre_order",
        "item_desc":     "Farewell gift pack x10 — office colleagues",
        "details":       "10x Pen Pack + 10x Notebook, individually gift-boxed, name sticker on each",
        "charge":        1200.0,
        "status":        "confirmed",
        "day_offset":    3,
        "promised_offset": 9,   # Jul 4
    },
]

# ── CRM brand deals ───────────────────────────────────────────────────────────
CRM_DEALS = [
    {
        "brand_name":  "Archies Gallery",
        "deal_type":   "listing",
        "deal_value":  8500.0,
        "stage":       "active",
        "day_offset":  1,
    },
    {
        "brand_name":  "Faber-Castell",
        "deal_type":   "promotion",
        "deal_value":  4200.0,
        "stage":       "negotiating",
        "day_offset":  3,
    },
]


def dt(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


def main():
    random.seed(42)
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    manifest = {
        "store_id": STORE_ID,
        "new_product_ids": [],
        "pricing_ids": [],
        "inventory_ids": [],
        "customer_ids": [],
        "staff_ids": [],
        "staff_attendance_ids": [],
        "supplier_ids": [],
        "purchase_ids": [],
        "khata_ids": [],
        "loyalty_config_inserted": False,
        "loyalty_txn_ids": [],
        "coupon_ids": [],
        "basket_ids": [],
        "job_card_ids": [],
        "footfall_ids": [],
        "order_ids": [],
        "crm_deal_ids": [],
        "inventory_movement_ids": [],
    }

    try:
        # ── 1. Add new global products ──────────────────────────────────────
        print("Seeding products...")
        all_products = dict(EXISTING_PRODUCTS)  # pid -> {name, price, mrp, cost, stock}

        for (cat_id, name, unit, price, mrp, cost, stock) in NEW_PRODUCTS:
            cur.execute(
                """
                INSERT INTO kirana_oltp.product (category_id, name, unit, brand)
                VALUES (%s, %s, %s, 'Generic')
                RETURNING product_id
                """,
                (cat_id, name, unit),
            )
            pid = cur.fetchone()["product_id"]
            manifest["new_product_ids"].append(pid)
            all_products[pid] = {"name": name, "price": price, "mrp": mrp, "cost": cost, "stock": stock}

        print(f"  {len(NEW_PRODUCTS)} new products created, {len(all_products)} total in catalogue")

        # ── 2. Pricing + inventory for store 53 ─────────────────────────────
        print("Seeding pricing and inventory...")
        for pid, info in all_products.items():
            cur.execute(
                """
                INSERT INTO kirana_oltp.pricing (product_id, store_id, price, mrp, valid_from)
                VALUES (%s, %s, %s, %s, %s) RETURNING pricing_id
                """,
                (pid, STORE_ID, info["price"], info["mrp"], REG_DATE),
            )
            manifest["pricing_ids"].append(cur.fetchone()["pricing_id"])

            cur.execute(
                """
                INSERT INTO kirana_oltp.inventory (store_id, product_id, quantity)
                VALUES (%s, %s, %s) RETURNING inventory_id
                """,
                (STORE_ID, pid, info["stock"]),
            )
            manifest["inventory_ids"].append(cur.fetchone()["inventory_id"])

        print(f"  {len(all_products)} products priced and stocked")

        # ── 3. Customers ─────────────────────────────────────────────────────
        print("Seeding customers...")
        customer_ids = []
        for name, phone, added_date in CUSTOMER_DATA:
            cur.execute(
                """
                INSERT INTO kirana_oltp.customer (name, phone, store_id, created_at)
                VALUES (%s, %s, %s, %s) RETURNING customer_id
                """,
                (name, phone, STORE_ID, added_date),
            )
            cid = cur.fetchone()["customer_id"]
            customer_ids.append(cid)
            manifest["customer_ids"].append(cid)

        print(f"  {len(customer_ids)} customers")

        # ── 4. Staff + attendance ─────────────────────────────────────────────
        print("Seeding staff...")
        staff_ids = []
        for name, phone, role, commission in STAFF_DATA:
            cur.execute(
                """
                INSERT INTO kirana_oltp.staff (store_id, name, phone, role, commission_pct, is_active, created_at)
                VALUES (%s, %s, %s, %s, %s, true, %s) RETURNING staff_id
                """,
                (STORE_ID, name, phone, role, commission, datetime.combine(REG_DATE, datetime.min.time())),
            )
            sid = cur.fetchone()["staff_id"]
            staff_ids.append(sid)
            manifest["staff_ids"].append(sid)

        for d_offset in range((TODAY - REG_DATE).days + 1):
            att_date = REG_DATE + timedelta(days=d_offset)
            for sid in staff_ids:
                check_in  = dt(att_date, 9, random.randint(0, 15))
                check_out = dt(att_date, 20, random.randint(0, 30)) if att_date < TODAY else None
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.staff_attendance
                        (staff_id, store_id, att_date, status, check_in, check_out)
                    VALUES (%s, %s, %s, 'present', %s, %s) RETURNING id
                    """,
                    (sid, STORE_ID, att_date, check_in, check_out),
                )
                manifest["staff_attendance_ids"].append(cur.fetchone()["id"])

        print(f"  {len(staff_ids)} staff, {len(manifest['staff_attendance_ids'])} attendance records")

        # ── 5. Suppliers ──────────────────────────────────────────────────────
        print("Seeding suppliers...")
        supplier_ids = []
        for name, contact, phone, category in SUPPLIER_DATA:
            cur.execute(
                """
                INSERT INTO kirana_oltp.supplier (name, contact, store_id, phone, category)
                VALUES (%s, %s, %s, %s, %s) RETURNING supplier_id
                """,
                (name, contact, STORE_ID, phone, category),
            )
            sid = cur.fetchone()["supplier_id"]
            supplier_ids.append(sid)
            manifest["supplier_ids"].append(sid)

        # ── 6. Purchases (initial stock Jun 25 + restock Jun 28) ─────────────
        print("Seeding purchases...")
        product_list = list(all_products.keys())

        # Purchase 1: Sri Sai Gift Emporium — opening stock (paid)
        gift_products = [pid for pid in product_list
                         if all_products[pid]["name"] in (
                             "Gift Box", "Gift Hamper", "Greeting Cards",
                             "Gift Wrap Set", "Wall Clock", "Photo Frame",
                             "Candles Set", "Decorative Vase",
                         )]
        p1_items = [(pid, random.randint(20, 40), round(all_products[pid]["cost"], 2))
                    for pid in gift_products]
        p1_total = round(sum(qty * cost for _, qty, cost in p1_items), 2)

        cur.execute(
            """
            INSERT INTO kirana_oltp.purchases
                (supplier_id, store_id, order_date, arrival_date, status,
                 total_amount, due_date, payment_status, notes)
            VALUES (%s, %s, %s, %s, 'received', %s, %s, 'paid', 'Opening stock')
            RETURNING purchase_id
            """,
            (supplier_ids[0], STORE_ID, dt(REG_DATE, 8), dt(REG_DATE, 10),
             p1_total, REG_DATE + timedelta(days=15)),
        )
        p1_id = cur.fetchone()["purchase_id"]
        manifest["purchase_ids"].append(p1_id)
        for pid, qty, cost in p1_items:
            cur.execute(
                """
                INSERT INTO kirana_oltp.purchase_items
                    (purchase_id, product_id, quantity, cost_price, requested_qty)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (p1_id, pid, qty, cost, qty),
            )

        # Purchase 2: Krishna Stationery House — opening stock (paid)
        stat_toy_products = [pid for pid in product_list
                             if all_products[pid]["name"] in (
                                 "Notebook", "Pen Pack", "Marker Set", "Sticky Notes",
                                 "Toy Car", "Puzzle Set", "Soft Toy Bear", "Board Game",
                             )]
        p2_items = [(pid, random.randint(15, 35), round(all_products[pid]["cost"], 2))
                    for pid in stat_toy_products]
        p2_total = round(sum(qty * cost for _, qty, cost in p2_items), 2)

        cur.execute(
            """
            INSERT INTO kirana_oltp.purchases
                (supplier_id, store_id, order_date, arrival_date, status,
                 total_amount, due_date, payment_status, notes)
            VALUES (%s, %s, %s, %s, 'received', %s, %s, 'paid', 'Opening stock')
            RETURNING purchase_id
            """,
            (supplier_ids[1], STORE_ID, dt(REG_DATE, 8), dt(REG_DATE, 11),
             p2_total, REG_DATE + timedelta(days=15)),
        )
        p2_id = cur.fetchone()["purchase_id"]
        manifest["purchase_ids"].append(p2_id)
        for pid, qty, cost in p2_items:
            cur.execute(
                """
                INSERT INTO kirana_oltp.purchase_items
                    (purchase_id, product_id, quantity, cost_price, requested_qty)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (p2_id, pid, qty, cost, qty),
            )

        # Purchase 3: Decor Palace — weekend restock (unpaid, due Jul 5)
        party_products = [pid for pid in product_list
                          if all_products[pid]["name"] in (
                              "Balloons Pack", "Party Hat Set", "Streamers Pack", "Paper Cups Pack",
                          )]
        restock_date = REG_DATE + timedelta(days=3)  # Jun 28 Sun
        p3_items = [(pid, random.randint(50, 100), round(all_products[pid]["cost"], 2))
                    for pid in party_products]
        p3_total = round(sum(qty * cost for _, qty, cost in p3_items), 2)

        cur.execute(
            """
            INSERT INTO kirana_oltp.purchases
                (supplier_id, store_id, order_date, arrival_date, status,
                 total_amount, due_date, payment_status, notes)
            VALUES (%s, %s, %s, %s, 'received', %s, %s, 'unpaid', 'Weekend restock — party items')
            RETURNING purchase_id
            """,
            (supplier_ids[2], STORE_ID, dt(restock_date, 9), dt(restock_date, 14),
             p3_total, TODAY + timedelta(days=5)),
        )
        p3_id = cur.fetchone()["purchase_id"]
        manifest["purchase_ids"].append(p3_id)
        for pid, qty, cost in p3_items:
            cur.execute(
                """
                INSERT INTO kirana_oltp.purchase_items
                    (purchase_id, product_id, quantity, cost_price, requested_qty)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (p3_id, pid, qty, cost, qty),
            )

        print(f"  {len(manifest['purchase_ids'])} purchases (2 paid, 1 unpaid Rs.{p3_total})")

        # ── 7. Orders + order_items ───────────────────────────────────────────
        print("Seeding orders...")

        # Weight cheaper/popular items higher in random selection
        popular   = [pid for pid in product_list
                     if all_products[pid]["price"] <= 80]
        mid_range = [pid for pid in product_list
                     if 80 < all_products[pid]["price"] <= 200]
        premium   = [pid for pid in product_list
                     if all_products[pid]["price"] > 200]

        def pick_order_products() -> list:
            n = random.randint(1, 3)
            pool = (popular * 4) + (mid_range * 2) + premium
            return random.sample(pool, min(n, len(pool)))

        # Khata targets: 4 customers get udhaar on specific orders
        # We'll mark the first order of these customers as udhaar
        udhaar_customers = {
            customer_ids[0]: 450.0,   # Priya Reddy
            customer_ids[2]: 280.0,   # Ravi Kumar
            customer_ids[5]: 150.0,   # Ananya Pillai
            customer_ids[8]: 320.0,   # Srinivas Murthy
        }
        udhaar_order_ids = {}  # customer_id -> order_id (first udhaar order)

        for day, n_orders in ORDERS_PER_DAY.items():
            # Business hours: 10am–8pm on weekdays, 10am–9pm weekends
            is_weekend = day.weekday() >= 5  # Sat=5, Sun=6
            close_hour = 21 if is_weekend else 20

            for i in range(n_orders):
                hour = random.randint(10, close_hour - 1)
                minute = random.randint(0, 59)
                order_dt = dt(day, hour, minute)

                # 70% of orders are from known customers
                cid = random.choice(customer_ids) if random.random() < 0.7 else None

                # Build order items first to know total
                line_products = pick_order_products()
                total = 0.0
                lines = []
                for pid in line_products:
                    price = all_products[pid]["price"]
                    cost  = all_products[pid]["cost"]
                    qty   = random.randint(1, 3)
                    lines.append((pid, qty, price, cost))
                    total += price * qty
                total = round(total, 2)

                # Decide if this is a udhaar order
                is_udhaar = (
                    cid in udhaar_customers
                    and cid not in udhaar_order_ids
                    and day >= REG_DATE + timedelta(days=1)  # not on day 1
                )
                udhaar_amt = round(total, 2) if is_udhaar else 0.0
                cash_paid  = 0.0 if is_udhaar else total

                cur.execute(
                    """
                    INSERT INTO kirana_oltp.orders
                        (store_id, user_id, order_date, total_amount, order_channel,
                         order_status, customer_id, udhaar_amount, cash_paid)
                    VALUES (%s, %s, %s, %s, 'walk_in', 'completed', %s, %s, %s)
                    RETURNING order_id
                    """,
                    (STORE_ID, USER_ID, order_dt, total, cid, udhaar_amt, cash_paid),
                )
                oid = cur.fetchone()["order_id"]
                manifest["order_ids"].append(oid)

                if is_udhaar:
                    udhaar_order_ids[cid] = oid

                for pid, qty, price, cost in lines:
                    cur.execute(
                        """
                        INSERT INTO kirana_oltp.order_item
                            (order_id, product_id, quantity, unit_price, cost_price)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (oid, pid, qty, price, cost),
                    )

        print(f"  {len(manifest['order_ids'])} orders seeded")

        # ── 8. Khata (udhaar) + partial repayments ────────────────────────────
        print("Seeding khata...")
        khata_ids = {}
        for cid, amt in udhaar_customers.items():
            oid = udhaar_order_ids.get(cid)
            issue_date = REG_DATE + timedelta(days=1)
            due_date   = issue_date + timedelta(days=14)
            cur.execute(
                """
                INSERT INTO kirana_oltp.khata
                    (customer_id, store_id, amount, amount_paid, issue_date, due_date, status, order_id)
                VALUES (%s, %s, %s, 0, %s, %s, 'open', %s) RETURNING khata_id
                """,
                (cid, STORE_ID, amt, issue_date, due_date, oid),
            )
            kid = cur.fetchone()["khata_id"]
            khata_ids[cid] = kid
            manifest["khata_ids"].append(kid)

        # Priya Reddy and Ananya Pillai partially paid back
        partial_payments = [
            (customer_ids[0], 200.0, REG_DATE + timedelta(days=3), "Cash payment"),
            (customer_ids[5],  50.0, REG_DATE + timedelta(days=4), "UPI payment"),
        ]
        for cid, paid_amt, paid_date, note in partial_payments:
            kid = khata_ids[cid]
            paid_at = datetime.combine(paid_date, datetime.min.time()).replace(
                hour=11, tzinfo=timezone.utc
            )
            cur.execute(
                """
                INSERT INTO kirana_oltp.khata_payments (khata_id, store_id, amount, paid_at, notes)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (kid, STORE_ID, paid_amt, paid_at, note),
            )
            cur.execute(
                "UPDATE kirana_oltp.khata SET amount_paid = amount_paid + %s WHERE khata_id = %s",
                (paid_amt, kid),
            )

        print(f"  {len(khata_ids)} khata entries, 2 partial repayments")

        # ── 9. Loyalty config + transactions ─────────────────────────────────
        print("Seeding loyalty...")
        cur.execute(
            """
            INSERT INTO kirana_oltp.loyalty_config
                (store_id, is_active, points_per_100, redeem_paise_per_point,
                 silver_threshold, gold_threshold)
            VALUES (%s, true, 2, 100, 300, 1500)
            ON CONFLICT (store_id) DO UPDATE
              SET is_active=true, points_per_100=2, redeem_paise_per_point=100
            """,
            (STORE_ID,),
        )
        manifest["loyalty_config_inserted"] = True

        # Award points for orders linked to known customers
        cur.execute(
            "SELECT order_id, customer_id, total_amount, order_date FROM kirana_oltp.orders "
            "WHERE store_id=%s AND customer_id IS NOT NULL ORDER BY order_date",
            (STORE_ID,),
        )
        loyalty_orders = cur.fetchall()
        for row in loyalty_orders:
            pts = int(float(row["total_amount"]) / 100 * 2)
            if pts <= 0:
                continue
            cur.execute(
                """
                INSERT INTO kirana_oltp.loyalty_transaction
                    (store_id, customer_id, order_id, points, kind, note, created_at)
                VALUES (%s, %s, %s, %s, 'earn', 'Points earned on purchase', %s)
                RETURNING txn_id
                """,
                (STORE_ID, row["customer_id"], row["order_id"], pts, row["order_date"]),
            )
            manifest["loyalty_txn_ids"].append(cur.fetchone()["txn_id"])

        print(f"  loyalty config set, {len(manifest['loyalty_txn_ids'])} point transactions")

        # ── 10. Launch coupon ─────────────────────────────────────────────────
        print("Seeding coupon...")
        cur.execute(
            """
            INSERT INTO kirana_oltp.coupon
                (store_id, code, discount_type, value, min_order, max_discount,
                 valid_from, valid_to, usage_limit, used_count, is_active)
            VALUES (%s, 'VANI10', 'percent', 10, 200, 100,
                    %s, %s, 100, 0, true)
            RETURNING coupon_id
            """,
            (STORE_ID, REG_DATE, REG_DATE + timedelta(days=30)),
        )
        manifest["coupon_ids"].append(cur.fetchone()["coupon_id"])

        # ── 11. Gift bundle basket ────────────────────────────────────────────
        print("Seeding basket...")
        # Celebration Pack = Gift Box + Balloons Pack + Greeting Cards
        basket_products = [
            (12502, "Gift Box",       1),
            (12530, "Balloons Pack",  1),
        ]
        # Find Greeting Cards pid
        greeting_pid = next(
            pid for pid in manifest["new_product_ids"]
            if all_products[pid]["name"] == "Greeting Cards"
        )
        basket_products.append((greeting_pid, "Greeting Cards", 1))

        gross = sum(all_products[pid]["price"] * qty for pid, _, qty in basket_products)
        bundle_price = round(gross * 0.90, 2)  # 10% bundle saving

        cur.execute(
            """
            INSERT INTO kirana_oltp.basket
                (store_id, name, description, price, valid_from, is_active,
                 gross_total, discount_pct)
            VALUES (%s, 'Celebration Pack',
                    'Gift Box + Balloons Pack + Greeting Cards — bundled at 10%% off',
                    %s, %s, true, %s, 10)
            RETURNING basket_id
            """,
            (STORE_ID, bundle_price, REG_DATE, round(gross, 2)),
        )
        bid = cur.fetchone()["basket_id"]
        manifest["basket_ids"].append(bid)

        for pid, pname, qty in basket_products:
            cur.execute(
                """
                INSERT INTO kirana_oltp.basket_item (basket_id, product_id, product_name, qty)
                VALUES (%s, %s, %s, %s)
                """,
                (bid, pid, pname, qty),
            )

        print(f"  basket 'Celebration Pack' @ Rs.{bundle_price}")

        # ── 12. Job cards ─────────────────────────────────────────────────────
        print("Seeding job cards...")
        for jc in JOB_CARDS:
            cid = customer_ids[jc["customer_idx"]]
            cname, cphone, _ = CUSTOMER_DATA[jc["customer_idx"]]
            created = dt(REG_DATE + timedelta(days=jc["day_offset"]), 11)
            promised = REG_DATE + timedelta(days=jc["promised_offset"])
            created_tz = created.replace(tzinfo=timezone.utc)
            cur.execute(
                """
                INSERT INTO kirana_oltp.job_card
                    (store_id, customer_id, customer_name, customer_phone,
                     job_type, item_desc, details, charge, status, promised_date, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING job_id
                """,
                (
                    STORE_ID, cid, cname, cphone,
                    jc["job_type"], jc["item_desc"], jc["details"],
                    jc["charge"], jc["status"], promised, created_tz,
                ),
            )
            manifest["job_card_ids"].append(cur.fetchone()["job_id"])

        print(f"  {len(manifest['job_card_ids'])} job cards")

        # ── 13. Footfall ──────────────────────────────────────────────────────
        print("Seeding footfall...")
        for day, visitors in FOOTFALL_PER_DAY.items():
            # Spread across 3 peak hours
            for hour in [11, 14, 17]:
                portion = visitors // 3 + (visitors % 3 if hour == 17 else 0)
                ts = dt(day, hour)
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.footfall (store_id, ts, hour, visitors)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (store_id, ts) DO NOTHING
                    RETURNING footfall_id
                    """,
                    (STORE_ID, ts, hour, portion),
                )
                row = cur.fetchone()
                if row:
                    manifest["footfall_ids"].append(row["footfall_id"])

        print(f"  {len(manifest['footfall_ids'])} footfall slots")

        # ── 14. CRM brand deals ───────────────────────────────────────────────
        print("Seeding CRM deals...")
        for deal in CRM_DEALS:
            opened = dt(REG_DATE + timedelta(days=deal["day_offset"]), 10)
            cur.execute(
                """
                INSERT INTO kirana_oltp.crm_deals
                    (store_id, brand_name, deal_type, deal_value, stage, opened_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING deal_id
                """,
                (STORE_ID, deal["brand_name"], deal["deal_type"],
                 deal["deal_value"], deal["stage"], opened),
            )
            manifest["crm_deal_ids"].append(cur.fetchone()["deal_id"])

        print(f"  {len(manifest['crm_deal_ids'])} CRM deals")

        # ── 15. Capture inventory movements created by trigger ────────────────
        if manifest["order_ids"]:
            cur.execute(
                """
                SELECT movement_id FROM kirana_oltp.inventory_movements
                WHERE reason = 'sale' AND reference_id = ANY(%s)
                """,
                (manifest["order_ids"],),
            )
            manifest["inventory_movement_ids"] = [r["movement_id"] for r in cur.fetchall()]

        conn.commit()

        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2)

        print(f"\nDone. Manifest -> {MANIFEST_PATH}")
        print(f"  {len(manifest['new_product_ids'])} new products | "
              f"{len(manifest['customer_ids'])} customers | "
              f"{len(manifest['order_ids'])} orders | "
              f"{len(manifest['staff_ids'])} staff | "
              f"{len(manifest['supplier_ids'])} suppliers | "
              f"{len(manifest['khata_ids'])} udhaar | "
              f"{len(manifest['job_card_ids'])} job cards | "
              f"{len(manifest['inventory_movement_ids'])} inv-movements (trigger)")
        print("  To undo: python scripts/fancy_demo_rollback.py")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
