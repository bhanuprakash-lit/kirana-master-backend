#!/usr/bin/env python3
"""
store27_sales_sim.py
====================
6-month sales simulation for Store #27 – Sujatha General Stores, Kothaguda, Hyderabad.

What this script does (touches ONLY store_id=27):
  1. Selects 250 products from global catalog (spread across all major categories)
  2. Creates 1 supplier for store 27
  3. Adds inventory + pricing + product_supplier for each product
  4. Creates 20 customers with real Indian names
  5. Generates 6 months of sales (Nov 20 2025 → May 19 2026)
     - Customer profiles: regular, impulse, occasional, bulk, credit, inactive
     - Shopping calendar: weekday / weekend / Indian holiday / festival boosts
     - Basket range: ₹100 – ₹4000
     - Payment methods: UPI, cash, card, khata (credit customers)
"""

import random
import sys
import psycopg2
from datetime import datetime, timedelta, date
from decimal import Decimal

random.seed(2025)

# ── DB ─────────────────────────────────────────────────────────────────────────
DB = dict(dbname="lit_db", user="postgres", password="123456",
          host="localhost", port="5432")
STORE_ID      = 27
STORE_USER_ID = 58          # bhanuprakash

START_DATE      = date(2025, 11, 20)
END_DATE        = date(2026, 5, 19)
INACTIVE_CUTOFF = date(2026, 3, 19)   # inactive customers: no sale after this

# ── 20 customers ───────────────────────────────────────────────────────────────
# (name, phone, email, profile, household_size)
CUSTOMERS_DEF = [
    # REGULAR – shop 4-6x/week, medium basket
    ("Rajesh Kumar",       "9876543201", "rajesh.kumar@kirana.local",       "regular",    4),
    ("Priya Sharma",       "9876543202", "priya.sharma@kirana.local",       "regular",    3),
    ("Anita Reddy",        "9876543203", "anita.reddy@kirana.local",        "regular",    5),
    ("Suresh Patel",       "9876543204", "suresh.patel@kirana.local",       "regular",    3),
    # IMPULSE – buy on weekends / festivals / mood
    ("Meena Krishnaswamy", "9876543205", "meena.krishnaswamy@kirana.local", "impulse",    2),
    ("Venkat Rao",         "9876543206", "venkat.rao@kirana.local",         "impulse",    1),
    ("Kavitha Nair",       "9876543207", "kavitha.nair@kirana.local",       "impulse",    2),
    # OCCASIONAL – 2-3 trips/month
    ("Ramesh Gupta",       "9876543208", "ramesh.gupta@kirana.local",       "occasional", 4),
    ("Sunita Joshi",       "9876543209", "sunita.joshi@kirana.local",       "occasional", 3),
    ("Harish Chandra",     "9876543210", "harish.chandra@kirana.local",     "occasional", 5),
    ("Lakshmi Devi",       "9876543211", "lakshmi.devi@kirana.local",       "occasional", 4),
    # BULK – big-basket every 2-3 weeks
    ("Mohan Das",          "9876543212", "mohan.das@kirana.local",          "bulk",       6),
    ("Saritha Iyer",       "9876543213", "saritha.iyer@kirana.local",       "bulk",       5),
    ("Ashok Singh",        "9876543214", "ashok.singh@kirana.local",        "bulk",       7),
    # CREDIT (khata) – frequent but pays on credit
    ("Uma Rani",           "9876543215", "uma.rani@kirana.local",           "credit",     3),
    ("Deepak Mehta",       "9876543216", "deepak.mehta@kirana.local",       "credit",     4),
    ("Rekha Agarwal",      "9876543217", "rekha.agarwal@kirana.local",      "credit",     3),
    # INACTIVE – no sales in last 2 months
    ("Srinivas Murthy",    "9876543218", "srinivas.murthy@kirana.local",    "inactive",   4),
    ("Pooja Verma",        "9876543219", "pooja.verma@kirana.local",        "inactive",   2),
    ("Arun Kumar",         "9876543220", "arun.kumar@kirana.local",         "inactive",   3),
]

# ── Indian festivals / holidays (Hyderabad context) ────────────────────────────
FESTIVALS: dict[date, tuple[str, float]] = {
    date(2025, 11,  1): ("Telangana Formation Day",   1.4),
    date(2025, 11,  5): ("Chhath Puja",               1.3),
    date(2025, 11, 15): ("Guru Nanak Jayanti",         1.2),
    date(2025, 12, 25): ("Christmas",                  1.3),
    date(2025, 12, 31): ("New Year Eve",               1.6),
    date(2026,  1,  1): ("New Year Day",               1.5),
    date(2026,  1, 13): ("Pre-Sankranti Shopping",     1.6),
    date(2026,  1, 14): ("Makar Sankranti / Pongal",   1.9),
    date(2026,  1, 15): ("Sankranti Day-2",            1.5),
    date(2026,  1, 26): ("Republic Day",               1.2),
    date(2026,  2, 14): ("Valentine's Day",            1.2),
    date(2026,  2, 26): ("Maha Shivaratri",            1.3),
    date(2026,  3, 14): ("Holi",                       1.4),
    date(2026,  3, 29): ("Pre-Ugadi Shopping",         1.8),
    date(2026,  3, 30): ("Ugadi",                      2.1),  # biggest in Hyd
    date(2026,  3, 31): ("Eid al-Fitr",                1.7),
    date(2026,  4,  1): ("Eid Holiday",                1.4),
    date(2026,  4,  6): ("Ram Navami",                 1.2),
    date(2026,  4, 14): ("Baisakhi / Tamil New Year",  1.3),
    date(2026,  5,  1): ("Labour Day",                 1.1),
}

