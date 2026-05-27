"""
seed_blinkit_prices.py
----------------------
Populates kirana_oltp.pricing.(price, mrp) from the Blinkit scraper JSON files.

The original seed_blinkit_catalog.py wrote name/brand/sku/category but DROPPED
the `price` and `mrp` fields from each JSON product, so most catalog products
end up with no price in lit_db. Result: scanning a barcode returns ₹0.

Scope (conservative by default):
  - Only touches kirana_oltp.pricing — never product, never inventory,
    never barcode. The product table has 244 rows with real-world POS-scan
    barcodes; those are matched by empty SKU so the join below cannot hit
    them anyway.
  - By default, only updates rows where the store actually stocks the
    product (inventory row exists) AND the current price is missing or zero.
  - --overwrite        : also replaces non-zero prices with the Blinkit value.
  - --all-stores       : also seeds pricing for stores that haven't stocked
                         the product yet (creates new pricing rows for every
                         store × matched product). Use with care; will write
                         ~150k rows on a 15-store DB.
  - --store-id N       : limit to a single store.
  - --dry-run          : show counts only, write nothing.

Match key: kirana_oltp.product.sku = 'KAI-' || JSON.product_id

Run:
    python seed_blinkit_prices.py                 # safe default
    python seed_blinkit_prices.py --overwrite     # also refresh stale prices
    python seed_blinkit_prices.py --all-stores    # also fresh stores
    python seed_blinkit_prices.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import psycopg2
from psycopg2.extras import execute_values

DB = dict(
    dbname="lit_db",
    user="postgres",
    password="123456",
    host="localhost",
    port="5432",
)

KIRANA_DATA_DIR = os.environ.get(
    "BLINKIT_DATA_DIR",
    r"C:\Users\Bhanuprakash\Documents\misc\blinkit-scrapper\kirana_data\products",
)


def load_blinkit_prices() -> dict[str, tuple[float | None, float | None]]:
    """Return {sku: (price, mrp)} for every JSON entry that has at least price."""
    out: dict[str, tuple[float | None, float | None]] = {}
    if not os.path.isdir(KIRANA_DATA_DIR):
        sys.exit(f"BLINKIT_DATA_DIR not found: {KIRANA_DATA_DIR}")
    for fname in sorted(os.listdir(KIRANA_DATA_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(KIRANA_DATA_DIR, fname), encoding="utf-8") as f:
            for p in json.load(f):
                pid = str(p.get("product_id") or "").strip()
                if not pid:
                    continue
                price = p.get("price")
                mrp = p.get("mrp")
                if price is None and mrp is None:
                    continue
                sku = f"KAI-{pid}"
                # Prefer the first occurrence; duplicates across categories
                # are noise (same product, same price).
                out.setdefault(sku, (price, mrp))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overwrite", action="store_true",
                    help="Also update rows that already have a non-zero price.")
    ap.add_argument("--all-stores", action="store_true",
                    help="Also create pricing rows for store/product pairs "
                         "with no inventory yet.")
    ap.add_argument("--store-id", type=int, default=None,
                    help="Limit operations to a single store_id.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Loading Blinkit prices from {KIRANA_DATA_DIR}")
    blinkit = load_blinkit_prices()
    print(f"  {len(blinkit):,} JSON products carry price/mrp")

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    # Map sku -> product_id for the matched catalog rows.
    cur.execute(
        "SELECT product_id, sku FROM kirana_oltp.product WHERE sku LIKE 'KAI-%'"
    )
    sku_to_pid: dict[str, int] = {row[1]: row[0] for row in cur.fetchall()}
    print(f"  {len(sku_to_pid):,} 'KAI-*' rows in kirana_oltp.product")

    pid_to_price: dict[int, tuple[float | None, float | None]] = {}
    matched = 0
    for sku, (price, mrp) in blinkit.items():
        pid = sku_to_pid.get(sku)
        if pid is None:
            continue
        pid_to_price[pid] = (price, mrp)
        matched += 1
    print(f"  {matched:,} products in BOTH JSON and DB\n")

    # Decide which (store_id, product_id) pairs to write.
    store_filter = "AND store_id = %s" if args.store_id else ""
    params = (args.store_id,) if args.store_id else ()

    # ── Pass 1 — existing pricing rows that need filling/overwriting ──────────
    cur.execute(
        f"""
        SELECT store_id, product_id, price
        FROM kirana_oltp.pricing
        WHERE product_id = ANY(%s) {store_filter}
        """,
        ([list(pid_to_price)] if not args.store_id
         else [list(pid_to_price), args.store_id]),
    )
    existing_rows = cur.fetchall()
    to_update: list[tuple[float | None, float | None, int, int]] = []
    for sid, pid, current_price in existing_rows:
        price, mrp = pid_to_price[pid]
        if current_price in (None, 0) or (args.overwrite and current_price):
            to_update.append((price, mrp, sid, pid))

    # ── Pass 2 — pricing rows that don't exist yet ────────────────────────────
    if args.all_stores:
        # All (store, matched_product) pairs that have NO pricing row yet.
        cur.execute(
            f"""
            SELECT s.store_id, p.product_id
            FROM kirana_oltp.store s
            CROSS JOIN UNNEST(%s::int[]) AS p(product_id)
            LEFT JOIN kirana_oltp.pricing pr
              ON pr.store_id = s.store_id AND pr.product_id = p.product_id
            WHERE pr.pricing_id IS NULL
              AND s.is_deleted = FALSE
              {store_filter.replace('store_id', 's.store_id')}
            """,
            ([list(pid_to_price), args.store_id] if args.store_id
             else [list(pid_to_price)]),
        )
    else:
        # Only stores that already stock the product (inventory row exists)
        # but lack a pricing row.
        cur.execute(
            f"""
            SELECT i.store_id, i.product_id
            FROM kirana_oltp.inventory i
            LEFT JOIN kirana_oltp.pricing pr
              ON pr.store_id = i.store_id AND pr.product_id = i.product_id
            WHERE i.product_id = ANY(%s)
              AND pr.pricing_id IS NULL
              {store_filter.replace('store_id', 'i.store_id')}
            """,
            ([list(pid_to_price), args.store_id] if args.store_id
             else [list(pid_to_price)]),
        )
    missing_rows = cur.fetchall()
    to_insert: list[tuple[int, int, float | None, float | None]] = []
    for sid, pid in missing_rows:
        price, mrp = pid_to_price[pid]
        to_insert.append((sid, pid, price, mrp))

    print(f"Will UPDATE  : {len(to_update):>6} existing rows")
    print(f"Will INSERT  : {len(to_insert):>6} new rows")

    if args.dry_run:
        print("\n[dry-run] no writes performed.")
        return

    if not to_update and not to_insert:
        print("\nNothing to do.")
        return

    # ── Apply changes ─────────────────────────────────────────────────────────
    # valid_from MUST be UTC (backend compares against datetime.utcnow()).
    if to_update:
        execute_values(
            cur,
            """
            UPDATE kirana_oltp.pricing AS pr
            SET    price = v.price,
                   mrp   = v.mrp,
                   valid_from = NOW() - INTERVAL '1 minute'
            FROM   (VALUES %s) AS v(price, mrp, store_id, product_id)
            WHERE  pr.store_id = v.store_id AND pr.product_id = v.product_id
            """,
            to_update,
            template="(%s::numeric, %s::numeric, %s::int, %s::int)",
        )

    if to_insert:
        # `pricing` is a history table — no unique key on (store, product),
        # multiple rows over time are intentional. The query that builds
        # `to_insert` already excludes pairs that have ANY existing pricing
        # row, so a plain INSERT is correct (no ON CONFLICT needed).
        execute_values(
            cur,
            """
            INSERT INTO kirana_oltp.pricing (store_id, product_id, price, mrp, valid_from)
            VALUES %s
            """,
            [(sid, pid, price, mrp,) for (sid, pid, price, mrp) in to_insert],
            template="(%s, %s, %s, %s, NOW() - INTERVAL '1 minute')",
            page_size=2000,
        )

    conn.commit()

    # ── Sanity check ──────────────────────────────────────────────────────────
    cur.execute(
        "SELECT COUNT(*) FROM kirana_oltp.pricing WHERE price IS NULL OR price = 0"
    )
    still_zero = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM kirana_oltp.pricing")
    total_pricing = cur.fetchone()[0]

    cur.close()
    conn.close()

    print(f"\nDone. pricing rows total: {total_pricing:,} "
          f"(still missing price: {still_zero:,})")
    print("Barcode column on kirana_oltp.product was NOT touched.")


if __name__ == "__main__":
    main()
