import psycopg2
import random
from datetime import datetime, timedelta

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"
DB_HOST = "localhost"
DB_PORT = "5432"

random.seed(42)

DAYS = 60  # change 30–90

# demand weights
DEMAND_WEIGHTS = {
    "high": (5, 10),
    "medium": (2, 5),
    "low": (0, 2)
}

RESTOCK_MIN_TARGETS = {
    "high": (24, 38),
    "medium": (20, 32),
    "low": (18, 28),
}


def get_restock_policy(product_name, unit, weight):
    demand_class = get_demand_class(product_name)
    reorder_point = {
        "high": 18,
        "medium": 16,
        "low": 15,
    }[demand_class]
    low, high = RESTOCK_MIN_TARGETS[demand_class]

    if unit in ["pcs"] or (unit in ["g", "ml"] and weight <= 250):
        reorder_point += 4
        low = min(low + 6, 34)
        high = min(high + 10, 48)
    elif unit in ["kg", "L"] and weight >= 5:
        reorder_point -= 2
        low = max(15, low - 4)
        high = max(low + 4, high - 6)

    return reorder_point, random.randint(low, high)


def ensure_restock(cur, store_id, product, current_date):
    product_id = product["product_id"]
    product_name = product["name"]
    unit = product["unit"]
    weight = float(product["weight"] or 0)
    supplier_id = product["supplier_id"]
    cost_price = product["cost_price"]

    cur.execute("""
        SELECT quantity
        FROM kirana_oltp.inventory
        WHERE store_id = %s AND product_id = %s
        FOR UPDATE
    """, (store_id, product_id))
    current_stock = cur.fetchone()[0]

    reorder_point, target_stock = get_restock_policy(product_name, unit, weight)
    if current_stock > reorder_point:
        return

    purchase_qty = target_stock - current_stock
    if purchase_qty <= 0:
        return

    order_date = current_date.replace(hour=6, minute=random.randint(0, 30), second=0, microsecond=0)
    arrival_date = order_date + timedelta(days=random.randint(1, 3))

    cur.execute("""
        INSERT INTO kirana_oltp.purchases
        (supplier_id, store_id, order_date, arrival_date, status)
        VALUES (%s, %s, %s, %s, 'received')
        RETURNING purchase_id;
    """, (supplier_id, store_id, order_date, arrival_date))
    purchase_id = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO kirana_oltp.purchase_items
        (purchase_id, product_id, quantity, cost_price)
        VALUES (%s, %s, %s, %s);
    """, (purchase_id, product_id, purchase_qty, cost_price))

    cur.execute("""
        UPDATE kirana_oltp.inventory
        SET quantity = quantity + %s
        WHERE store_id = %s AND product_id = %s
    """, (purchase_qty, store_id, product_id))

    cur.execute("""
        INSERT INTO kirana_oltp.inventory_movements
        (store_id, product_id, change_quantity, reason, reference_id)
        VALUES (%s, %s, %s, 'purchase', %s);
    """, (store_id, product_id, purchase_qty, purchase_id))


def create_inventory_snapshot(cur, snapshot_date):
    cur.execute("""
        INSERT INTO kirana_oltp.inventory_snapshots
        (snapshot_date, store_id, product_id, stock_on_hand)
        SELECT %s, store_id, product_id, quantity
        FROM kirana_oltp.inventory
        ON CONFLICT (snapshot_date, store_id, product_id)
        DO UPDATE SET stock_on_hand = EXCLUDED.stock_on_hand;
    """, (snapshot_date.date(),))


def restock_store_end_of_day(cur, store_id, current_date):
    cur.execute("""
        SELECT
            p.product_id,
            p.name,
            p.unit,
            p.weight,
            ps.cost_price,
            s.supplier_id,
            i.quantity
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p
            ON p.product_id = i.product_id
        JOIN kirana_oltp.product_supplier ps
            ON ps.product_id = p.product_id
        JOIN kirana_oltp.supplier s
            ON s.supplier_id = ps.supplier_id
        WHERE i.store_id = %s
          AND s.store_id = %s
    """, (store_id, store_id))

    for row in cur.fetchall():
        product = {
            "product_id": row[0],
            "name": row[1],
            "unit": row[2],
            "weight": row[3],
            "cost_price": row[4],
            "supplier_id": row[5],
        }
        ensure_restock(cur, store_id, product, current_date.replace(hour=20, minute=0, second=0, microsecond=0))

def get_demand_class(product_name):
    name = product_name.lower()

    if any(x in name for x in ["rice", "atta", "milk"]):
        return "high"
    elif any(x in name for x in ["oil", "tea", "biscuits"]):
        return "medium"
    else:
        return "low"


def generate():
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    cur = conn.cursor()

    # -------------------------
    # fetch stores
    # -------------------------
    cur.execute("SELECT store_id FROM kirana_oltp.store;")
    stores = [x[0] for x in cur.fetchall()]

    cur.execute("SELECT store_id, user_id FROM kirana_oltp.users;")
    store_users = dict(cur.fetchall())

    cur.execute("SELECT customer_id FROM kirana_oltp.customer;")
    customers = [row[0] for row in cur.fetchall()]

    # -------------------------
    # fetch products per store
    # -------------------------
    store_products = {}

    for store_id in stores:
        cur.execute("""
            SELECT
                p.product_id,
                p.name,
                pr.price,
                COALESCE(pr.mrp, pr.price),
                p.unit,
                p.weight,
                ps.cost_price,
                s.supplier_id
            FROM kirana_oltp.product p
            JOIN kirana_oltp.pricing pr
                ON pr.product_id = p.product_id
            JOIN kirana_oltp.product_supplier ps
                ON ps.product_id = p.product_id
            JOIN kirana_oltp.supplier s
                ON s.supplier_id = ps.supplier_id
            WHERE pr.store_id = %s
              AND s.store_id = %s
        """, (store_id, store_id))
        rows = cur.fetchall()
        store_products[store_id] = [
            {
                "product_id": row[0],
                "name": row[1],
                "price": row[2],
                "mrp": row[3],
                "unit": row[4],
                "weight": row[5],
                "cost_price": row[6],
                "supplier_id": row[7],
            }
            for row in rows
        ]

    # -------------------------
    # simulation loop
    # -------------------------
    today = datetime.today()

    for day in range(DAYS):
        current_date = today - timedelta(days=day)

        for store_id in stores:

            # weekend boost
            is_weekend = current_date.weekday() >= 5
            orders_count = random.randint(40, 80) if is_weekend else random.randint(25, 50)

            products = store_products[store_id]
            store_user_id = store_users[store_id]

            for _ in range(orders_count):
                basket_size = min(random.randint(1, 5), len(products))
                total = 0
                order_items = []
                reserved_qty = {}

                order_time = current_date.replace(
                    hour=random.randint(8, 22),
                    minute=random.randint(0, 59),
                    second=random.randint(0, 59),
                    microsecond=0,
                )

                for product in random.sample(products, basket_size):
                    product_id = product["product_id"]
                    name = product["name"]
                    price = float(product["price"])
                    mrp = float(product["mrp"])

                    ensure_restock(cur, store_id, product, current_date)

                    # demand logic
                    demand_class = get_demand_class(name)
                    qty = random.randint(*DEMAND_WEIGHTS[demand_class])

                    if qty == 0:
                        continue

                    # check inventory
                    cur.execute("""
                        SELECT quantity FROM kirana_oltp.inventory
                        WHERE store_id=%s AND product_id=%s
                    """, (store_id, product_id))

                    res = cur.fetchone()
                    if not res:
                        continue

                    stock = res[0]

                    if stock <= 0:
                        continue

                    available_stock = max(stock - reserved_qty.get(product_id, 0), 0)
                    if available_stock <= 0:
                        continue

                    sell_qty = min(qty, available_stock)

                    discount = 0
                    cur.execute("""
                        SELECT discount_percent
                        FROM kirana_oltp.promotion
                        WHERE store_id = %s
                          AND product_id = %s
                          AND %s BETWEEN start_date AND end_date
                        ORDER BY discount_percent DESC
                        LIMIT 1
                    """, (store_id, product_id, order_time))
                    promo_row = cur.fetchone()
                    if promo_row:
                        discount = float(promo_row[0])

                    effective_price = round(min(
                        price,
                        mrp,
                        price * (1 - (discount / 100))
                    ), 2)
                    cost = float(product["cost_price"])

                    order_items.append((product_id, sell_qty, effective_price, cost))
                    reserved_qty[product_id] = reserved_qty.get(product_id, 0) + sell_qty

                    total += sell_qty * effective_price

                if not order_items:
                    continue

                # create order only when at least one valid line item exists
                cur.execute("""
                    INSERT INTO kirana_oltp.orders
                    (store_id, user_id, customer_id, order_status, order_date, total_amount)
                    VALUES (%s, %s, %s, 'completed', %s, %s)
                    RETURNING order_id;
                """, (
                    store_id,
                    store_user_id,
                    random.choice(customers),
                    order_time,
                    total
                ))

                order_id = cur.fetchone()[0]

                for product_id, sell_qty, price, cost in order_items:
                    cur.execute("""
                        INSERT INTO kirana_oltp.order_item
                        (order_id, product_id, quantity, unit_price, cost_price)
                        VALUES (%s, %s, %s, %s, %s);
                    """, (order_id, product_id, sell_qty, price, cost))

                payment_method = random.choices(
                    ["upi", "cash", "card"],
                    weights=[60, 30, 10],
                    k=1
                )[0]
                cur.execute("""
                    INSERT INTO kirana_oltp.payments
                    (order_id, amount, payment_method, status, created_at)
                    VALUES (%s, %s, %s, 'paid', %s);
                """, (order_id, total, payment_method, order_time))

            restock_store_end_of_day(cur, store_id, current_date)
            create_inventory_snapshot(cur, current_date)

    create_inventory_snapshot(cur, today)

    conn.commit()
    cur.close()
    conn.close()

    print(f"Generated {DAYS} days of realistic transactions.")


if __name__ == "__main__":
    generate()