# ── Category price ranges (INR) ────────────────────────────────────────────────
CAT_PRICE = {
    "Rice":                       (60,  350),
    "Basmati Rice":               (100, 500),
    "Flour":                      (40,  200),
    "Dal":                        (60,  250),
    "Dry Fruits":                 (80,  600),
    "Powdered Masala":            (30,  200),
    "Whole Spices":               (20,  180),
    "Oil":                        (120, 600),
    "Ghee & Vanaspati":           (150, 800),
    "Salt, Sugar & Jaggery":      (20,  100),
    "Chips & Crisps":             (20,   60),
    "Namkeen Snacks":             (20,   80),
    "Cookies":                    (20,  100),
    "Cream Biscuits":             (15,   60),
    "Bhujia & Mixtures":          (20,   80),
    "Popcorn":                    (20,   60),
    "Nachos":                     (30,   80),
    "Healthy Snacks":             (30,  150),
    "Soft Drinks":                (20,   70),
    "Fruit Juices":               (30,  150),
    "Leaf & Dust Tea":            (50,  400),
    "Coffee":                     (100, 600),
    "Cold Coffee":                (30,  150),
    "Energy Drinks":              (50,  150),
    "Milk Drinks":                (30,  100),
    "Cocktail Mixers/Tonic Waters": (80, 300),
    "Soaps":                      (30,  200),
    "Oral Care":                  (50,  300),
    "Hair Oil":                   (80,  400),
    "Hair Oil, Masks & Serums":   (100, 600),
    "Handwash":                   (60,  250),
    "Feminine Care":              (60,  300),
    "Face Cream & Gel":           (100, 600),
    "Floor Cleaners & More":      (50,  300),
    "Toilet Cleaners & More":     (60,  300),
    "Air Fresheners":             (80,  400),
    "Garbage Bags":               (40,  200),
    "Dishwashing Accessories":    (30,  200),
    "Noodles":                    (15,   60),
    "Pasta":                      (30,  150),
    "Ready to Eat":               (40,  250),
    "Instant Mixes":              (40,  200),
    "Bread":                      (30,   80),
    "Oats":                       (60,  300),
    "Curd & Yogurt":              (30,  150),
    "Ice Cream & Frozen Dessert": (30,  200),
    "Fresh Vegetables":           (10,   80),
    "Fruits":                     (40,  200),
    "Chutney & Pickle":           (50,  200),
    "Cooking Sauces & Paste":     (40,  200),
    "Dips & Spreads":             (50,  300),
    "Staples":                    (50,  300),
    "Snacks & Biscuits":          (20,  100),
    "Beverages":                  (20,  200),
    "Dairy":                      (30,  200),
    "Personal Care":              (50,  500),
    "Household":                  (50,  300),
    "Cleaning & Household":       (50,  300),
    "Dairy & Breakfast":          (40,  200),
    "Instant Food":               (30,  200),
    "Sauces & Spreads":           (40,  250),
    "Pickles":                    (50,  200),
    "Tea Powder":                 (50,  400),
    "Dish Liquid":                (30,  200),
}
DEFAULT_PRICE = (30, 200)

# ── Customer shopping probability per day ──────────────────────────────────────
# (weekday_prob, weekend_prob, festival_override)
PROFILE_PARAMS = {
    "regular":    dict(weekday_p=0.75, weekend_p=0.90, items_range=(5, 12),  basket_range=(350, 1500)),
    "impulse":    dict(weekday_p=0.15, weekend_p=0.55, items_range=(2,  6),  basket_range=(150,  800)),
    "occasional": dict(weekday_p=0.10, weekend_p=0.25, items_range=(3,  8),  basket_range=(200,  800)),
    "bulk":       dict(weekday_p=0.08, weekend_p=0.18, items_range=(8, 18),  basket_range=(1000, 4000)),
    "credit":     dict(weekday_p=0.65, weekend_p=0.80, items_range=(4, 10),  basket_range=(300, 1200)),
    "inactive":   dict(weekday_p=0.50, weekend_p=0.70, items_range=(3,  9),  basket_range=(250,  900)),
}

# Festival probability override (multiplier on the base prob)
FESTIVAL_PROB_BOOST = {
    "regular":    1.2,
    "impulse":    2.5,
    "occasional": 1.8,
    "bulk":       1.5,
    "credit":     1.3,
    "inactive":   1.5,
}

# ── Preferred category mix per profile ────────────────────────────────────────
STAPLE_CATS   = frozenset({"Rice", "Basmati Rice", "Flour", "Dal", "Oil", "Ghee & Vanaspati",
                            "Salt, Sugar & Jaggery", "Powdered Masala", "Whole Spices", "Staples"})
SNACK_CATS    = frozenset({"Chips & Crisps", "Namkeen Snacks", "Cookies", "Cream Biscuits",
                            "Bhujia & Mixtures", "Popcorn", "Nachos", "Healthy Snacks", "Snacks & Biscuits"})
BEVERAGE_CATS = frozenset({"Soft Drinks", "Fruit Juices", "Leaf & Dust Tea", "Coffee",
                            "Cold Coffee", "Energy Drinks", "Milk Drinks", "Tea Powder", "Beverages"})
PERSONAL_CATS = frozenset({"Soaps", "Oral Care", "Hair Oil", "Hair Oil, Masks & Serums",
                            "Handwash", "Feminine Care", "Face Cream & Gel", "Personal Care"})
HOUSEHOLD_CATS= frozenset({"Floor Cleaners & More", "Toilet Cleaners & More", "Air Fresheners",
                            "Garbage Bags", "Dishwashing Accessories", "Household", "Cleaning & Household",
                            "Dish Liquid"})
FRESH_CATS    = frozenset({"Fresh Vegetables", "Fruits", "Curd & Yogurt", "Bread", "Oats", "Dairy",
                            "Dairy & Breakfast", "Ice Cream & Frozen Dessert"})
INSTANT_CATS  = frozenset({"Noodles", "Pasta", "Ready to Eat", "Instant Mixes", "Instant Food"})
SAUCE_CATS    = frozenset({"Chutney & Pickle", "Cooking Sauces & Paste", "Dips & Spreads",
                            "Sauces & Spreads", "Pickles"})

