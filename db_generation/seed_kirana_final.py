import psycopg2
import random
from datetime import datetime, timedelta

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"
DB_HOST = "localhost"
DB_PORT = "5432"

random.seed(42)

# -------------------------
# CATEGORY TREE
# -------------------------
CATEGORY_TREE = {
    "Staples": ["Rice", "Atta", "Dal"],
    "Oils & Ghee": ["Refined Oil", "Mustard Oil"],
    "Snacks & Biscuits": ["Biscuits", "Namkeen"],
    "Beverages": ["Tea", "Coffee"],
    "Dairy": ["Milk", "Curd"],
    "Personal Care": ["Shampoo", "Soap"],
    "Household": ["Detergent", "Cleaning"],
}

# -------------------------
# BRAND MAP
# -------------------------
BRANDS = {
    "Rice": ["India Gate", "Daawat"],
    "Atta": ["Aashirvaad", "Pillsbury"],
    "Dal": ["Tata Sampann"],
    "Refined Oil": ["Fortune", "Saffola"],
    "Mustard Oil": ["Dhara"],
    "Biscuits": ["Parle", "Britannia"],
    "Namkeen": ["Haldiram"],
    "Tea": ["Tata Tea", "Red Label"],
    "Coffee": ["Nescafe"],
    "Milk": ["Amul", "Heritage"],
    "Curd": ["Amul", "Nestle"],
    "Shampoo": ["Clinic Plus", "Sunsilk"],
    "Soap": ["Dove", "Lux"],
    "Detergent": ["Surf Excel", "Ariel"],
    "Cleaning": ["Vim"],
}

UNITS = ["kg", "g", "ml", "L", "pcs"]
CURRENT_DATE = datetime.now()
GLOBAL_PRODUCT_COUNT = 300
PRODUCT_VARIANTS = [
    "Classic",
    "Premium",
    "Value Pack",
    "Family Pack",
    "Daily",
    "Select",
    "Gold",
    "Fresh",
    "Super Saver",
    "Mini",
    "Large",
    "Economy",
]

# -------------------------
# SKU TYPE RULES
# -------------------------
def get_stock_range(category):
    if category in ["Rice", "Atta", "Dal"]:
        return (20, 35)
    elif category in ["Refined Oil", "Mustard Oil"]:
        return (20, 40)
    elif category in ["Milk", "Curd"]:
        return (15, 30)
    elif category in ["Biscuits", "Namkeen", "Shampoo"]:
        return (30, 50)
    elif category in ["Tea", "Coffee"]:
        return (20, 30)
    elif category in ["Detergent", "Cleaning"]:
        return (20, 35)
    else:
        return (15, 25)

def get_margin(category):
    if category in ["Rice", "Atta", "Dal"]:
        return random.uniform(1.08, 1.12)
    elif category in ["Refined Oil"]:
        return random.uniform(1.10, 1.15)
    elif category in ["Milk", "Curd"]:
        return random.uniform(1.05, 1.10)
    elif category in ["Biscuits", "Namkeen"]:
        return random.uniform(1.15, 1.25)
    elif category in ["Shampoo"]:
        return random.uniform(1.20, 1.35)
    else:
        return random.uniform(1.12, 1.20)


def get_unit_and_weight(category, is_loose):
    if category in ["Rice", "Atta", "Dal"]:
        unit = "kg" if is_loose else random.choice(["g", "kg"])
        weight = random.choice([1, 5]) if unit == "kg" else random.choice([500, 1000, 5000])
    elif category in ["Refined Oil", "Mustard Oil"]:
        unit = random.choice(["ml", "L"])
        weight = random.choice([500, 1000]) if unit == "ml" else random.choice([1, 5])
    elif category in ["Milk", "Curd"]:
        unit = random.choice(["ml", "L", "g", "kg"])
        weight = random.choice([500, 1000]) if unit in ["ml", "g"] else 1
    elif category in ["Biscuits", "Namkeen", "Tea", "Coffee"]:
        unit = random.choice(["g", "pcs"])
        weight = random.choice([100, 250, 500]) if unit == "g" else random.choice([1, 5, 10])
    elif category in ["Shampoo", "Cleaning"]:
        unit = random.choice(["ml", "L"])
        weight = random.choice([100, 250, 500]) if unit == "ml" else 1
    elif category in ["Soap", "Detergent"]:
        unit = random.choice(["g", "kg", "pcs"])
        weight = random.choice([100, 250, 500]) if unit == "g" else random.choice([1, 2, 5])
    else:
        unit = random.choice(UNITS)
        weight = random.choice([100, 250, 500, 1000])

    return unit, weight


