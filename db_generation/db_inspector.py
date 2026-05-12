from math import ceil

import psycopg2
from flask import Flask, abort, render_template, request, url_for
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"
DB_HOST = "localhost"
DB_PORT = "5432"

APP_TITLE = "Kirana DB Inspector"
PAGE_SIZE = 50
SCHEMAS = ("kirana_oltp", "kirana_olap")
STORE_FILTERS = {
    ("kirana_oltp", "category"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.product p "
        "JOIN kirana_oltp.inventory i ON i.product_id = p.product_id "
        "WHERE p.category_id = t.category_id AND i.store_id = %s)"
    ),
    ("kirana_oltp", "product"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.inventory i "
        "WHERE i.product_id = t.product_id AND i.store_id = %s)"
    ),
    ("kirana_oltp", "order_item"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.orders o "
        "WHERE o.order_id = t.order_id AND o.store_id = %s)"
    ),
    ("kirana_oltp", "payments"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.orders o "
        "WHERE o.order_id = t.order_id AND o.store_id = %s)"
    ),
    ("kirana_oltp", "purchase_items"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.purchases p "
        "WHERE p.purchase_id = t.purchase_id AND p.store_id = %s)"
    ),
    ("kirana_oltp", "product_supplier"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.supplier s "
        "WHERE s.supplier_id = t.supplier_id AND s.store_id = %s)"
    ),
    ("kirana_oltp", "customer"): (
        "EXISTS (SELECT 1 FROM kirana_oltp.orders o "
        "WHERE o.customer_id = t.customer_id AND o.store_id = %s)"
    ),
}

app = Flask(__name__)


def get_conn():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )


def fetch_value(query, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def fetch_rows(query, params=None):
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params or ())
        return list(cur.fetchall())


def get_tables():
    query = """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema = ANY(%s)
          AND table_type IN ('BASE TABLE', 'VIEW')
        ORDER BY table_schema, table_name;
    """
    return fetch_rows(query, (list(SCHEMAS),))