# Weight map for picking categories per profile
PROFILE_CAT_WEIGHTS = {
    "regular":    {STAPLE_CATS: 5, PERSONAL_CATS: 2, HOUSEHOLD_CATS: 2, BEVERAGE_CATS: 1, SNACK_CATS: 1, FRESH_CATS: 1},
    "impulse":    {SNACK_CATS: 4, BEVERAGE_CATS: 4, FRESH_CATS: 2, INSTANT_CATS: 2, SAUCE_CATS: 1},
    "occasional": {STAPLE_CATS: 3, SNACK_CATS: 2, BEVERAGE_CATS: 2, PERSONAL_CATS: 2, HOUSEHOLD_CATS: 1},
    "bulk":       {STAPLE_CATS: 6, HOUSEHOLD_CATS: 3, PERSONAL_CATS: 2, BEVERAGE_CATS: 1, SNACK_CATS: 1},
    "credit":     {STAPLE_CATS: 4, FRESH_CATS: 3, PERSONAL_CATS: 2, HOUSEHOLD_CATS: 1, SNACK_CATS: 1},
    "inactive":   {STAPLE_CATS: 3, SNACK_CATS: 2, BEVERAGE_CATS: 2, PERSONAL_CATS: 1, HOUSEHOLD_CATS: 1},
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_price_for_category(cat_name: str) -> float:
    lo, hi = CAT_PRICE.get(cat_name, DEFAULT_PRICE)
    # round to nearest 0.50
    raw = random.uniform(lo, hi)
    return round(raw * 2) / 2


def get_day_factor(d: date) -> float:
    """Return demand multiplier for a given date."""
    if d in FESTIVALS:
        return FESTIVALS[d][1]
    if d.weekday() >= 5:          # Saturday = 5, Sunday = 6
        return 1.35
    if d.weekday() == 4:          # Friday slight uptick
        return 1.10
    return 1.0


def should_customer_shop(profile: str, d: date, day_factor: float,
                         last_shop_day: dict, cid: int) -> bool:
    params = PROFILE_PARAMS[profile]
    is_weekend = d.weekday() >= 5
    base_p = params["weekend_p"] if is_weekend else params["weekday_p"]

    # Festival boost
    if d in FESTIVALS:
        base_p = min(base_p * FESTIVAL_PROB_BOOST[profile], 0.98)

    # Bulk customers: enforce minimum gap of 10 days between shops
    if profile == "bulk":
        last = last_shop_day.get(cid)
        if last and (d - last).days < 10:
            return False

    # Occasional: enforce minimum gap of 7 days
    if profile == "occasional":
        last = last_shop_day.get(cid)
        if last and (d - last).days < 6:
            return False

    # Regular: small gap between daily shops (min 1 day)
    if profile == "regular":
        last = last_shop_day.get(cid)
        if last and (d - last).days < 1:
            return False

    # Credit: similar to regular but slightly less frequent
    if profile == "credit":
        last = last_shop_day.get(cid)
        if last and (d - last).days < 1:
            return False

    return random.random() < base_p


def pick_products_for_basket(profile: str, all_products: list[dict],
                             products_by_cat: dict[str, list[dict]],
                             n_items: int) -> list[dict]:
    """Pick n_items products weighted by profile category preferences."""
    cat_weights = PROFILE_CAT_WEIGHTS[profile]
    # Build flat list of (product, weight) from preferred categories
    candidates = []
    for cat_set, w in cat_weights.items():
        for cat in cat_set:
            for p in products_by_cat.get(cat, []):
                candidates.append((p, w))
    # Fallback: any product
    if len(candidates) < n_items:
        for p in all_products:
            candidates.append((p, 1))

    if not candidates:
        return []

    prods_seen = set()
    chosen = []
    weights = [c[1] for c in candidates]
    total_w = sum(weights)
    norm = [w / total_w for w in weights]

    attempts = 0
    while len(chosen) < n_items and attempts < n_items * 10:
        attempts += 1
        idx = random.choices(range(len(candidates)), weights=norm, k=1)[0]
        p = candidates[idx][0]
        if p["product_id"] not in prods_seen:
            prods_seen.add(p["product_id"])
            chosen.append(p)
    return chosen


def build_basket(profile: str, products_for_basket: list[dict],
                 basket_min: float, basket_max: float,
                 inventory: dict[int, int]) -> list[tuple]:
    """Returns list of (product_id, qty, unit_price, cost_price)."""
    items = []
    total = 0.0

    # Bulk profile: higher per-product qty
    qty_ranges = {
        "regular":    (1, 3),
        "impulse":    (1, 2),
        "occasional": (1, 3),
        "bulk":       (2, 6),
        "credit":     (1, 4),
        "inactive":   (1, 3),
    }

    for p in products_for_basket:
        pid   = p["product_id"]
        stock = inventory.get(pid, 0)
        if stock <= 0:
            continue

        lo, hi = qty_ranges[profile]
        qty = random.randint(lo, hi)
        qty = min(qty, stock)
        if qty <= 0:
            continue

        price     = float(p["price"])
        cost      = float(p["cost_price"])
        line_val  = qty * price

        # Cap basket at basket_max
        if total + line_val > basket_max * 1.15:
            # try qty=1
            if total + price > basket_max * 1.15:
                continue
            qty = 1
            line_val = price

        items.append((pid, qty, price, cost))
        total += qty * price

        if total >= basket_min:
            # keep adding with 40% chance to grow basket further
            if random.random() < 0.40:
                continue
            else:
                break

    return items if total >= basket_min else []


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: Setup products, supplier, inventory, pricing
# ──────────────────────────────────────────────────────────────────────────────

def setup_store(conn):
    cur = conn.cursor()
    print("\n[1/4] Setting up supplier, products, inventory, pricing for store 27 ...")

    # ── Supplier ──────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT supplier_id FROM kirana_oltp.supplier WHERE store_id = %s LIMIT 1
    """, (STORE_ID,))
    row = cur.fetchone()
    if row:
        supplier_id = row[0]
        print(f"    Existing supplier: {supplier_id}")
    else:
        cur.execute("""
            INSERT INTO kirana_oltp.supplier (name, contact, store_id, phone, category)
            VALUES ('Hyderabad Wholesale Traders', 'Ramakrishna Rao', %s,
                    '9000001111', 'General')
            RETURNING supplier_id
        """, (STORE_ID,))
        supplier_id = cur.fetchone()[0]
        print(f"    Created supplier: {supplier_id}")

    # ── Existing product_ids in store 27 ──────────────────────────────────────
    cur.execute("""
        SELECT product_id FROM kirana_oltp.inventory WHERE store_id = %s
    """, (STORE_ID,))
    existing_pids = {r[0] for r in cur.fetchall()}
    print(f"    Products already in store: {len(existing_pids)}")

    # ── Pick 250 products from global catalog (spread across categories) ──────
    # Strategy: sample up to 12 products per category from top categories
    cur.execute("""
        SELECT c.category_id, c.name, COUNT(p.product_id) as cnt
        FROM kirana_oltp.category c
        JOIN kirana_oltp.product p ON p.category_id = c.category_id
        GROUP BY c.category_id, c.name
        HAVING COUNT(p.product_id) >= 3
        ORDER BY cnt DESC
        LIMIT 60
    """)
    categories = cur.fetchall()  # (category_id, name, cnt)

    target_per_cat = max(4, 250 // len(categories) + 2)
    selected_pids = list(existing_pids)  # start with already-existing

    for cat_id, cat_name, _ in categories:
        if len(selected_pids) >= 260:
            break
        cur.execute("""
            SELECT product_id FROM kirana_oltp.product
            WHERE category_id = %s
            ORDER BY RANDOM()
            LIMIT %s
        """, (cat_id, target_per_cat + 5))
        pids = [r[0] for r in cur.fetchall()]
        for pid in pids:
            if pid not in existing_pids and len(selected_pids) < 260:
                selected_pids.append(pid)

    # Final trim to 250
    new_pids = [p for p in selected_pids if p not in existing_pids]
    new_pids = new_pids[:250 - len(existing_pids)]
    print(f"    New products to add: {len(new_pids)}")

    # ── Get product info + existing pricing from any store ────────────────────
    if not new_pids:
        print("    No new products needed.")
    else:
        # Fetch existing price reference from other stores
        cur.execute("""
            SELECT pr.product_id, AVG(pr.price)::numeric(12,2),
                   AVG(pr.mrp)::numeric(12,2)
            FROM kirana_oltp.pricing pr
            WHERE pr.product_id = ANY(%s)
              AND pr.store_id != %s
              AND pr.valid_to IS NULL
            GROUP BY pr.product_id
        """, (new_pids, STORE_ID))
        ref_prices = {r[0]: (float(r[1]), float(r[2]) if r[2] else None)
                      for r in cur.fetchall()}

        cur.execute("""
            SELECT p.product_id, p.name, p.unit, p.weight, c.name as cat_name
            FROM kirana_oltp.product p
            JOIN kirana_oltp.category c ON c.category_id = p.category_id
            WHERE p.product_id = ANY(%s)
        """, (new_pids,))
        prod_info = {r[0]: {"name": r[1], "unit": r[2],
                             "weight": float(r[3] or 1), "cat": r[4]}
                     for r in cur.fetchall()}

        added = 0
        now_ts = datetime(2025, 11, 19, 0, 0, 0)

        for pid in new_pids:
            info = prod_info.get(pid)
            if not info:
                continue

            cat = info["cat"]
            # Determine price
            if pid in ref_prices:
                base_price = ref_prices[pid][0] * random.uniform(0.95, 1.05)
            else:
                lo, hi = CAT_PRICE.get(cat, DEFAULT_PRICE)
                base_price = random.uniform(lo, hi)

            base_price = round(base_price * 2) / 2  # round to .00 or .50
            mrp        = round(base_price * random.uniform(1.05, 1.15) * 2) / 2
            cost_price = round(base_price * random.uniform(0.80, 0.92) * 2) / 2

            # Inventory quantity
            lo_q, hi_q = (80, 150) if cat in (STAPLE_CATS | BEVERAGE_CATS) else (50, 100)
            qty = random.randint(lo_q, hi_q)

            # Insert inventory
            cur.execute("""
                INSERT INTO kirana_oltp.inventory (store_id, product_id, quantity)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (STORE_ID, pid, qty))

            # Insert pricing
            cur.execute("""
                INSERT INTO kirana_oltp.pricing
                    (product_id, store_id, price, mrp, valid_from, valid_to)
                VALUES (%s, %s, %s, %s, %s, NULL)
                ON CONFLICT DO NOTHING
            """, (pid, STORE_ID, base_price, mrp, now_ts))

            # Insert product_supplier
            cur.execute("""
                INSERT INTO kirana_oltp.product_supplier
                    (product_id, supplier_id, cost_price, lead_time_days)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (pid, supplier_id, cost_price, random.randint(1, 3)))

            added += 1

        print(f"    Added {added} products with inventory + pricing.")

    # Make sure existing 4 products have product_supplier entry
    cur.execute("""
        SELECT product_id FROM kirana_oltp.inventory WHERE store_id = %s
    """, (STORE_ID,))
    all_store_pids = [r[0] for r in cur.fetchall()]
    for pid in all_store_pids:
        cur.execute("""
            SELECT 1 FROM kirana_oltp.product_supplier ps
            JOIN kirana_oltp.supplier s ON s.supplier_id = ps.supplier_id
            WHERE ps.product_id = %s AND s.store_id = %s LIMIT 1
        """, (pid, STORE_ID))
        if not cur.fetchone():
            cur.execute("""
                SELECT price FROM kirana_oltp.pricing
                WHERE product_id = %s AND store_id = %s LIMIT 1
            """, (pid, STORE_ID))
            pr = cur.fetchone()
            cost = float(pr[0]) * 0.85 if pr else 50.0
            cur.execute("""
                INSERT INTO kirana_oltp.product_supplier
                    (product_id, supplier_id, cost_price, lead_time_days)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (pid, supplier_id, round(cost, 2), 2))

    conn.commit()
    print("    Supplier / product setup committed.")
    cur.close()
    return supplier_id


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2: Create 20 customers
# ──────────────────────────────────────────────────────────────────────────────

