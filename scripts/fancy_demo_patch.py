#!/usr/bin/env python3
"""
Patch script for Vani Fancy Stores demo data.
Adds everything slipped out of the main seed:

  - GST rates on all 20 products (so GST report works)
  - inventory_snapshots for 6 days (powers fast-moving / ML predictions)
  - Set 2 items to low/zero stock (shows stockout alerts on home screen)
  - 3 area associations + customers linked (powers customer heatmap)
  - 6 staff tasks (mix of done / pending for both staff members)
  - 2 estimates with line items (pending quotes)
  - 1 sales return (populates return-rate KPI)

Appends new IDs to fancy_demo_manifest.json so rollback still works.

Usage:
    python scripts/fancy_demo_patch.py
    AZURE_DB_URL="postgresql://..." python scripts/fancy_demo_patch.py
"""

import json
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

STORE_ID   = 53
REG_DATE   = date(2026, 6, 25)
TODAY      = date(2026, 6, 30)

STAFF_IDS      = [3, 4]        # Ramu Naik=3, Preethi Rao=4
CUSTOMER_IDS   = list(range(1635, 1647))   # 1635..1646

# Customer name map for reference
CUSTOMERS = {
    1635: "Priya Reddy",     1636: "Kavitha Sharma",
    1637: "Ravi Kumar",      1638: "Sunitha Naidu",
    1639: "Mahesh Rao",      1640: "Ananya Pillai",
    1641: "Vijay Chowdary",  1642: "Lakshmi Devi",
    1643: "Srinivas Murthy", 1644: "Deepa Varma",
    1645: "Naveen Yadav",    1646: "Bhavana Iyer",
}

DB_URL = os.environ.get("AZURE_DB_URL") or os.environ.get("DATABASE_URL")
if not DB_URL:
    raise SystemExit("Set AZURE_DB_URL (or DATABASE_URL) — no hardcoded DB credentials.")

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "fancy_demo_manifest.json")


