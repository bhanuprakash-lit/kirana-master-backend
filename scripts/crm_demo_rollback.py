#!/usr/bin/env python3
"""
Undo exactly what crm_demo_seed.py created, using the manifest it wrote.
Deletes inserted rows; for pricing rows that already existed on a store
(stores 22/23/24 had legacy pricing for nearly the whole catalogue), restores
the original price/mrp instead of deleting the row.

Usage:
    python scripts/crm_demo_rollback.py
    AZURE_DB_URL="postgresql://..." python scripts/crm_demo_rollback.py
"""

import json
import os

import psycopg2

DB_URL = os.environ.get(
    "AZURE_DB_URL",
    "host=psql-lohiya-kirana.postgres.database.azure.com port=5432 "
    "dbname=db-kirana-dev user=psqladmin password=Lohiya@2026 sslmode=require",
)

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "crm_demo_manifest.json")


def main():
    if not os.path.exists(MANIFEST_PATH):
        raise SystemExit(f"No manifest found at {MANIFEST_PATH} -- nothing to roll back.")

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    try:
        movement_ids = manifest.get("inventory_movement_ids") or []
        if movement_ids:
            cur.execute(
                "DELETE FROM kirana_oltp.inventory_movements WHERE movement_id = ANY(%s)",
                (movement_ids,),
            )
            print(f"Removed {cur.rowcount} inventory_movements rows")

        for store_id_str, sm in manifest["target_stores"].items():
            store_id = int(store_id_str)

            deal_ids = sm.get("crm_deal_ids") or []
            if deal_ids:
                cur.execute(
                    "DELETE FROM kirana_oltp.crm_deals WHERE deal_id = ANY(%s)", (deal_ids,)
                )

            footfall_ids = sm.get("footfall_ids") or []
            if footfall_ids:
                cur.execute(
                    "DELETE FROM kirana_oltp.footfall WHERE footfall_id = ANY(%s)",
                    (footfall_ids,),
                )

            # orders cascade-delete their order_item rows
            order_ids = sm.get("order_ids") or []
            if order_ids:
                cur.execute(
                    "DELETE FROM kirana_oltp.orders WHERE order_id = ANY(%s)", (order_ids,)
                )

            customer_ids = sm.get("customer_ids") or []
            if customer_ids:
                cur.execute(
                    "DELETE FROM kirana_oltp.customer WHERE customer_id = ANY(%s)",
                    (customer_ids,),
                )

            inventory_ids = sm.get("inventory_ids") or []
            if inventory_ids:
                cur.execute(
                    "DELETE FROM kirana_oltp.inventory WHERE inventory_id = ANY(%s)",
                    (inventory_ids,),
                )

            pricing_inserts = sm.get("pricing_inserts") or []
            if pricing_inserts:
                cur.execute(
                    "DELETE FROM kirana_oltp.pricing WHERE pricing_id = ANY(%s)",
                    (pricing_inserts,),
                )

            for upd in sm.get("pricing_updates") or []:
                cur.execute(
                    "UPDATE kirana_oltp.pricing SET price = %s, mrp = %s WHERE pricing_id = %s",
                    (upd["old_price"], upd["old_mrp"], upd["pricing_id"]),
                )

            print(
                f"store {store_id}: removed {len(order_ids)} orders, "
                f"{len(customer_ids)} customers, {len(inventory_ids)} inventory rows, "
                f"{len(footfall_ids)} footfall rows, {len(deal_ids)} CRM deals; "
                f"deleted {len(pricing_inserts)} pricing rows, "
                f"restored {len(sm.get('pricing_updates') or [])} original prices"
            )

        conn.commit()
        print("\nRollback complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    backup_path = MANIFEST_PATH + ".applied"
    os.replace(MANIFEST_PATH, backup_path)
    print(f"Manifest moved to {backup_path} (rollback already applied).")


if __name__ == "__main__":
    main()