def create_customers(conn) -> dict[str, int]:
    """Returns dict {profile: [customer_id, ...]}."""
    cur = conn.cursor()
    print("\n[2/4] Creating 20 customers ...")

    profile_map: dict[str, list[int]] = {}
    for (name, phone, email, profile, hsize) in CUSTOMERS_DEF:
        cur.execute("""
            SELECT customer_id FROM kirana_oltp.customer
            WHERE phone = %s AND store_id = %s
        """, (phone, STORE_ID))
        row = cur.fetchone()
        if row:
            cid = row[0]
        else:
            cur.execute("""
                INSERT INTO kirana_oltp.customer
                    (name, phone, email, store_id, household_size, referral_count, created_at)
                VALUES (%s, %s, %s, %s, %s, 0, %s)
                RETURNING customer_id
            """, (name, phone, email, STORE_ID, hsize,
                  datetime(2025, 11, 1)))
            cid = cur.fetchone()[0]
        profile_map.setdefault(profile, []).append(cid)
        print(f"    {profile:10s}  {name:22s}  cid={cid}")

    conn.commit()
    cur.close()
    return profile_map


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Load product catalogue into memory
# ──────────────────────────────────────────────────────────────────────────────

def load_products(conn) -> tuple[list[dict], dict[str, list[dict]], dict[int, int]]:
    cur = conn.cursor()
    cur.execute("""
        SELECT p.product_id, p.name, pr.price, pr.mrp,
               ps.cost_price, c.name as cat_name
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product  p  ON p.product_id = i.product_id
        JOIN kirana_oltp.pricing  pr ON pr.product_id = p.product_id
                                     AND pr.store_id = i.store_id
                                     AND pr.valid_to IS NULL
        JOIN kirana_oltp.product_supplier ps
             ON ps.product_id = p.product_id
        JOIN kirana_oltp.supplier s ON s.supplier_id = ps.supplier_id
                                    AND s.store_id = %s
        JOIN kirana_oltp.category c  ON c.category_id = p.category_id
        WHERE i.store_id = %s
    """, (STORE_ID, STORE_ID))
    rows = cur.fetchall()

    all_products = []
    products_by_cat: dict[str, list[dict]] = {}
    inventory: dict[int, int] = {}

    for r in rows:
        pid, name, price, mrp, cost, cat = r
        p = {
            "product_id":  pid,
            "name":        name,
            "price":       float(price),
            "mrp":         float(mrp or price),
            "cost_price":  float(cost),
            "cat":         cat,
        }
        all_products.append(p)
        products_by_cat.setdefault(cat, []).append(p)

    # Load current inventory
    cur.execute("""
        SELECT product_id, quantity FROM kirana_oltp.inventory WHERE store_id = %s
    """, (STORE_ID,))
    for pid, qty in cur.fetchall():
        inventory[pid] = qty

    cur.close()
    print(f"\n[3/4] Loaded {len(all_products)} products from store 27 catalogue.")
    return all_products, products_by_cat, inventory


