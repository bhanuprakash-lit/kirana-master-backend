"""
Load and join all relevant tables from lit_db into feature-ready DataFrames.

Multi-store: automatically discovers all stores that have inventory_snapshots
data — no ACTIVE_STORE constant needed.  Pass store_ids=[...] to any function
to restrict to a subset of stores.

DATA SOURCE STRATEGY:
  - Backbone  : kirana_oltp.inventory_snapshots  (daily stock per product)
  - Sales     : kirana_oltp.order_item + orders  (actual transactions, aggregated to daily)
  - Attributes: kirana_oltp.product + category
  - Supply    : kirana_oltp.product_supplier     (lead_time_days, cost_price)
  - Promotions: kirana_oltp.promotion            (promo_flag, discount_percent)

The legacy kirana_olap.daily_store_sku_metrics table uses synthetic store IDs 1-4 /
product IDs 1-298 which have zero overlap with production stores. All functions
below source from the real OLTP tables instead.
"""
import warnings
import pandas as pd
import numpy as np
import psycopg2
from config import DB_CONFIG

_FALLBACK_STORE = 27   # used only when DB has no inventory_snapshots at all


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(**DB_CONFIG)


def _q(sql: str, conn=None) -> pd.DataFrame:
    close = conn is None
    if conn is None:
        conn = _conn()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_sql(sql, conn)
    if close:
        conn.close()
    return df


def _in(store_ids: list[int]) -> str:
    """Format store_ids list as SQL IN clause content: '27' or '27,28,29'.

    Every element is coerced to int before it reaches the query text (SAST
    Finding 03): the IN-lists are string-built into SQL, so a non-integer
    store id would otherwise be an injection surface if this helper is ever
    reused in a request-scoped path. int() raises on anything non-numeric.
    """
    return ",".join(str(int(s)) for s in store_ids)


# ─── Active store discovery ───────────────────────────────────────────────────

def get_active_stores(conn=None) -> list[int]:
    """
    Return all store IDs that have inventory_snapshots rows.
    Falls back to [_FALLBACK_STORE] if the table is empty.
    """
    df = _q(
        "SELECT DISTINCT store_id FROM kirana_oltp.inventory_snapshots ORDER BY store_id",
        conn,
    )
    stores = df["store_id"].tolist()
    return stores if stores else [_FALLBACK_STORE]


# ─── Core daily metrics (replaces kirana_olap.daily_store_sku_metrics) ────────