def main():
    if not os.path.exists(MANIFEST_PATH):
        raise SystemExit("fancy_demo_manifest.json not found — run fancy_demo_seed.py first.")

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    # Extend manifest with patch sections
    patch = manifest.setdefault("patch", {})
    patch.setdefault("updated_product_ids", [])
    patch.setdefault("snapshot_keys", [])       # (date, store_id, product_id) triples
    patch.setdefault("zeroed_inventory_ids", [])
    patch.setdefault("association_ids", [])
    patch.setdefault("customer_area_updates", [])  # [{customer_id, old_association_id}]
    patch.setdefault("staff_task_ids", [])
    patch.setdefault("estimate_ids", [])
    patch.setdefault("sales_return_ids", [])

    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # ── 1. GST rates on all products for store 53 ─────────────────────────
        print("Setting GST rates on products...")
        # Products priced for this store — fetch their product_ids
        cur.execute(
            "SELECT DISTINCT product_id FROM kirana_oltp.pricing WHERE store_id=%s AND valid_to IS NULL",
            (STORE_ID,),
        )
        product_ids = [r["product_id"] for r in cur.fetchall()]

        # Toy (12512, and board game/puzzle/soft toy new ids) → 18%; rest → 12%
        # We'll use product name to decide
        cur.execute(
            "SELECT product_id, name FROM kirana_oltp.product WHERE product_id = ANY(%s)",
            (product_ids,),
        )
        products_info = {r["product_id"]: r["name"] for r in cur.fetchall()}

        toy_words = {"toy", "puzzle", "board", "game"}
        for pid, name in products_info.items():
            gst = 18.0 if any(w in name.lower() for w in toy_words) else 12.0
            cur.execute(
                "UPDATE kirana_oltp.product SET gst_rate=%s WHERE product_id=%s",
                (gst, pid),
            )
            if pid not in patch["updated_product_ids"]:
                patch["updated_product_ids"].append(pid)

        print(f"  GST rates set on {len(product_ids)} products")

        # ── 2. Inventory snapshots (6 days × 20 products) ─────────────────────
        print("Seeding inventory snapshots...")

        # Pull actual units sold per day per product from seeded orders
        cur.execute(
            """
            SELECT o.order_date::date AS day,
                   oi.product_id,
                   SUM(oi.quantity)   AS units_sold,
                   SUM(oi.quantity * oi.unit_price) AS revenue,
                   SUM(oi.quantity * (oi.unit_price - oi.cost_price)) AS profit
            FROM kirana_oltp.order_item oi
            JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
            WHERE o.store_id = %s
            GROUP BY o.order_date::date, oi.product_id
            """,
            (STORE_ID,),
        )
        daily_sales = {}   # (day, pid) -> {units_sold, revenue, profit}
        for r in cur.fetchall():
            daily_sales[(r["day"], r["product_id"])] = {
                "units_sold": float(r["units_sold"] or 0),
                "revenue":    float(r["revenue"]    or 0),
                "profit":     float(r["profit"]     or 0),
            }

        # Pull current inventory
        cur.execute(
            "SELECT product_id, quantity FROM kirana_oltp.inventory WHERE store_id=%s",
            (STORE_ID,),
        )
        current_stock = {r["product_id"]: float(r["quantity"] or 0) for r in cur.fetchall()}

        # Pull prices
        cur.execute(
            "SELECT product_id, price FROM kirana_oltp.pricing WHERE store_id=%s AND valid_to IS NULL",
            (STORE_ID,),
        )
        prices = {r["product_id"]: float(r["price"] or 0) for r in cur.fetchall()}

        # Work backwards: current_stock is today's end-of-day.
        # For each past day, add back what was sold to get SOH at end of that day.
        all_days = [REG_DATE + timedelta(days=d) for d in range((TODAY - REG_DATE).days + 1)]
        snap_count = 0

        for pid in product_ids:
            # Rebuild SOH from today backwards
            soh = current_stock.get(pid, 0)
            for day in reversed(all_days):
                sold  = daily_sales.get((day, pid), {}).get("units_sold", 0)
                rev   = daily_sales.get((day, pid), {}).get("revenue", 0)
                proft = daily_sales.get((day, pid), {}).get("profit", 0)
                price = prices.get(pid, 0)
                # SOH at end of this day = current running value
                # For previous day we add back what was sold that day
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.inventory_snapshots
                        (snapshot_date, store_id, product_id, stock_on_hand, units_sold,
                         stock, revenue, profit, price, promo_flag)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)
                    ON CONFLICT (snapshot_date, store_id, product_id) DO UPDATE
                      SET stock_on_hand = EXCLUDED.stock_on_hand,
                          units_sold    = EXCLUDED.units_sold,
                          revenue       = EXCLUDED.revenue,
                          profit        = EXCLUDED.profit,
                          upserted_at   = now()
                    """,
                    (day, STORE_ID, pid,
                     round(soh, 2), round(sold, 2),
                     round(soh * price, 2),       # stock = monetary value
                     round(rev, 2), round(proft, 2), price),
                )
                snap_count += 1
                patch["snapshot_keys"].append({"date": day.isoformat(), "pid": pid})
                soh = soh + sold   # previous day had this much more stock

        print(f"  {snap_count} snapshot rows upserted ({len(product_ids)} products x {len(all_days)} days)")

        # ── 3. Low / zero stock on 2 items (triggers stockout alerts) ─────────
        print("Reducing stock on 2 items for stockout demo...")

        # Wall Clock (12527) → 0 units (stockout)
        # Soft Toy Bear → 3 units (low stock), find its product_id
        cur.execute(
            "SELECT product_id FROM kirana_oltp.product WHERE name IN ('Wall Clock','Soft Toy Bear')",
        )
        stockout_pids = {r["product_id"] for r in cur.fetchall()}

        for pid in stockout_pids:
            new_qty = 0 if products_info.get(pid, "") == "Wall Clock" else 3
            cur.execute(
                "UPDATE kirana_oltp.inventory SET quantity=%s WHERE store_id=%s AND product_id=%s "
                "RETURNING inventory_id",
                (new_qty, STORE_ID, pid),
            )
            row = cur.fetchone()
            if row and row["inventory_id"] not in patch["zeroed_inventory_ids"]:
                patch["zeroed_inventory_ids"].append(row["inventory_id"])

        print("  Wall Clock -> 0 units (stockout); Soft Toy Bear -> 3 units (low stock)")

        # ── 4. Area associations ──────────────────────────────────────────────
        print("Seeding area associations...")
        areas = [
            ("Green Apartments", "apartment", 85,
             "Nearby gated society — main customer cluster"),
            ("Ramesh Colony",    "colony",    120,
             "Residential colony 500m from store"),
            ("St. Xavier School","school",    None,
             "Teachers and staff frequent buyers"),
        ]
        assoc_ids = []
        for name, atype, households, notes in areas:
            cur.execute(
                """
                INSERT INTO kirana_oltp.store_association
                    (store_id, name, area_type, estimated_households, notes, is_active)
                VALUES (%s, %s, %s, %s, %s, true) RETURNING association_id
                """,
                (STORE_ID, name, atype, households, notes),
            )
            aid = cur.fetchone()["association_id"]
            assoc_ids.append(aid)
            patch["association_ids"].append(aid)

        # Link customers to areas
        area_links = [
            (1635, assoc_ids[0]),   # Priya Reddy → Green Apartments
            (1636, assoc_ids[0]),   # Kavitha Sharma → Green Apartments
            (1637, assoc_ids[1]),   # Ravi Kumar → Ramesh Colony
            (1639, assoc_ids[1]),   # Mahesh Rao → Ramesh Colony
            (1642, assoc_ids[1]),   # Lakshmi Devi → Ramesh Colony
            (1640, assoc_ids[2]),   # Ananya Pillai → School
        ]
        for cid, aid in area_links:
            cur.execute(
                "SELECT association_id FROM kirana_oltp.customer WHERE customer_id=%s",
                (cid,),
            )
            old_aid = cur.fetchone()["association_id"]
            cur.execute(
                "UPDATE kirana_oltp.customer SET association_id=%s WHERE customer_id=%s",
                (aid, cid),
            )
            patch["customer_area_updates"].append(
                {"customer_id": cid, "old_association_id": old_aid}
            )

        print(f"  {len(assoc_ids)} areas, {len(area_links)} customers linked")

        # ── 5. Staff tasks ────────────────────────────────────────────────────
        print("Seeding staff tasks...")
        tasks = [
            # Ramu Naik (cashier) — staff_id 3
            (3, "Update daily cash register report",          TODAY - timedelta(days=1), True),
            (3, "Restock Balloons Pack at front counter",     TODAY,                     False),
            (3, "Check and update shelf price labels",        TODAY + timedelta(days=2), False),
            # Preethi Rao (sales) — staff_id 4
            (4, "Arrange new arrivals on display shelf",      TODAY - timedelta(days=1), True),
            (4, "Follow up: Vijay Chowdary corporate order",  TODAY + timedelta(days=1), False),
            (4, "Prepare gift wrapping materials checklist",  TODAY + timedelta(days=2), False),
        ]
        for staff_id, title, due_date, is_done in tasks:
            created = datetime(due_date.year, due_date.month, due_date.day, 9, 0,
                               tzinfo=timezone.utc) - timedelta(days=1)
            cur.execute(
                """
                INSERT INTO kirana_oltp.staff_task
                    (store_id, staff_id, title, due_date, is_done, created_at)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING task_id
                """,
                (STORE_ID, staff_id, title, due_date, is_done, created),
            )
            patch["staff_task_ids"].append(cur.fetchone()["task_id"])

        print(f"  {len(patch['staff_task_ids'])} tasks (2 done, 4 pending)")

        # ── 6. Estimates (pending quotes) ─────────────────────────────────────
        print("Seeding estimates...")
        # Find product_ids we need
        cur.execute(
            "SELECT product_id, name FROM kirana_oltp.product WHERE name IN "
            "('Board Game','Gift Box','Pen Pack','Notebook')",
        )
        prod_map = {r["name"]: r["product_id"] for r in cur.fetchall()}

        # Estimate 1: Vijay Chowdary — corporate gifting (5x Board Game + 5x Gift Box)
        e1_lines = [
            (prod_map.get("Board Game"), "Board Game", 5, 399.0),
            (prod_map.get("Gift Box"),   "Gift Box",   5, 130.0),
        ]
        e1_total = sum(qty * price for _, _, qty, price in e1_lines)
        cur.execute(
            """
            INSERT INTO kirana_oltp.estimate
                (store_id, customer_id, customer_name, total, status, valid_until, created_at)
            VALUES (%s, %s, %s, %s, 'sent', %s, %s) RETURNING estimate_id
            """,
            (STORE_ID, 1641, "Vijay Chowdary", e1_total,
             TODAY + timedelta(days=7),
             datetime(2026, 6, 27, 11, 0, tzinfo=timezone.utc)),
        )
        eid1 = cur.fetchone()["estimate_id"]
        patch["estimate_ids"].append(eid1)
        for pid, name, qty, price in e1_lines:
            if pid:
                cur.execute(
                    "INSERT INTO kirana_oltp.estimate_item (estimate_id, product_id, name, quantity, unit_price) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (eid1, pid, name, qty, price),
                )

        # Estimate 2: Mahesh Rao — farewell packs (10x Pen Pack + 10x Notebook)
        e2_lines = [
            (prod_map.get("Pen Pack"),  "Pen Pack",  10, 45.0),
            (prod_map.get("Notebook"),  "Notebook",  10, 80.0),
        ]
        e2_total = sum(qty * price for _, _, qty, price in e2_lines)
        cur.execute(
            """
            INSERT INTO kirana_oltp.estimate
                (store_id, customer_id, customer_name, total, status, valid_until, created_at)
            VALUES (%s, %s, %s, %s, 'draft', %s, %s) RETURNING estimate_id
            """,
            (STORE_ID, 1639, "Mahesh Rao", e2_total,
             TODAY + timedelta(days=5),
             datetime(2026, 6, 28, 15, 30, tzinfo=timezone.utc)),
        )
        eid2 = cur.fetchone()["estimate_id"]
        patch["estimate_ids"].append(eid2)
        for pid, name, qty, price in e2_lines:
            if pid:
                cur.execute(
                    "INSERT INTO kirana_oltp.estimate_item (estimate_id, product_id, name, quantity, unit_price) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (eid2, pid, name, qty, price),
                )

        print(f"  2 estimates (Vijay Rs.{e1_total}, Mahesh Rs.{e2_total})")

        # ── 7. Sales return (1 return for demo) ───────────────────────────────
        print("Seeding sales return...")
        # Sunitha Naidu returned 1x Balloons Pack — wrong color
        # Find any order from Sunitha (customer_id=1638)
        cur.execute(
            "SELECT order_id FROM kirana_oltp.orders WHERE store_id=%s AND customer_id=1638 LIMIT 1",
            (STORE_ID,),
        )
        row = cur.fetchone()
        order_ref = row["order_id"] if row else None
        returned_at = datetime(2026, 6, 28, 14, 0, tzinfo=timezone.utc)
        cur.execute(
            """
            INSERT INTO kirana_oltp.sales_return
                (store_id, order_id, customer_id, reason, refund_amount, is_exchange, notes, created_at)
            VALUES (%s, %s, %s, 'wrong_item', 35.00, false,
                    'Customer received wrong colour Balloons Pack — full refund issued', %s)
            RETURNING return_id
            """,
            (STORE_ID, order_ref, 1638, returned_at),
        )
        patch["sales_return_ids"].append(cur.fetchone()["return_id"])
        print("  1 return: Sunitha Naidu — Balloons Pack refund Rs.35")

        conn.commit()
        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2)

        print(f"\nPatch done. Manifest updated -> {MANIFEST_PATH}")
        print(f"  GST set on {len(patch['updated_product_ids'])} products")
        print(f"  {len(patch['snapshot_keys'])} inventory snapshot rows")
        print(f"  {len(patch['association_ids'])} area associations, "
              f"{len(patch['customer_area_updates'])} customers linked")
        print(f"  {len(patch['staff_task_ids'])} staff tasks")
        print(f"  {len(patch['estimate_ids'])} estimates")
        print(f"  {len(patch['sales_return_ids'])} sales return")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