# ──────────────────────────────────────────────────────────────────────────────
# Phase 4: Run 6-month simulation
# ──────────────────────────────────────────────────────────────────────────────

def restock_if_needed(cur, inventory: dict[int, int], supplier_id: int,
                      d: date, hour: int = 7) -> None:
    """Auto-restock products below threshold."""
    restock_ts = datetime(d.year, d.month, d.day, hour,
                          random.randint(0, 30), 0)
    arrival_ts = restock_ts + timedelta(days=random.randint(1, 2))

    low_stock = [(pid, qty) for pid, qty in inventory.items() if qty < 15]
    if not low_stock:
        return

    cur.execute("""
        INSERT INTO kirana_oltp.purchases
            (supplier_id, store_id, order_date, arrival_date, status)
        VALUES (%s, %s, %s, %s, 'received')
        RETURNING purchase_id
    """, (supplier_id, STORE_ID, restock_ts, arrival_ts))
    purchase_id = cur.fetchone()[0]

    for pid, curr_qty in low_stock:
        refill = random.randint(40, 80)
        cur.execute("""
            INSERT INTO kirana_oltp.purchase_items
                (purchase_id, product_id, quantity, cost_price)
            SELECT %s, %s, %s, ps.cost_price
            FROM kirana_oltp.product_supplier ps
            JOIN kirana_oltp.supplier s ON s.supplier_id = ps.supplier_id
            WHERE ps.product_id = %s AND s.store_id = %s
            LIMIT 1
        """, (purchase_id, pid, refill, pid, STORE_ID))

        cur.execute("""
            UPDATE kirana_oltp.inventory
            SET quantity = quantity + %s
            WHERE store_id = %s AND product_id = %s
        """, (refill, STORE_ID, pid))

        inventory[pid] = curr_qty + refill

        cur.execute("""
            INSERT INTO kirana_oltp.inventory_movements
                (store_id, product_id, change_quantity, reason, reference_id)
            VALUES (%s, %s, %s, 'purchase', %s)
        """, (STORE_ID, pid, refill, purchase_id))


