"""
Backfill estimated cost prices so profit / dead-stock / inventory-turnover KPIs
aren't starved by missing supplier costs.

WHY: order_item.cost_price is snapshotted from product_supplier at sale time and
defaults to 0 when a product has no supplier cost. Every profit calculation then
filters `cost_price > 0`, so those sales are silently dropped from the numbers.

HOW we estimate: from the product's current selling price, using CATEGORY-AWARE
gross margins (kirana/cost_estimation.py) — staples thin (~8%), personal-care /
household fat (~20%+). Override with a flat --margin if you prefer. This is an
ESTIMATE; review the dry-run before applying. Real captured costs always win.

TARGETS (pick with flags; nothing writes without --apply):
  --supplier   Fill product_supplier.cost_price where it is NULL/0 (real supplier
               rows only — does NOT invent suppliers). Fixes FUTURE sales, which
               re-snapshot cost from product_supplier.
  --orders     Backfill historical order_item.cost_price where it is 0 — prefers a
               real product_supplier cost, else the category estimate. This is
               what actually moves the existing profit KPIs.
  --margin X   Use a flat margin X (e.g. 0.12) instead of category-aware margins.
  --apply      Execute. Without it, prints impact only (dry-run).

EXAMPLES:
    python db_generation/backfill_cost_prices.py --supplier --orders
    python db_generation/backfill_cost_prices.py --supplier --orders --apply
    python db_generation/backfill_cost_prices.py --orders --margin 0.15 --apply

Connection comes from DATABASE_URL (loaded from the backend .env).
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Make the backend package importable + load its .env (script is one level down).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))

from kirana.cost_estimation import estimate_cost  # noqa: E402

# Latest active selling price per product (across stores) — the estimate basis.
_LATEST_PRICE = """
    SELECT product_id, price FROM (
        SELECT product_id, price,
               ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY valid_from DESC) AS rn
        FROM kirana_oltp.pricing
        WHERE price IS NOT NULL AND price > 0 AND valid_from <= now()
    ) t WHERE rn = 1
"""


def _engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in environment or backend .env")
    return create_engine(url)


def _cost_map(conn, flat_margin: float | None) -> dict[int, float]:
    """product_id -> estimated unit cost (category-aware unless --margin given)."""
    rows = conn.execute(text(f"""
        SELECT p.product_id, c.name AS category_name, pr.price::float AS price
        FROM kirana_oltp.product p
        LEFT JOIN kirana_oltp.category c ON c.category_id = p.category_id
        JOIN ({_LATEST_PRICE}) pr ON pr.product_id = p.product_id
    """)).mappings().all()
    out: dict[int, float] = {}
    for r in rows:
        price = r["price"]
        if not price or price <= 0:
            continue
        if flat_margin is not None:
            cost = round(price * (1 - flat_margin), 2)
        else:
            cost = estimate_cost(price, r["category_name"])
        if cost is not None and cost > 0:
            out[int(r["product_id"])] = cost
    return out


def run(do_supplier: bool, do_orders: bool, flat_margin: float | None, apply: bool) -> None:
    if not (do_supplier or do_orders):
        raise SystemExit("Nothing to do — pass --supplier and/or --orders.")
    if flat_margin is not None and not (0.0 < flat_margin < 1.0):
        raise SystemExit("--margin must be between 0 and 1 (e.g. 0.12 for 12%).")

    eng = _engine()
    mode = "APPLY" if apply else "DRY-RUN"
    basis = f"flat {flat_margin:.0%}" if flat_margin is not None else "category-aware margins"
    print(f"[{mode}] cost-price backfill · estimate basis: {basis}\n")

    with eng.begin() as conn:
        cost_map = _cost_map(conn, flat_margin)
        pids = list(cost_map)
        print(f"products with a usable price (estimable): {len(pids)}")
        if not pids:
            print("Nothing to estimate (no priced products). Run AI Price Memory first.")
            return

        if do_supplier:
            n = conn.execute(text("""
                SELECT COUNT(*) FROM kirana_oltp.product_supplier
                WHERE (cost_price IS NULL OR cost_price = 0)
                  AND product_id = ANY(:pids)
            """), {"pids": pids}).scalar() or 0
            print(f"\nproduct_supplier rows with no usable cost: {n}")
            if apply and n:
                conn.execute(text("""
                    UPDATE kirana_oltp.product_supplier
                    SET cost_price = :c
                    WHERE product_id = :pid AND (cost_price IS NULL OR cost_price = 0)
                """), [{"pid": pid, "c": c} for pid, c in cost_map.items()])
                print(f"  → filled {n} product_supplier rows")
            elif n:
                print("  → would fill (pass --apply)")

        if do_orders:
            stats = conn.execute(text("""
                SELECT COUNT(*) AS rows,
                       COALESCE(SUM(quantity * unit_price), 0) AS revenue_uncovered
                FROM kirana_oltp.order_item
                WHERE COALESCE(cost_price, 0) = 0 AND product_id = ANY(:pids)
            """), {"pids": pids}).mappings().first()
            print(f"\norder_item rows with cost 0: {stats['rows']}")
            print(f"  uncovered revenue these represent: ₹{float(stats['revenue_uncovered']):,.0f}")
            if apply and stats["rows"]:
                conn.execute(text("""
                    UPDATE kirana_oltp.order_item oi
                    SET cost_price = COALESCE(
                        (SELECT ps.cost_price FROM kirana_oltp.product_supplier ps
                         WHERE ps.product_id = :pid AND ps.cost_price > 0
                         ORDER BY ps.cost_price LIMIT 1),
                        :c)
                    WHERE oi.product_id = :pid AND COALESCE(oi.cost_price, 0) = 0
                """), [{"pid": pid, "c": c} for pid, c in cost_map.items()])
                print(f"  → backfilled {stats['rows']} order_item rows")
            elif stats["rows"]:
                print("  → would backfill (pass --apply)")

    print(f"\n[{mode}] done." + ("" if apply else "  No changes written."))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill estimated cost prices.")
    ap.add_argument("--supplier", action="store_true", help="fill product_supplier.cost_price gaps")
    ap.add_argument("--orders", action="store_true", help="backfill historical order_item.cost_price")
    ap.add_argument("--margin", type=float, default=None,
                    help="flat margin override (e.g. 0.12); default = category-aware")
    ap.add_argument("--apply", action="store_true", help="execute (otherwise dry-run)")
    args = ap.parse_args()
    run(args.supplier, args.orders, args.margin, args.apply)
