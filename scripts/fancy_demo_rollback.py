#!/usr/bin/env python3
"""
Undo exactly what fancy_demo_seed.py created for Vani Fancy Stores (store_id=53).
Reads fancy_demo_manifest.json and deletes every row that was inserted.

Usage:
    python scripts/fancy_demo_rollback.py
    AZURE_DB_URL="postgresql://..." python scripts/fancy_demo_rollback.py
"""

import json
import os

import psycopg2

# Connection string comes from the environment — never a hardcoded DB
# password in source (a committed credential is a real leak; the Azure
# password that used to live here has been removed and must be rotated).
DB_URL = os.environ.get("AZURE_DB_URL") or os.environ.get("DATABASE_URL")
if not DB_URL:
    raise SystemExit("Set AZURE_DB_URL (or DATABASE_URL) to run this rollback.")

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "fancy_demo_manifest.json")

# Only these (table, id_col) pairs may be targeted by delete_ids — the table
# and id column become raw SQL identifiers (not bindable), so they must come
# from this closed, code-defined set, never from arguments (SAST Finding 06:
# a destructive DELETE with a dynamic target table).
_DELETABLE = {
    ("basket", "basket_id"),
    ("coupon", "coupon_id"),
    ("crm_deals", "deal_id"),
    ("customer", "customer_id"),
    ("footfall", "footfall_id"),
    ("inventory", "inventory_id"),
    ("inventory_movements", "movement_id"),
    ("job_card", "job_id"),
    ("khata", "khata_id"),
    ("loyalty_transaction", "txn_id"),
    ("orders", "order_id"),
    ("pricing", "pricing_id"),
    ("product", "product_id"),
    ("purchases", "purchase_id"),
    ("staff", "staff_id"),
    ("staff_attendance", "id"),
    ("supplier", "supplier_id"),
}


def delete_ids(cur, table: str, id_col: str, ids: list, label: str = ""):
    if not ids:
        return 0
    if (table, id_col) not in _DELETABLE:
        raise ValueError(f"Refusing DELETE on non-allowlisted target: {table}.{id_col}")
    cur.execute(f"DELETE FROM kirana_oltp.{table} WHERE {id_col} = ANY(%s)", (ids,))
    n = cur.rowcount
    print(f"  {label or table}: removed {n} rows")
    return n


def main():
    if not os.path.exists(MANIFEST_PATH):
        raise SystemExit(f"No manifest at {MANIFEST_PATH} — nothing to roll back.")

    with open(MANIFEST_PATH) as f:
        m = json.load(f)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    try:
        # Delete in FK-safe order (children before parents)

        # inventory movements (trigger-created, no FK to orders)
        delete_ids(cur, "inventory_movements", "movement_id",
                   m.get("inventory_movement_ids", []), "inventory_movements")

        # loyalty transactions
        delete_ids(cur, "loyalty_transaction", "txn_id",
                   m.get("loyalty_txn_ids", []), "loyalty_transactions")

        # loyalty config
        if m.get("loyalty_config_inserted"):
            cur.execute("DELETE FROM kirana_oltp.loyalty_config WHERE store_id = %s",
                        (m["store_id"],))
            print(f"  loyalty_config: removed {cur.rowcount} rows")

        # coupons
        delete_ids(cur, "coupon", "coupon_id", m.get("coupon_ids", []), "coupons")

        # baskets (cascade deletes basket_items)
        delete_ids(cur, "basket", "basket_id", m.get("basket_ids", []),
                   "baskets (+ basket_items via cascade)")

        # job cards
        delete_ids(cur, "job_card", "job_id", m.get("job_card_ids", []), "job_cards")

        # footfall
        delete_ids(cur, "footfall", "footfall_id", m.get("footfall_ids", []), "footfall")

        # CRM deals
        delete_ids(cur, "crm_deals", "deal_id", m.get("crm_deal_ids", []), "crm_deals")

        # staff attendance (cascade from staff, but manifest tracks them explicitly)
        delete_ids(cur, "staff_attendance", "id",
                   m.get("staff_attendance_ids", []), "staff_attendance")

        # staff
        delete_ids(cur, "staff", "staff_id", m.get("staff_ids", []), "staff")

        # orders → cascades order_items; but khata has FK to orders so delete khata first
        # khata_payments cascade from khata
        delete_ids(cur, "khata", "khata_id", m.get("khata_ids", []),
                   "khata (+ khata_payments via cascade)")

        # orders (cascades order_items)
        delete_ids(cur, "orders", "order_id", m.get("order_ids", []),
                   "orders (+ order_items via cascade)")

        # customers
        delete_ids(cur, "customer", "customer_id", m.get("customer_ids", []), "customers")

        # purchases (cascades purchase_items)
        delete_ids(cur, "purchases", "purchase_id", m.get("purchase_ids", []),
                   "purchases (+ purchase_items via cascade)")

        # suppliers
        delete_ids(cur, "supplier", "supplier_id", m.get("supplier_ids", []), "suppliers")

        # pricing
        delete_ids(cur, "pricing", "pricing_id", m.get("pricing_ids", []), "pricing")

        # inventory
        delete_ids(cur, "inventory", "inventory_id", m.get("inventory_ids", []), "inventory")

        # new global products (added to catalog)
        delete_ids(cur, "product", "product_id", m.get("new_product_ids", []),
                   "global products (new)")

        conn.commit()
        print("\nRollback complete — Vani Fancy Stores is back to zero.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    backup = MANIFEST_PATH + ".applied"
    os.replace(MANIFEST_PATH, backup)
    print(f"Manifest moved to {backup}.")


if __name__ == "__main__":
    main()