def place_order(cur, inventory: dict[int, int],
                customer_id: int, profile: str,
                order_dt: datetime,
                all_products: list[dict],
                products_by_cat: dict[str, list[dict]]) -> float | None:
    """Create one order. Returns basket total or None if nothing sold."""
    params = PROFILE_PARAMS[profile]
    n_items = random.randint(*params["items_range"])
    b_min, b_max = params["basket_range"]

    candidates = pick_products_for_basket(profile, all_products,
                                          products_by_cat, n_items + 6)
    basket_items = build_basket(profile, candidates, b_min, b_max, inventory)

    if not basket_items:
        return None

    total = sum(qty * price for _, qty, price, _ in basket_items)

    cur.execute("""
        INSERT INTO kirana_oltp.orders
            (store_id, user_id, customer_id, order_status,
             order_date, total_amount, order_channel)
        VALUES (%s, %s, %s, 'completed', %s, %s, 'walk_in')
        RETURNING order_id
    """, (STORE_ID, STORE_USER_ID, customer_id, order_dt, round(total, 2)))
    order_id = cur.fetchone()[0]

    for pid, qty, price, cost in basket_items:
        cur.execute("""
            INSERT INTO kirana_oltp.order_item
                (order_id, product_id, quantity, unit_price, cost_price)
            VALUES (%s, %s, %s, %s, %s)
        """, (order_id, pid, qty, price, cost))

        cur.execute("""
            UPDATE kirana_oltp.inventory
            SET quantity = GREATEST(0, quantity - %s)
            WHERE store_id = %s AND product_id = %s
        """, (qty, STORE_ID, pid))
        inventory[pid] = max(0, inventory.get(pid, 0) - qty)

        cur.execute("""
            INSERT INTO kirana_oltp.inventory_movements
                (store_id, product_id, change_quantity, reason, reference_id)
            VALUES (%s, %s, %s, 'sale', %s)
        """, (STORE_ID, pid, -qty, order_id))

    # ── Payment ───────────────────────────────────────────────────────────────
    if profile == "credit":
        method = random.choices(
            ["upi", "cash", "khata"],
            weights=[20, 15, 65], k=1)[0]
    else:
        method = random.choices(
            ["upi", "cash", "card"],
            weights=[60, 30, 10], k=1)[0]

    if method == "khata":
        due_date = (order_dt + timedelta(days=30)).date()
        cur.execute("""
            INSERT INTO kirana_oltp.khata
                (customer_id, store_id, order_id, amount, amount_paid,
                 issue_date, due_date, status)
            VALUES (%s, %s, %s, %s, 0, %s, %s, 'pending')
        """, (customer_id, STORE_ID, order_id, round(total, 2),
              order_dt.date(), due_date))
        # record a payment of 0 (on credit)
        cur.execute("""
            INSERT INTO kirana_oltp.payments
                (order_id, amount, payment_method, status, created_at)
            VALUES (%s, %s, 'khata', 'pending', %s)
        """, (order_id, round(total, 2), order_dt))
    else:
        cur.execute("""
            INSERT INTO kirana_oltp.payments
                (order_id, amount, payment_method, status, created_at)
            VALUES (%s, %s, %s, 'paid', %s)
        """, (order_id, round(total, 2), method, order_dt))

    return total


def reset_simulation_data(conn, customer_ids: list[int]) -> None:
    """Delete any previously generated simulation data for store 27 customers."""
    cur = conn.cursor()
    print("\n[Pre-sim] Cleaning up any previous simulation data ...")

    if customer_ids:
        # Orders (and cascade: order_items, payments via FK if exists; else manual)
        cur.execute("""
            SELECT order_id FROM kirana_oltp.orders
            WHERE store_id = %s AND customer_id = ANY(%s)
        """, (STORE_ID, customer_ids))
        order_ids = [r[0] for r in cur.fetchall()]

        if order_ids:
            cur.execute("DELETE FROM kirana_oltp.khata    WHERE order_id = ANY(%s)", (order_ids,))
            cur.execute("DELETE FROM kirana_oltp.payments WHERE order_id = ANY(%s)", (order_ids,))
            cur.execute("DELETE FROM kirana_oltp.order_item WHERE order_id = ANY(%s)", (order_ids,))
            cur.execute("DELETE FROM kirana_oltp.orders   WHERE order_id = ANY(%s)", (order_ids,))
            print(f"    Removed {len(order_ids)} old orders.")

    # Inventory movements for store 27 (sales + purchases from simulation)
    cur.execute("""
        DELETE FROM kirana_oltp.inventory_movements WHERE store_id = %s
    """, (STORE_ID,))

    # Inventory snapshots for store 27 in simulation range
    cur.execute("""
        DELETE FROM kirana_oltp.inventory_snapshots
        WHERE store_id = %s AND snapshot_date >= %s
    """, (STORE_ID, START_DATE))

    # Purchases generated by simulation
    cur.execute("""
        DELETE FROM kirana_oltp.purchase_items pi
        USING kirana_oltp.purchases p
        WHERE pi.purchase_id = p.purchase_id AND p.store_id = %s
    """, (STORE_ID,))
    cur.execute("DELETE FROM kirana_oltp.purchases WHERE store_id = %s", (STORE_ID,))

    # Reset inventory to starting levels (high enough for 6-month simulation)
    cur.execute("""
        UPDATE kirana_oltp.inventory i
        SET quantity = CASE
            WHEN c.name IN ('Rice','Basmati Rice','Flour','Dal','Oil','Ghee & Vanaspati',
                            'Salt, Sugar & Jaggery','Powdered Masala','Whole Spices',
                            'Soft Drinks','Fruit Juices','Noodles','Soaps','Leaf & Dust Tea')
                 THEN 200
            WHEN c.name IN ('Chips & Crisps','Namkeen Snacks','Cookies','Cream Biscuits',
                            'Snacks & Biscuits','Beverages','Coffee','Energy Drinks',
                            'Milk Drinks','Handwash','Oral Care')
                 THEN 150
            ELSE 100
        END
        FROM kirana_oltp.product p
        JOIN kirana_oltp.category c ON c.category_id = p.category_id
        WHERE i.product_id = p.product_id AND i.store_id = %s
    """, (STORE_ID,))

    conn.commit()
    print("    Inventory reset to simulation starting levels.")
    cur.close()