def load_daily_metrics(store_ids: list[int] | None = None) -> pd.DataFrame:
    """
    Build a daily (date × store × product) DataFrame from real OLTP tables.

    store_ids: list of store IDs to load. Defaults to all stores discovered
               in inventory_snapshots.

    Returns columns matching what feature_engineering.py expects:
        date, store_id, product_id, units_sold, revenue, profit, stock_on_hand,
        lost_sales, price, discount, promo_flag, avg_selling_price, margin,
        weather_temp, rain_flag, category_id, is_perishable, is_loose,
        lead_time_days, supplier_cost
    """
    conn = _conn()

    if store_ids is None:
        store_ids = get_active_stores(conn)

    sid_sql = _in(store_ids)

    # 1. ── Inventory snapshots — daily stock backbone ────────────────────────
    snaps = _q(f"""
        SELECT store_id, product_id,
               snapshot_date::date AS date,
               stock_on_hand
        FROM kirana_oltp.inventory_snapshots
        WHERE store_id IN ({sid_sql})
        ORDER BY product_id, snapshot_date
    """, conn)

    # 2. ── Daily sales from order_item + orders ───────────────────────────────
    sales = _q(f"""
        SELECT
            o.store_id,
            oi.product_id,
            o.order_date::date          AS date,
            SUM(oi.quantity)            AS units_sold,
            SUM(oi.quantity * oi.unit_price)                      AS revenue,
            SUM(oi.quantity * (oi.unit_price - oi.cost_price))    AS profit,
            AVG(oi.unit_price)                                    AS price,
            AVG(oi.cost_price)                                    AS avg_cost,
            AVG(
                CASE WHEN oi.unit_price > 0
                     THEN (oi.unit_price - oi.cost_price) / oi.unit_price * 100
                     ELSE 0 END
            )                                                      AS margin
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id IN ({sid_sql})
          AND o.order_status != 'cancelled'
        GROUP BY o.store_id, oi.product_id, o.order_date::date
    """, conn)

    # 3. ── Product attributes ────────────────────────────────────────────────
    products = _q(f"""
        SELECT p.product_id, p.category_id,
               p.is_perishable::int AS is_perishable,
               p.is_loose::int      AS is_loose
        FROM kirana_oltp.product p
        WHERE p.product_id IN (
            SELECT DISTINCT product_id
            FROM kirana_oltp.inventory_snapshots
            WHERE store_id IN ({sid_sql})
        )
    """, conn)

    # 4. ── Supplier info (min lead time per product) ─────────────────────────
    supplier = _q(f"""
        SELECT
            product_id,
            MIN(lead_time_days) AS lead_time_days,
            MIN(cost_price)     AS supplier_cost
        FROM kirana_oltp.product_supplier
        WHERE product_id IN (
            SELECT DISTINCT product_id
            FROM kirana_oltp.inventory_snapshots
            WHERE store_id IN ({sid_sql})
        )
        GROUP BY product_id
    """, conn)

    # 5. ── Promotions → daily promo_flag and discount ────────────────────────
    promos = _q(f"""
        SELECT product_id, store_id,
               discount_percent,
               start_date::date AS start_date,
               end_date::date   AS end_date
        FROM kirana_oltp.promotion
        WHERE store_id IN ({sid_sql})
    """, conn)

    conn.close()

    # ── Type fixes ────────────────────────────────────────────────────────────
    snaps["date"]  = pd.to_datetime(snaps["date"])
    sales["date"]  = pd.to_datetime(sales["date"])

    # ── Per-product historical averages (used to fill no-sale days) ───────────
    avg_price_hist  = sales.groupby("product_id")["price"].mean().rename("_avg_price")
    avg_margin_hist = sales.groupby("product_id")["margin"].mean().rename("_avg_margin")

    # ── Merge sales onto snapshot backbone ───────────────────────────────────
    df = snaps.merge(
        sales[["store_id", "product_id", "date",
               "units_sold", "revenue", "profit", "price", "margin"]],
        on=["store_id", "product_id", "date"],
        how="left",
    )

    # ── Fill no-sale rows ─────────────────────────────────────────────────────
    df["units_sold"] = df["units_sold"].fillna(0.0)
    df["revenue"]    = df["revenue"].fillna(0.0)
    df["profit"]     = df["profit"].fillna(0.0)
    df["lost_sales"] = 0.0
    df["discount"]   = 0.0

    # ── Price: use sale day price → historical avg → cost * 1.2 ─────────────
    df = df.merge(avg_price_hist,  on="product_id", how="left")
    df = df.merge(avg_margin_hist, on="product_id", how="left")
    df = df.merge(supplier,        on="product_id", how="left")

    df["price"] = df["price"].fillna(df["_avg_price"]).fillna(df["supplier_cost"] * 1.2)
    df["avg_selling_price"] = df["price"]
    df["margin"] = df["margin"].fillna(df["_avg_margin"]).fillna(0.0)

    # ── Promotions → vectorised expansion ─────────────────────────────────────
    if len(promos) > 0:
        rows = []
        for _, r in promos.iterrows():
            dates = pd.date_range(r["start_date"], r["end_date"], freq="D")
            rows.append(pd.DataFrame({
                "store_id":        int(r["store_id"]),
                "product_id":      int(r["product_id"]),
                "date":            dates,
                "promo_flag":      1.0,
                "promo_discount":  float(r["discount_percent"]),
            }))
        promo_daily = pd.concat(rows, ignore_index=True)
        df = df.merge(promo_daily, on=["store_id", "product_id", "date"], how="left")
        df["promo_flag"]    = df["promo_flag"].fillna(0.0)
        df["promo_discount"] = df["promo_discount"].fillna(0.0)
        df["discount"] = df["promo_discount"]
        df = df.drop(columns=["promo_discount"], errors="ignore")
    else:
        df["promo_flag"] = 0.0

    # ── Product attributes ────────────────────────────────────────────────────
    df = df.merge(products, on="product_id", how="left")
    df["is_perishable"] = df["is_perishable"].fillna(0).astype(int)
    df["is_loose"]      = df["is_loose"].fillna(0).astype(int)
    df["category_id"]   = df["category_id"].fillna(0).astype(int)

    # ── Supply chain defaults ─────────────────────────────────────────────────
    df["lead_time_days"] = df["lead_time_days"].fillna(3.0)
    df["supplier_cost"]  = df["supplier_cost"].fillna(df["price"] * 0.8)

    # ── Weather (no data available — filled with neutral values) ─────────────
    # Model will assign zero importance to these; kept for schema compatibility.
    df["weather_temp"] = 25.0
    df["rain_flag"]    = 0.0

    # ── Cleanup ───────────────────────────────────────────────────────────────
    df = df.drop(columns=["_avg_price", "_avg_margin"], errors="ignore")
    df = df.drop_duplicates(subset=["date", "store_id", "product_id"], keep="first")
    df["date"] = pd.to_datetime(df["date"])

    before = len(df)
    df = df.dropna(subset=["stock_on_hand"])
    if len(df) != before:
        warnings.warn(f"load_daily_metrics: dropped {before - len(df)} rows with null stock_on_hand")

    return df.sort_values(["product_id", "store_id", "date"]).reset_index(drop=True)