def get_initial_stock(category, unit, weight):
    low, high = get_stock_range(category)

    if category in ["Rice", "Atta", "Dal"]:
        if unit == "kg" and weight >= 5:
            return random.randint(15, 24)
        return random.randint(18, 32)
    if category in ["Refined Oil", "Mustard Oil"]:
        if unit == "L" and weight >= 5:
            return random.randint(15, 22)
        return random.randint(18, 35)
    if category in ["Milk", "Curd"]:
        return random.randint(15, 24)
    if unit == "pcs" or (unit == "g" and weight <= 250) or (unit == "ml" and weight <= 250):
        small_low = max(low, min(high, 24))
        small_high = min(max(high, small_low + 2), 50)
        return random.randint(small_low, small_high)

    return random.randint(low, high)


def build_global_product_catalog(category_ids):
    products = []
    seen_names = set()
    attempts = 0

    while len(products) < GLOBAL_PRODUCT_COUNT:
        attempts += 1
        if attempts > GLOBAL_PRODUCT_COUNT * 100:
            raise RuntimeError("Could not generate enough unique global products")

        subcat = random.choice(list(category_ids.keys()))
        brand = random.choice(BRANDS.get(subcat, ["Generic"]))
        is_loose = random.choice([True, False])
        unit, weight = get_unit_and_weight(subcat, is_loose)
        is_perishable = subcat in ["Milk", "Curd"]
        base_name = f"{brand} {subcat} {weight}{unit}"
        name = base_name

        if name in seen_names:
            variant = PRODUCT_VARIANTS[(attempts + len(products)) % len(PRODUCT_VARIANTS)]
            name = f"{brand} {variant} {subcat} {weight}{unit}"

        if name in seen_names:
            name = f"{base_name} SKU {len(products) + 1:03d}"

        if name in seen_names:
            continue

        seen_names.add(name)
        products.append({
            "category_id": category_ids[subcat],
            "subcat": subcat,
            "name": name,
            "brand": brand,
            "unit": unit,
            "weight": weight,
            "is_loose": is_loose,
            "is_perishable": is_perishable,
        })

    return products