def simulate_sales(conn, profile_map: dict[str, list[int]],
                   all_products: list[dict],
                   products_by_cat: dict[str, list[dict]],
                   inventory: dict[int, int],
                   supplier_id: int) -> None:
    cur = conn.cursor()
    print("\n[4/4] Simulating 6 months of sales ...")
    print(f"      Period: {START_DATE}  to  {END_DATE}")

    # Ensure extra snapshot columns exist before the loop begins
    for ddl in [
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS units_sold  NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS stock       INT",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS revenue     NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS profit      NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS price       NUMERIC(10,2)",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS promo_flag  BOOLEAN",
    ]:
        cur.execute(ddl)
    conn.commit()

    # Flatten customer list with profile
    customers: list[tuple[int, str]] = []
    for profile, cids in profile_map.items():
        for cid in cids:
            customers.append((cid, profile))

    last_shop_day: dict[int, date] = {}
    total_orders = 0
    total_revenue = 0.0

    d = START_DATE
    while d <= END_DATE:
        day_factor = get_day_factor(d)

        # Restock every Monday
        if d.weekday() == 0:
            restock_if_needed(cur, inventory, supplier_id, d)

        day_orders = 0

        for cid, profile in customers:
            # Inactive customers stop shopping after the cutoff
            effective_profile = profile
            if profile == "inactive" and d > INACTIVE_CUTOFF:
                continue

            if not should_customer_shop(effective_profile, d,
                                        day_factor, last_shop_day, cid):
                continue

            # Random hour: kirana store open 7AM – 10PM
            hour   = random.randint(7, 21)
            minute = random.randint(0, 59)
            sec    = random.randint(0, 59)
            order_dt = datetime(d.year, d.month, d.day, hour, minute, sec)

            total = place_order(cur, inventory, cid, effective_profile,
                                order_dt, all_products, products_by_cat)
            if total:
                last_shop_day[cid] = d
                day_orders  += 1
                total_revenue += total

        total_orders += day_orders

        # Daily snapshot — populate sales metrics from today's orders
        cur.execute("""
            INSERT INTO kirana_oltp.inventory_snapshots
                (snapshot_date, store_id, product_id,
                 stock_on_hand, units_sold, stock, revenue, profit, price)
            SELECT
                %s                               AS snapshot_date,
                i.store_id,
                i.product_id,
                i.quantity                       AS stock_on_hand,
                COALESCE(s.units_sold, 0)        AS units_sold,
                i.quantity                       AS stock,
                COALESCE(s.revenue, 0)           AS revenue,
                COALESCE(s.profit, 0)            AS profit,
                pr.price
            FROM kirana_oltp.inventory i
            LEFT JOIN (
                SELECT
                    oi.product_id,
                    SUM(oi.quantity)                                   AS units_sold,
                    SUM(oi.quantity * oi.unit_price)                   AS revenue,
                    SUM((oi.unit_price - oi.cost_price) * oi.quantity) AS profit
                FROM kirana_oltp.order_item oi
                JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
                WHERE o.store_id = %s
                  AND DATE(o.order_date) = %s
                GROUP BY oi.product_id
            ) s ON s.product_id = i.product_id
            LEFT JOIN kirana_oltp.pricing pr
                ON pr.product_id = i.product_id
               AND pr.store_id   = i.store_id
               AND pr.valid_to IS NULL
            WHERE i.store_id = %s
            ON CONFLICT (snapshot_date, store_id, product_id)
            DO UPDATE SET
                stock_on_hand = EXCLUDED.stock_on_hand,
                units_sold    = EXCLUDED.units_sold,
                stock         = EXCLUDED.stock,
                revenue       = EXCLUDED.revenue,
                profit        = EXCLUDED.profit,
                price         = EXCLUDED.price
        """, (d, STORE_ID, d, STORE_ID))

        # Progress log every 30 days
        if (d - START_DATE).days % 30 == 0 or d in FESTIVALS:
            label = FESTIVALS.get(d, ("",))[0]
            fest_tag = f" [{label}]" if label else ""
            print(f"      {d}  orders={day_orders:3d}  "
                  f"factor={day_factor:.2f}{fest_tag}")

        conn.commit()
        d += timedelta(days=1)

    cur.close()
    print(f"\n    Simulation complete.")
    print(f"    Total orders  : {total_orders:,}")
    print(f"    Total revenue : Rs.{total_revenue:,.2f}")


# ──────────────────────────────────────────────────────────────────────────────
# Backfill: rebuild snapshots from existing orders (no new orders created)
# ──────────────────────────────────────────────────────────────────────────────

