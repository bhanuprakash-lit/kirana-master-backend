"""
seed_blinkit_catalog.py
-----------------------
Seeds kirana_oltp.category and kirana_oltp.product from the Blinkit scraper's
kirana_data/products/*.json files.

Safe rules:
  - Categories resolved by (name, parent_category_id) — never duplicated.
  - Products: ON CONFLICT (sku) DO NOTHING — safe to re-run.
  - Never touches: inventory, pricing, orders, or any store-specific table.

Usage:
    python seed_blinkit_catalog.py
    BLINKIT_DATA_DIR=/path/to/kirana_data/products python seed_blinkit_catalog.py
"""

import json
import os
import psycopg2

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

# Map JSON filename stem → parent category display name
FILE_TO_PARENT: dict[str, str] = {
    "beverages":          "Beverages",
    "cleaning_household": "Cleaning & Household",
    "dairy_breakfast":    "Dairy & Breakfast",
    "fruits_vegetables":  "Fruits & Vegetables",
    "instant_food":       "Instant Food",
    "personal_care":      "Personal Care",
    "sauces_spreads":     "Sauces & Spreads",
    "snacks":             "Snacks & Biscuits",
    "staples":            "Staples",
}

# Subcategory names considered perishable
PERISHABLE_SUBCATS: set[str] = {
    "milk", "curd", "curd & yogurt", "eggs", "cheese", "butter & more",
    "paneer & tofu", "flavored milk", "ice cream & frozen dessert",
    "fresh vegetables", "fruits", "leafies & herbs", "herbs & seasoning",
    "batter", "frozen veg", "frozen peas & corn",
}

# Parent categories where everything is perishable
PERISHABLE_PARENTS: set[str] = {"Fruits & Vegetables"}


def get_or_create_category(cur, name: str, parent_id: int | None) -> int:
    cur.execute(
        """
        SELECT category_id FROM kirana_oltp.category
        WHERE name = %s
          AND (parent_category_id IS NOT DISTINCT FROM %s)
        """,
        (name, parent_id),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """
        INSERT INTO kirana_oltp.category (name, parent_category_id)
        VALUES (%s, %s)
        RETURNING category_id
        """,
        (name, parent_id),
    )
    return cur.fetchone()[0]


def is_perishable(parent_name: str, subcat_name: str) -> bool:
    if parent_name in PERISHABLE_PARENTS:
        return True
    return subcat_name.lower() in PERISHABLE_SUBCATS


def main() -> None:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    total_inserted = 0
    total_skipped = 0
    total_errors = 0

    print(f"Seeding from: {KIRANA_DATA_DIR}\n")

    for filename in sorted(os.listdir(KIRANA_DATA_DIR)):
        if not filename.endswith(".json"):
            continue
        stem = filename[:-5]
        parent_name = FILE_TO_PARENT.get(stem)
        if not parent_name:
            print(f"  [SKIP] {filename} — no parent mapping")
            continue

        filepath = os.path.join(KIRANA_DATA_DIR, filename)
        with open(filepath, encoding="utf-8") as f:
            products: list[dict] = json.load(f)

        parent_id = get_or_create_category(cur, parent_name, None)

        subcat_cache: dict[str, int] = {}
        inserted = skipped = errors = 0

        for p in products:
            subcat_name = (p.get("category_name") or "").strip() or parent_name
            if subcat_name not in subcat_cache:
                subcat_cache[subcat_name] = get_or_create_category(cur, subcat_name, parent_id)
            subcat_id = subcat_cache[subcat_name]

            sku = f"BL-{p['product_id']}"
            brand = (p.get("brand") or "").strip()[:100] or None
            perishable = is_perishable(parent_name, subcat_name)

            try:
                cur.execute(
                    """
                    INSERT INTO kirana_oltp.product
                        (category_id, name, brand, sku, is_loose, is_perishable)
                    VALUES (%s, %s, %s, %s, FALSE, %s)
                    ON CONFLICT (sku) DO NOTHING
                    """,
                    (subcat_id, p["name"][:200], brand, sku, perishable),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                print(f"    ERROR {sku} '{p['name'][:40]}': {e}")
                conn.rollback()
                # Re-establish transaction state
                cur = conn.cursor()
                continue

        conn.commit()

        subcats = len(subcat_cache)
        print(
            f"  {parent_name:<26} {subcats:>3} subcats  "
            f"{inserted:>4} inserted  {skipped:>4} skipped  {errors:>2} errors"
        )
        total_inserted += inserted
        total_skipped += skipped
        total_errors += errors

    cur.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"  Total products inserted : {total_inserted:>5}")
    print(f"  Total products skipped  : {total_skipped:>5}  (already existed)")
    print(f"  Total errors            : {total_errors:>5}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