# -------------------------
# MAIN
# -------------------------
def seed():
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    cur = conn.cursor()

    # -------------------------
    # STORES + USERS
    # -------------------------
    stores_data = [
        ("Gurumurthy Kirana", "KPHB, Hyderabad", "9000000001", "gurumurthy"),
        ("Vijay Stores", "Ameerpet, Hyderabad", "9000000002", "vijay_bhaskar"),
        ("Bhanu Mart", "Miyapur, Hyderabad", "9000000003", "bhanuprakash"),
        ("Gnyan Store", "Dilsukhnagar, Hyderabad", "9000000004", "gnyandeep"),
    ]

    store_ids = []
    store_user_ids = {}

    for name, loc, phone, user in stores_data:
        cur.execute("""
            INSERT INTO kirana_oltp.store (name, location, region)
            VALUES (%s, %s, 'Hyderabad')
            RETURNING store_id;
        """, (name, loc))
        store_id = cur.fetchone()[0]
        store_ids.append(store_id)

        cur.execute("""
            INSERT INTO kirana_oltp.users (username, email, role, store_id)
            VALUES (%s, %s, 'owner', %s)
            RETURNING user_id;
        """, (user, f"{user}@kirana.local", store_id))
        store_user_ids[store_id] = cur.fetchone()[0]

    # -------------------------
    # CATEGORIES
    # -------------------------
    category_ids = {}

    for parent, children in CATEGORY_TREE.items():
        cur.execute("""
            INSERT INTO kirana_oltp.category (name)
            VALUES (%s) RETURNING category_id;
        """, (parent,))
        parent_id = cur.fetchone()[0]

        for child in children:
            cur.execute("""
                INSERT INTO kirana_oltp.category (name, parent_category_id)
                VALUES (%s, %s)
                RETURNING category_id;
            """, (child, parent_id))
            category_ids[child] = cur.fetchone()[0]

    # -------------------------
    # PRODUCTS (300 GLOBAL)
    # -------------------------
    product_ids = []

    for i, product in enumerate(build_global_product_catalog(category_ids)):
        cur.execute("""
            INSERT INTO kirana_oltp.product
            (category_id, name, brand, unit, weight, is_loose, is_perishable, sku, barcode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING product_id;
        """, (
            product["category_id"],
            product["name"],
            product["brand"],
            product["unit"],
            product["weight"],
            product["is_loose"],
            product["is_perishable"],
            f"SKU{i}",
            f"89{random.randint(1000000000,9999999999)}"
        ))

        product_ids.append((cur.fetchone()[0], product["subcat"], product["unit"], product["weight"]))

    # -------------------------
    # CUSTOMERS
    # -------------------------
    customer_ids = []

    for store_id in store_ids:
        for i in range(60):
            customer_name = f"Customer_{store_id}_{i + 1}"
            phone = f"98{store_id:01d}{i + 1:08d}"
            email = f"customer_{store_id}_{i + 1}@kirana.local"
            cur.execute("""
                INSERT INTO kirana_oltp.customer (name, phone, email)
                VALUES (%s, %s, %s)
                RETURNING customer_id;
            """, (customer_name, phone, email))
            customer_ids.append(cur.fetchone()[0])

    # -------------------------
    # STORE DATA (200 SKUs each)
    # -------------------------
    for store_id in store_ids:

        # suppliers
        supplier_ids = []
        for i in range(random.randint(3, 5)):
            cur.execute("""
                INSERT INTO kirana_oltp.supplier (name, contact, store_id)
                VALUES (%s, %s, %s)
                RETURNING supplier_id;
            """, (f"Supplier_{store_id}_{i}", f"9{random.randint(100000000,999999999)}", store_id))
            supplier_ids.append(cur.fetchone()[0])

        selected_products = random.sample(product_ids, 200)

        for product_id, subcat, unit, weight in selected_products:

            supplier_id = random.choice(supplier_ids)

            cost = round(random.uniform(10, 500), 2)
            margin = get_margin(subcat)
            selling = round(cost * margin, 2)
            mrp = round(selling * random.uniform(1.02, 1.08), 2)
            valid_from = CURRENT_DATE - timedelta(days=random.randint(20, 120))
            valid_to = valid_from + timedelta(days=random.randint(90, 240))

            # pricing
            cur.execute("""
                INSERT INTO kirana_oltp.pricing
                (product_id, store_id, price, mrp, valid_from, valid_to)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (product_id, store_id, selling, mrp, valid_from, valid_to))

            # supplier map
            cur.execute("""
                INSERT INTO kirana_oltp.product_supplier
                (product_id, supplier_id, cost_price, lead_time_days)
                VALUES (%s, %s, %s, %s);
            """, (product_id, supplier_id, cost, random.randint(1,4)))

            # inventory
            qty = get_initial_stock(subcat, unit, weight)

            cur.execute("""
                INSERT INTO kirana_oltp.inventory
                (store_id, product_id, quantity)
                VALUES (%s, %s, %s);
            """, (store_id, product_id, qty))

        promoted_products = random.sample(selected_products, random.randint(10, 18))
        for product_id, subcat, unit, weight in promoted_products:
            start_date = CURRENT_DATE - timedelta(days=random.randint(0, 20))
            end_date = start_date + timedelta(days=random.randint(7, 30))
            cur.execute("""
                INSERT INTO kirana_oltp.promotion
                (product_id, store_id, discount_percent, start_date, end_date)
                VALUES (%s, %s, %s, %s, %s);
            """, (
                product_id,
                store_id,
                round(random.uniform(3, 15), 2),
                start_date,
                end_date
            ))

    conn.commit()
    cur.close()
    conn.close()

    print("Realistic kirana dataset created successfully.")


if __name__ == "__main__":
    seed()