# ─── Inventory snapshots (for backward compat) ────────────────────────────────

def load_inventory_snapshots(store_ids: list[int] | None = None) -> pd.DataFrame:
    if store_ids is None:
        store_ids = get_active_stores()
    df = _q(f"""
        SELECT store_id, product_id,
               snapshot_date::date AS date,
               stock_on_hand
        FROM kirana_oltp.inventory_snapshots
        WHERE store_id IN ({_in(store_ids)})
        ORDER BY product_id, snapshot_date
    """)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ─── Products ─────────────────────────────────────────────────────────────────

def load_products(store_ids: list[int] | None = None) -> pd.DataFrame:
    if store_ids is None:
        store_ids = get_active_stores()
    return _q(f"""
        SELECT p.product_id, p.name, p.sku, p.brand, p.unit,
               p.category_id, c.name AS category_name,
               p.is_perishable, p.is_loose
        FROM kirana_oltp.product p
        JOIN kirana_oltp.category c ON p.category_id = c.category_id
        WHERE p.product_id IN (
            SELECT DISTINCT product_id
            FROM kirana_oltp.inventory_snapshots
            WHERE store_id IN ({_in(store_ids)})
        )
    """)


# ─── Product-supplier ─────────────────────────────────────────────────────────

def load_product_supplier(store_ids: list[int] | None = None) -> pd.DataFrame:
    if store_ids is None:
        store_ids = get_active_stores()
    return _q(f"""
        SELECT ps.product_id, ps.supplier_id, ps.cost_price, ps.lead_time_days
        FROM kirana_oltp.product_supplier ps
        WHERE ps.product_id IN (
            SELECT DISTINCT product_id
            FROM kirana_oltp.inventory_snapshots
            WHERE store_id IN ({_in(store_ids)})
        )
    """)


# ─── Current inventory ────────────────────────────────────────────────────────

def load_current_inventory(store_ids: list[int] | None = None) -> pd.DataFrame:
    if store_ids is None:
        store_ids = get_active_stores()
    return _q(f"""
        SELECT store_id, product_id, quantity AS current_stock
        FROM kirana_oltp.inventory
        WHERE store_id IN ({_in(store_ids)})
    """)


# ─── Order items (for margin features) ───────────────────────────────────────

def load_order_items(store_ids: list[int] | None = None) -> pd.DataFrame:
    if store_ids is None:
        store_ids = get_active_stores()
    df = _q(f"""
        SELECT oi.product_id,
               o.store_id,
               o.order_date::date AS date,
               oi.quantity,
               oi.unit_price,
               oi.cost_price,
               (oi.unit_price - oi.cost_price) AS gross_profit,
               CASE WHEN oi.unit_price > 0
                    THEN (oi.unit_price - oi.cost_price) / oi.unit_price * 100
                    ELSE 0 END AS margin_pct
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id IN ({_in(store_ids)})
          AND o.order_status != 'cancelled'
        ORDER BY o.order_date, oi.product_id
    """)
    df["date"] = pd.to_datetime(df["date"])
    return df
