"""
cleanup_categories.py
---------------------
Step 1 — Fix underscore/raw names from Blinkit JSON scraper
          e.g. "tea_powder" → "Tea Powder", "curd_&_yogurt" → "Curd & Yogurt"

Step 2 — Merge same-name categories that exist at multiple parent levels
          e.g. user-created root "Curd" + Blinkit "Curd" (under Dairy & Breakfast)
          Preference: root-level (parent IS NULL) wins; else lowest category_id wins.

Safe to re-run — no-ops when nothing to do.
"""

import psycopg2

DB = dict(
    dbname="lit_db",
    user="postgres",
    password="123456",
    host="localhost",
    port="5432",
)


def snake_to_title(name: str) -> str:
    """'tea_powder' → 'Tea Powder', 'curd_&_yogurt' → 'Curd & Yogurt'"""
    return " ".join(
        w if w == "&" else w.capitalize()
        for w in name.replace("_", " ").split()
    )


def main() -> None:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    # ── Step 1: Fix underscore names ──────────────────────────────────────────

    cur.execute(
        "SELECT category_id, name FROM kirana_oltp.category WHERE name LIKE '%\\_%' ESCAPE '\\'"
    )
    underscore_cats = cur.fetchall()

    step1_fixed = 0
    for cid, name in underscore_cats:
        new_name = snake_to_title(name)
        if new_name != name:
            cur.execute(
                "UPDATE kirana_oltp.category SET name = %s WHERE category_id = %s",
                (new_name, cid),
            )
            print(f"  Rename: '{name}' → '{new_name}'")
            step1_fixed += 1

    conn.commit()
    print(f"Step 1: {step1_fixed} name(s) fixed\n")

    # ── Step 2: Merge same-name categories across parent levels ──────────────
    # Sort: NULL parent first (root wins), then lowest category_id

    cur.execute("""
        SELECT
            LOWER(TRIM(name)) AS lower_name,
            array_agg(category_id
                ORDER BY (parent_category_id IS NOT NULL), category_id) AS ids
        FROM kirana_oltp.category
        GROUP BY LOWER(TRIM(name))
        HAVING COUNT(*) > 1
        ORDER BY LOWER(TRIM(name))
    """)
    groups = cur.fetchall()

    if not groups:
        print("Step 2: No same-name duplicates found.")
        conn.close()
        return

    print(f"Step 2: {len(groups)} duplicate group(s) found:\n")
    total_products = 0
    total_deleted = 0

    for lower_name, ids in groups:
        canonical = ids[0]
        dupes = ids[1:]

        # Remap child categories whose parent is a dupe
        cur.execute(
            "UPDATE kirana_oltp.category SET parent_category_id = %s "
            "WHERE parent_category_id = ANY(%s)",
            (canonical, dupes),
        )

        # Remap products
        cur.execute(
            "UPDATE kirana_oltp.product SET category_id = %s "
            "WHERE category_id = ANY(%s)",
            (canonical, dupes),
        )
        remapped = cur.rowcount

        # Delete duplicates
        cur.execute(
            "DELETE FROM kirana_oltp.category WHERE category_id = ANY(%s)",
            (dupes,),
        )
        deleted = cur.rowcount

        print(
            f"  '{lower_name}': kept id={canonical}, "
            f"removed {dupes}, remapped {remapped} products"
        )
        total_products += remapped
        total_deleted += deleted

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone: {total_deleted} duplicate(s) deleted, {total_products} product(s) remapped.")


if __name__ == "__main__":
    main()