def get_table_columns(schema, table):
    query = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position;
    """
    return fetch_rows(query, (schema, table))


def table_has_column(schema, table, column):
    query = """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        );
    """
    return bool(fetch_value(query, (schema, table, column)))


def is_valid_table(schema, table):
    if schema not in SCHEMAS:
        return False
    query = """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
        );
    """
    return bool(fetch_value(query, (schema, table)))


def get_stores():
    query = """
        SELECT store_id, name, location, region
        FROM kirana_oltp.store
        WHERE COALESCE(is_deleted, FALSE) = FALSE
        ORDER BY store_id;
    """
    return fetch_rows(query)


def parse_store_id():
    raw_store_id = request.args.get("store_id")
    if not raw_store_id:
        return None
    try:
        store_id = int(raw_store_id)
    except ValueError:
        abort(400)
    if not fetch_value("SELECT EXISTS (SELECT 1 FROM kirana_oltp.store WHERE store_id = %s)", (store_id,)):
        abort(404)
    return store_id


def get_selected_store(stores, store_id):
    if store_id is None:
        return None
    return next((store for store in stores if store["store_id"] == store_id), None)


def store_url(endpoint, **values):
    store_id = request.args.get("store_id")
    if store_id:
        values["store_id"] = store_id
    return url_for(endpoint, **values)


app.jinja_env.globals["store_url"] = store_url


def get_store_filter(schema, table, store_id):
    if store_id is None:
        return sql.SQL(""), ()

    if table_has_column(schema, table, "store_id"):
        return sql.SQL(" WHERE {} = %s").format(sql.Identifier("t", "store_id")), (store_id,)

    filter_sql = STORE_FILTERS.get((schema, table))
    if filter_sql:
        return sql.SQL(" WHERE " + filter_sql), (store_id,)

    return sql.SQL(""), ()


def get_table_count(schema, table, store_id=None):
    where_clause, params = get_store_filter(schema, table, store_id)
    query = sql.SQL("SELECT COUNT(*) FROM {}.{} AS t{}").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        where_clause,
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()[0]


def get_table_rows(schema, table, page, page_size, store_id=None):
    columns = get_table_columns(schema, table)
    if not columns:
        return [], []

    order_column = columns[0]["column_name"]
    offset = (page - 1) * page_size
    where_clause, params = get_store_filter(schema, table, store_id)
    query = sql.SQL("SELECT * FROM {}.{} AS t{} ORDER BY {} LIMIT %s OFFSET %s").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        where_clause,
        sql.Identifier("t", order_column),
    )

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params + (page_size, offset))
        return columns, list(cur.fetchall())


def get_summary_cards(store_id=None):
    if store_id is None:
        return [
            ("Stores", fetch_value("SELECT COUNT(*) FROM kirana_oltp.store")),
            ("Users", fetch_value("SELECT COUNT(*) FROM kirana_oltp.users")),
            ("Customers", fetch_value("SELECT COUNT(*) FROM kirana_oltp.customer")),
            ("Products", fetch_value("SELECT COUNT(*) FROM kirana_oltp.product")),
            ("Orders", fetch_value("SELECT COUNT(*) FROM kirana_oltp.orders")),
            ("Payments", fetch_value("SELECT COUNT(*) FROM kirana_oltp.payments")),
            ("Purchases", fetch_value("SELECT COUNT(*) FROM kirana_oltp.purchases")),
            ("Inventory Snapshots", fetch_value("SELECT COUNT(*) FROM kirana_oltp.inventory_snapshots")),
        ]

    return [
        ("Stores", 1),
        ("Users", fetch_value("SELECT COUNT(*) FROM kirana_oltp.users WHERE store_id = %s", (store_id,))),
        (
            "Customers",
            fetch_value(
                "SELECT COUNT(DISTINCT customer_id) FROM kirana_oltp.orders WHERE store_id = %s",
                (store_id,),
            ),
        ),
        (
            "Products",
            fetch_value(
                "SELECT COUNT(DISTINCT product_id) FROM kirana_oltp.inventory WHERE store_id = %s",
                (store_id,),
            ),
        ),
        ("Orders", fetch_value("SELECT COUNT(*) FROM kirana_oltp.orders WHERE store_id = %s", (store_id,))),
        (
            "Payments",
            fetch_value(
                """
                SELECT COUNT(*)
                FROM kirana_oltp.payments p
                JOIN kirana_oltp.orders o ON o.order_id = p.order_id
                WHERE o.store_id = %s
                """,
                (store_id,),
            ),
        ),
        ("Purchases", fetch_value("SELECT COUNT(*) FROM kirana_oltp.purchases WHERE store_id = %s", (store_id,))),
        (
            "Inventory Snapshots",
            fetch_value(
                "SELECT COUNT(*) FROM kirana_oltp.inventory_snapshots WHERE store_id = %s",
                (store_id,),
            ),
        ),
    ]


def add_store_clause(query, store_id, alias=""):
    if store_id is None:
        return query, ()

    prefix = f"{alias}." if alias else ""
    keyword = " AND " if " WHERE " in query.upper() else " WHERE "
    return f"{query}{keyword}{prefix}store_id = %s", (store_id,)


def get_data_quality_checks(store_id=None):
    checks = [
        ("Orders missing user", "SELECT COUNT(*) FROM kirana_oltp.orders WHERE user_id IS NULL", ""),
        ("Orders missing customer", "SELECT COUNT(*) FROM kirana_oltp.orders WHERE customer_id IS NULL", ""),
        ("Users missing email", "SELECT COUNT(*) FROM kirana_oltp.users WHERE email IS NULL", ""),
        ("Customers loaded", "SELECT COUNT(DISTINCT customer_id) FROM kirana_oltp.orders", ""),
        ("Inventory at zero", "SELECT COUNT(*) FROM kirana_oltp.inventory WHERE quantity = 0", ""),
        ("Inventory below 15", "SELECT COUNT(*) FROM kirana_oltp.inventory WHERE quantity < 15", ""),
        ("Pricing missing valid_to", "SELECT COUNT(*) FROM kirana_oltp.pricing WHERE valid_to IS NULL", ""),
        (
            "Payments loaded",
            """
            SELECT COUNT(*)
            FROM kirana_oltp.payments p
            JOIN kirana_oltp.orders o ON o.order_id = p.order_id
            """,
            "o",
        ),
        ("Promotions loaded", "SELECT COUNT(*) FROM kirana_oltp.promotion", ""),
        (
            "Purchase items loaded",
            """
            SELECT COUNT(*)
            FROM kirana_oltp.purchase_items pi
            JOIN kirana_oltp.purchases p ON p.purchase_id = pi.purchase_id
            """,
            "p",
        ),
    ]

    results = []
    for label, query, alias in checks:
        scoped_query, params = add_store_clause(query, store_id, alias)
        results.append({"label": label, "value": fetch_value(scoped_query, params)})
    return results


def get_inventory_distribution(store_id=None):
    query = """
        SELECT
            store_id,
            MIN(quantity) AS min_qty,
            MAX(quantity) AS max_qty,
            ROUND(AVG(quantity)::numeric, 2) AS avg_qty
        FROM kirana_oltp.inventory
        WHERE (%s IS NULL OR store_id = %s)
        GROUP BY store_id
        ORDER BY store_id;
    """
    return fetch_rows(query, (store_id, store_id))


def get_recent_orders(store_id=None):
    query = """
        SELECT
            o.order_id,
            o.order_date,
            s.name AS store_name,
            u.username,
            c.name AS customer_name,
            o.total_amount
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.store s ON s.store_id = o.store_id
        LEFT JOIN kirana_oltp.users u ON u.user_id = o.user_id
        LEFT JOIN kirana_oltp.customer c ON c.customer_id = o.customer_id
        WHERE (%s IS NULL OR o.store_id = %s)
        ORDER BY o.order_date DESC
        LIMIT 15;
    """
    return fetch_rows(query, (store_id, store_id))


@app.route("/")
def index():
    store_id = parse_store_id()
    stores = get_stores()
    return render_template(
        "index.html",
        app_title=APP_TITLE,
        summary_cards=get_summary_cards(store_id),
        quality_checks=get_data_quality_checks(store_id),
        inventory_distribution=get_inventory_distribution(store_id),
        recent_orders=get_recent_orders(store_id),
        tables=get_tables(),
        stores=stores,
        selected_store=get_selected_store(stores, store_id),
        selected_store_id=store_id,
    )


@app.route("/table/<schema>/<table>")
def table_view(schema, table):
    if not is_valid_table(schema, table):
        abort(404)

    store_id = parse_store_id()
    stores = get_stores()
    page = max(int(request.args.get("page", 1)), 1)
    count = get_table_count(schema, table, store_id)
    total_pages = max(ceil(count / PAGE_SIZE), 1)
    page = min(page, total_pages)

    columns, rows = get_table_rows(schema, table, page, PAGE_SIZE, store_id)

    return render_template(
        "table.html",
        app_title=APP_TITLE,
        schema=schema,
        table=table,
        count=count,
        columns=columns,
        rows=rows,
        page=page,
        total_pages=total_pages,
        page_size=PAGE_SIZE,
        tables=get_tables(),
        stores=stores,
        selected_store=get_selected_store(stores, store_id),
        selected_store_id=store_id,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)