"""
db_cleanup_and_upgrade.py
--------------------------
Runs four operations atomically:

  1. Add image_url column to kirana_oltp.product (idempotent)
  2. Rename catalog SKUs: BL-{id}  -> KAI-{id}
  3. Backfill image_url for KAI- products from scraper JSON files
  4. Delete old uppercase-SKU dummy products (SKU0…SKU299)
  5. Delete dummy users (1-7, 39-51) + their stores + ALL dependent data
     in correct FK order — zero risk to unrelated stores/data.
"""

import json
import os
import psycopg2

# ── config ────────────────────────────────────────────────────────────────────
DB = dict(dbname="lit_db", user="postgres", password="123456",
          host="localhost", port="5432")

BLINKIT_DATA_DIR = os.environ.get(
    "BLINKIT_DATA_DIR",
    r"C:\Users\Bhanuprakash\Documents\misc\blinkit-scrapper\kirana_data\products",
)

TARGET_USER_IDS  = list(range(1, 8)) + list(range(39, 52))   # 1-7 + 39-51
TARGET_STORE_IDS = [1, 2, 3, 4, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21]


# ── helpers ───────────────────────────────────────────────────────────────────

def _count(cur, table: str, col: str, ids: list) -> int:
    cur.execute(
        f"SELECT COUNT(*) FROM kirana_oltp.{table} WHERE {col} = ANY(%s)", (ids,)
    )
    return cur.fetchone()[0]


def _delete(cur, table: str, col: str, ids: list) -> int:
    cur.execute(
        f"DELETE FROM kirana_oltp.{table} WHERE {col} = ANY(%s)", (ids,)
    )
    return cur.rowcount


