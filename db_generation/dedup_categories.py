"""
dedup_categories.py
-------------------
Removes duplicate categories (same name + same parent) in kirana_oltp.category.
Keeps the lowest category_id as canonical, remaps products, then deletes dupes.
Safe to re-run — no-ops if no duplicates exist.
"""

import psycopg2

DB = dict(
    dbname="lit_db",
    user="postgres",
    password="123456",
    host="localhost",
    port="5432",
)


def main() -> None:
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT name, parent_category_id,
               array_agg(category_id ORDER BY category_id) AS ids,
               COUNT(*) AS cnt
        FROM kirana_oltp.category
        GROUP BY name, parent_category_id
        HAVING COUNT(*) > 1
        ORDER BY name
    """)
    groups = cur.fetchall()

    if not groups:
        print("No duplicate categories found — nothing to do.")
        conn.close()
        return

    print(f"Found {len(groups)} duplicate group(s):\n")

    total_remapped = 0
    total_deleted = 0

    for name, parent_id, ids, _ in groups:
        canonical = ids[0]
        dupes = ids[1:]

        cur.execute(
            "UPDATE kirana_oltp.product SET category_id = %s WHERE category_id = ANY(%s)",
            (canonical, dupes),
        )
        remapped = cur.rowcount

        cur.execute(
            "DELETE FROM kirana_oltp.category WHERE category_id = ANY(%s)",
            (dupes,),
        )
        deleted = cur.rowcount

        parent_label = f"parent={parent_id}" if parent_id else "root"
        print(
            f"  '{name}' ({parent_label}): "
            f"kept id={canonical}, removed {dupes}, remapped {remapped} products"
        )
        total_remapped += remapped
        total_deleted += deleted

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone: {total_deleted} duplicate(s) removed, {total_remapped} product(s) remapped.")


if __name__ == "__main__":
    main()