def backfill_snapshots(conn) -> None:
    """
    Delete all inventory_snapshots for store 27 in the simulation range and
    recompute them from existing order_items + inventory_movements + pricing.

    Stock reconstruction logic:
      current inventory = stock after ALL simulation movements
      eod_stock on day d = current_stock - SUM(movements on dates AFTER d)
    We walk backwards from END_DATE, accumulating future adjustments.
    """
    from collections import defaultdict

    cur = conn.cursor()

    # ── 0. Ensure extra columns exist (original schema only has stock_on_hand) ─
    print("\n[Backfill] Ensuring schema columns exist ...")
    for ddl in [
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS units_sold  NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS stock       INT",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS revenue     NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS profit      NUMERIC(12,2) DEFAULT 0",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS price       NUMERIC(10,2)",
        "ALTER TABLE kirana_oltp.inventory_snapshots ADD COLUMN IF NOT EXISTS promo_flag  BOOLEAN",
    ]:
        cur.execute(ddl)
    conn.commit()
    print("    Columns ready.")

    print("[Backfill] Deleting existing snapshots for store 27 ...")
    cur.execute("""
        DELETE FROM kirana_oltp.inventory_snapshots
        WHERE store_id = %s AND snapshot_date BETWEEN %s AND %s
    """, (STORE_ID, START_DATE, END_DATE))
    print(f"    Deleted {cur.rowcount} rows.")

    # ── 1. Current stock (post-simulation state) ──────────────────────────────
    cur.execute("""
        SELECT product_id, quantity FROM kirana_oltp.inventory WHERE store_id = %s
    """, (STORE_ID,))
    current_stock: dict[int, int] = {r[0]: r[1] for r in cur.fetchall()}
    product_ids = list(current_stock.keys())
    print(f"    Products: {len(product_ids)}")

    # ── 2. All movements with their simulation dates ───────────────────────────
    # created_at is the wall-clock time the script ran, not the simulated date.
    # Derive the date from the linked order_date / arrival_date instead.
    cur.execute("""
        SELECT
            im.product_id,
            im.change_quantity,
            CASE im.reason
                WHEN 'sale'     THEN DATE(o.order_date)
                WHEN 'purchase' THEN DATE(pu.arrival_date)
            END AS movement_date
        FROM kirana_oltp.inventory_movements im
        LEFT JOIN kirana_oltp.orders    o  ON im.reason = 'sale'
                                           AND o.order_id     = im.reference_id
        LEFT JOIN kirana_oltp.purchases pu ON im.reason = 'purchase'
                                           AND pu.purchase_id = im.reference_id
        WHERE im.store_id = %s
    """, (STORE_ID,))
    net_by_date: dict[date, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for pid, delta, move_date in cur.fetchall():
        if move_date is not None:
            net_by_date[move_date][pid] += delta
    print(f"    Movement dates loaded: {len(net_by_date)}")

    # ── 3. Daily sales per product ────────────────────────────────────────────
    # Diagnostic: confirm orders exist
    cur.execute("SELECT COUNT(*) FROM kirana_oltp.orders WHERE store_id = %s", (STORE_ID,))
    order_count = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
        WHERE o.store_id = %s
    """, (STORE_ID,))
    item_count = cur.fetchone()[0]
    print(f"    Orders in DB: {order_count},  Order items: {item_count}")

    cur.execute("""
        SELECT
            DATE(o.order_date)                                  AS sale_date,
            oi.product_id,
            SUM(oi.quantity)                                    AS units_sold,
            SUM(oi.quantity * oi.unit_price)                    AS revenue,
            SUM((oi.unit_price - oi.cost_price) * oi.quantity)  AS profit
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
        WHERE o.store_id = %s
        GROUP BY DATE(o.order_date), oi.product_id
    """, (STORE_ID,))
    rows_raw = cur.fetchall()
    print(f"    Sales query rows returned: {len(rows_raw)}")
    if rows_raw:
        print(f"    Sample row: {rows_raw[0]}")

    # sales_by_date[date][product_id] = (units_sold, revenue, profit)
    sales_by_date: dict[date, dict[int, tuple]] = defaultdict(dict)
    for row in rows_raw:
        sale_date, pid, units, rev, profit = row
        sales_by_date[sale_date][pid] = (int(units), float(rev), float(profit))
    print(f"    Sale dates loaded: {len(sales_by_date)}")

    # ── 4. Current pricing ────────────────────────────────────────────────────
    cur.execute("""
        SELECT product_id, price FROM kirana_oltp.pricing
        WHERE store_id = %s AND valid_to IS NULL
    """, (STORE_ID,))
    prices: dict[int, float] = {r[0]: float(r[1]) for r in cur.fetchall()}

    # ── 5. Reconstruct end-of-day stock walking backwards ────────────────────
    # eod_stock[d][pid] = stock at close of business on day d
    # = current_stock[pid] - SUM(movements on dates strictly after d)
    date_list: list[date] = []
    d = START_DATE
    while d <= END_DATE:
        date_list.append(d)
        d += timedelta(days=1)

    # future_adj[pid] accumulates net movements on dates > current d
    future_adj: dict[int, int] = defaultdict(int)
    eod: dict[date, dict[int, int]] = {}

    for d in reversed(date_list):
        eod[d] = {
            pid: max(current_stock.get(pid, 0) - future_adj[pid], 0)
            for pid in product_ids
        }
        # add today's movements so they're "future" for d-1
        for pid, delta in net_by_date.get(d, {}).items():
            future_adj[pid] += delta

    # ── 6. Insert snapshot rows ───────────────────────────────────────────────
    print("[Backfill] Inserting fresh snapshot rows ...")
    INSERT_SQL = """
        INSERT INTO kirana_oltp.inventory_snapshots
            (snapshot_date, store_id, product_id,
             stock_on_hand, units_sold, stock, revenue, profit, price)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_date, store_id, product_id) DO NOTHING
    """
    inserted = 0
    for d in date_list:
        day_sales = sales_by_date.get(d, {})
        rows = []
        for pid in product_ids:
            qty   = eod[d][pid]
            us, rev, prof = day_sales.get(pid, (0, 0.0, 0.0))
            price = prices.get(pid)
            rows.append((d, STORE_ID, pid, qty, us, qty, rev, prof, price))
        cur.executemany(INSERT_SQL, rows)
        inserted += len(rows)

        if d.month != (d + timedelta(days=1)).month:
            conn.commit()
            print(f"    Committed through {d}  ({inserted:,} rows so far)")

    conn.commit()
    print(f"    Done. {inserted:,} snapshot rows inserted.")
    cur.close()


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    backfill_only = "--backfill" in sys.argv

    if backfill_only:
        print("=" * 60)
        print("Store 27 - Sujatha General Stores  [Snapshot Backfill]")
        print("=" * 60)
    else:
        print("=" * 60)
        print("Store 27 - Sujatha General Stores  [Sales Simulation]")
        print("=" * 60)

    try:
        conn = psycopg2.connect(**DB)
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}")
        sys.exit(1)

    try:
        if backfill_only:
            backfill_snapshots(conn)
        else:
            supplier_id = setup_store(conn)
            profile_map = create_customers(conn)

            all_cids = [cid for cids in profile_map.values() for cid in cids]
            reset_simulation_data(conn, all_cids)

            all_products, products_by_cat, inventory = load_products(conn)

            if not all_products:
                print("ERROR: No products found for store 27 - aborting.")
                sys.exit(1)

            simulate_sales(conn, profile_map, all_products,
                           products_by_cat, inventory, supplier_id)

    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()

    print("\nDone. Store 27 data is ready.")


if __name__ == "__main__":
    main()