def _col_exists(cur, table: str, col: str) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_schema='kirana_oltp' AND table_name=%s AND column_name=%s""",
        (table, col),
    )
    return bool(cur.fetchone())


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    conn = psycopg2.connect(**DB)
    conn.autocommit = False
    cur = conn.cursor()

    # ── 1. Add image_url column ───────────────────────────────────────────────
    print("1. Adding image_url column...")
    if not _col_exists(cur, "product", "image_url"):
        cur.execute(
            "ALTER TABLE kirana_oltp.product ADD COLUMN image_url VARCHAR(500)"
        )
        print("   [OK] image_url added")
    else:
        print("   [OK] already exists, skipping")

    # ── 2. Rename BL- -> KAI- ────────────────────────────────────────────────
    print("2. Renaming BL- SKUs to KAI-...")
    cur.execute("""
        UPDATE kirana_oltp.product
        SET sku = 'KAI-' || substring(sku FROM 4)
        WHERE sku LIKE 'BL-%'
    """)
    print(f"   [OK] {cur.rowcount} SKUs renamed")

    # ── 3. Backfill image_url from JSON files ─────────────────────────────────
    print("3. Backfilling image_url for KAI- products...")
    updates: list[tuple[str, str]] = []   # (image_url, sku)

    for filename in os.listdir(BLINKIT_DATA_DIR):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(BLINKIT_DATA_DIR, filename), encoding="utf-8") as f:
            products = json.load(f)
        for p in products:
            img = (p.get("image") or "").strip()
            pid = str(p.get("product_id", "")).strip()
            if img and pid:
                updates.append((img[:500], f"KAI-{pid}"))

    cur.executemany(
        "UPDATE kirana_oltp.product SET image_url = %s WHERE sku = %s",
        updates,
    )
    print(f"   [OK] {len(updates)} image URLs written")

    # Collect SKU product IDs early (needed for FK ordering below)
    cur.execute(
        "SELECT product_id FROM kirana_oltp.product WHERE sku ~ '^SKU[0-9]+$'"
    )
    sku_product_ids = [r[0] for r in cur.fetchall()]
    print(f"4. Found {len(sku_product_ids)} dummy SKU products (will delete after store cleanup)")

    # ── 4/5. Delete dummy stores + users (order_item cleared here frees SKU FKs) ─
    print("5. Deleting dummy stores and users...")

    # Gather order_ids for these stores
    cur.execute(
        "SELECT array_agg(order_id) FROM kirana_oltp.orders WHERE store_id = ANY(%s)",
        (TARGET_STORE_IDS,)
    )
    order_ids: list = cur.fetchone()[0] or []

    # Gather purchase_ids for these stores
    cur.execute(
        "SELECT array_agg(purchase_id) FROM kirana_oltp.purchases WHERE store_id = ANY(%s)",
        (TARGET_STORE_IDS,)
    )
    purchase_ids: list = cur.fetchone()[0] or []

    # Gather supplier_ids for these stores
    cur.execute(
        "SELECT array_agg(supplier_id) FROM kirana_oltp.supplier WHERE store_id = ANY(%s)",
        (TARGET_STORE_IDS,)
    )
    supplier_ids: list = cur.fetchone()[0] or []

    steps: list[tuple[str, str, list]] = [
        # ── orders and all its children (FK order matters) ──
        ("order_item",          "order_id",    order_ids),       # FK -> orders
        ("payments",            "order_id",    order_ids),       # FK -> orders
        ("khata",               "order_id",    order_ids),       # FK -> orders (order_id col)
        ("referrals",           "order_id",    order_ids),       # FK -> orders
        ("referral_vouchers",   "store_id",    TARGET_STORE_IDS),# FK -> orders via used_on_order_id; delete by store first
        ("orders",              "store_id",    TARGET_STORE_IDS),
        # ── inventory tree ──
        ("inventory_movements", "store_id",    TARGET_STORE_IDS),
        ("inventory_snapshots", "store_id",    TARGET_STORE_IDS),
        ("inventory_batch",     "store_id",    TARGET_STORE_IDS),
        ("inventory",           "store_id",    TARGET_STORE_IDS),
        # ── purchases ──
        ("purchase_items",      "purchase_id", purchase_ids),
        ("purchases",           "store_id",    TARGET_STORE_IDS),
        # ── pricing / promotions ──
        ("pricing",             "store_id",    TARGET_STORE_IDS),
        ("promotion",           "store_id",    TARGET_STORE_IDS),
        ("scheme_claim",        "store_id",    TARGET_STORE_IDS),
        ("shelf_planogram",     "store_id",    TARGET_STORE_IDS),
        ("return_to_vendor",    "store_id",    TARGET_STORE_IDS),
        # ── finance / ops ──
        ("ap_ar_aging",         "store_id",    TARGET_STORE_IDS),
        ("cart_session",        "store_id",    TARGET_STORE_IDS),
        ("cashflow_requests",   "store_id",    TARGET_STORE_IDS),
        ("crm_deals",           "store_id",    TARGET_STORE_IDS),
        ("footfall",            "store_id",    TARGET_STORE_IDS),
        ("intelligence_log",    "store_id",    TARGET_STORE_IDS),
        ("issue_report",        "store_id",    TARGET_STORE_IDS),
        ("marketing_spend",     "store_id",    TARGET_STORE_IDS),
        ("opex",                "store_id",    TARGET_STORE_IDS),
        ("process_events",      "store_id",    TARGET_STORE_IDS),
        # ── referral system (tokens FK campaigns, so tokens first) ──
        ("referral_tokens",     "store_id",    TARGET_STORE_IDS),
        ("referral_campaigns",  "store_id",    TARGET_STORE_IDS),
        # ── supplier / product links (scheme FKs supplier, so scheme first) ──
        ("scheme",              "supplier_id", supplier_ids),
        ("product_supplier",    "supplier_id", supplier_ids),
        ("supplier",            "store_id",    TARGET_STORE_IDS),
        # ── customer / associations ──
        ("khata",               "store_id",    TARGET_STORE_IDS),  # khata also FKs customer; delete remainder by store_id
        ("store_association",   "store_id",    TARGET_STORE_IDS),  # customer.association_id -> SET NULL
        ("customer",            "store_id",    TARGET_STORE_IDS),
        # ── subscription ──
        ("subscription",        "store_id",    TARGET_STORE_IDS),
        # ── per-user tables ──
        ("user_sessions",       "user_id",     TARGET_USER_IDS),
        ("user_prefs",          "user_id",     TARGET_USER_IDS),
        ("cashflow_requests",   "user_id",     TARGET_USER_IDS),
        ("issue_report",        "user_id",     TARGET_USER_IDS),
        # ── finally: users and stores ──
        ("users",               "user_id",     TARGET_USER_IDS),
        ("store",               "store_id",    TARGET_STORE_IDS),
    ]

    for table, col, ids in steps:
        if not ids:
            continue
        try:
            n = _delete(cur, table, col, ids)
            if n:
                print(f"   {table:<26} {col:<12} -> {n:>5} rows deleted")
        except Exception as e:
            print(f"   [WARN] {table}.{col}: {e}")
            conn.rollback()
            raise

    # ── 5. Delete old dummy SKU products (now safe — order_items gone) ──────────
    print("5. Deleting dummy SKU products...")
    if sku_product_ids:
        cur.execute(
            "DELETE FROM kirana_oltp.product_supplier WHERE product_id = ANY(%s)",
            (sku_product_ids,)
        )
        cur.execute(
            "DELETE FROM kirana_oltp.product WHERE product_id = ANY(%s)",
            (sku_product_ids,)
        )
        print(f"   [OK] {cur.rowcount} dummy products deleted")

    conn.commit()
    cur.close()
    conn.close()
    print("\n[OK] All done. DB is clean.")


if __name__ == "__main__":
    main()
