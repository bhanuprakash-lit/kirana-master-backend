"""Per-store category groups (G7).

Products are global on purpose — that's the contribution model: one store adds a
product, every other store can scan the barcode and set its own price. `pricing`
and `inventory` are already per store, so nothing about the catalog needs to be
privatised. What *isn't* per store today is how the categories are organised, and
that's what this module adds.

A group is a store-owned bucket over the shared categories ("Kids & treats" =
chips + biscuits + ice cream). It is a second axis, on purpose: the existing
`category.parent_category_id` is the merchandising taxonomy (Beverages -> Tea),
it's global, and a store's own grouping cuts across it.

Resolution order for a store:
  1. the store's own groups, if it has any;
  2. otherwise the per-vertical default template (`store_id IS NULL`).

The first write forks the template into store-owned rows, so the table stays
empty for stores that never customise.
"""
from __future__ import annotations

from sqlalchemy import text


def _vertical_of(conn, store_id: int) -> str:
    row = conn.execute(
        text("""
            SELECT COALESCE(vertical_code, 'grocery')
            FROM kirana_oltp.store WHERE store_id = :sid
        """),
        {"sid": store_id},
    ).first()
    return (row[0] if row else "grocery") or "grocery"


def has_own_groups(conn, store_id: int) -> bool:
    return bool(
        conn.execute(
            text("""
                SELECT 1 FROM kirana_oltp.category_group
                WHERE store_id = :sid LIMIT 1
            """),
            {"sid": store_id},
        ).first()
    )


def list_groups(conn, store_id: int) -> list[dict]:
    """Groups for a store, with their categories and live product counts.

    Counts are scoped to what the store actually stocks (via `inventory`), not
    the whole global catalog — a grocery store shouldn't see "767 products" under
    Kids when it stocks eleven of them.
    """
    vertical = _vertical_of(conn, store_id)
    own = has_own_groups(conn, store_id)

    rows = conn.execute(
        text("""
            SELECT g.group_id, g.name, g.seed_key, g.sort_order, g.is_active,
                   (g.store_id IS NOT NULL) AS is_custom,
                   c.category_id, c.name AS category_name,
                   (SELECT COUNT(DISTINCT i.product_id)
                      FROM kirana_oltp.inventory i
                      JOIN kirana_oltp.product p ON p.product_id = i.product_id
                     WHERE i.store_id = :sid AND p.category_id = c.category_id
                   ) AS stocked
              FROM kirana_oltp.category_group g
              LEFT JOIN kirana_oltp.category_group_member m
                     ON m.group_id = g.group_id
              LEFT JOIN kirana_oltp.category c
                     ON c.category_id = m.category_id
             WHERE g.vertical_code = :vc
               AND g.is_active = TRUE
               AND (g.store_id = :sid OR (:own = FALSE AND g.store_id IS NULL))
             ORDER BY g.sort_order, g.name, c.name
        """),
        {"sid": store_id, "vc": vertical, "own": own},
    ).fetchall()

    out: dict[int, dict] = {}
    for r in rows:
        g = out.setdefault(
            r.group_id,
            {
                "group_id": r.group_id,
                "name": r.name,
                # Non-null only while the group still carries its seeded name.
                # The app localises on this; a renamed group shows `name` as-is,
                # because a DB string can only ever be one language.
                "seed_key": r.seed_key,
                "sort_order": r.sort_order,
                "is_custom": r.is_custom,
                "categories": [],
                "stocked_products": 0,
            },
        )
        if r.category_id is not None:
            g["categories"].append(
                {
                    "category_id": r.category_id,
                    "name": r.category_name,
                    "stocked_products": int(r.stocked or 0),
                }
            )
            g["stocked_products"] += int(r.stocked or 0)
    return list(out.values())


def ungrouped_categories(conn, store_id: int) -> list[dict]:
    """Categories the store stocks that no active group covers.

    Without this the grouped view silently hides stock, which is the failure mode
    that makes owners stop trusting it.
    """
    vertical = _vertical_of(conn, store_id)
    own = has_own_groups(conn, store_id)
    rows = conn.execute(
        text("""
            SELECT c.category_id, c.name,
                   COUNT(DISTINCT i.product_id) AS stocked
              FROM kirana_oltp.inventory i
              JOIN kirana_oltp.product p ON p.product_id = i.product_id
              JOIN kirana_oltp.category c ON c.category_id = p.category_id
             WHERE i.store_id = :sid
               AND NOT EXISTS (
                    SELECT 1
                      FROM kirana_oltp.category_group_member m
                      JOIN kirana_oltp.category_group g ON g.group_id = m.group_id
                     WHERE m.category_id = c.category_id
                       AND g.is_active = TRUE
                       AND g.vertical_code = :vc
                       AND (g.store_id = :sid
                            OR (:own = FALSE AND g.store_id IS NULL))
               )
             GROUP BY c.category_id, c.name
             ORDER BY stocked DESC, c.name
        """),
        {"sid": store_id, "vc": vertical, "own": own},
    ).fetchall()
    return [
        {"category_id": r.category_id, "name": r.name,
         "stocked_products": int(r.stocked or 0)}
        for r in rows
    ]


def fork_groups_for_store(conn, store_id: int) -> None:
    """Copy the vertical template into store-owned rows. No-op if already forked.

    Called before any mutation. Editing one group would otherwise have to either
    mutate the shared template (breaking every other store on that vertical) or
    leave the store with a single group and no others.
    """
    if has_own_groups(conn, store_id):
        return
    vertical = _vertical_of(conn, store_id)
    conn.execute(
        text("""
            WITH copied AS (
                INSERT INTO kirana_oltp.category_group
                    (store_id, vertical_code, name, seed_key, sort_order, is_active)
                SELECT :sid, g.vertical_code, g.name, g.seed_key, g.sort_order, g.is_active
                  FROM kirana_oltp.category_group g
                 WHERE g.store_id IS NULL AND g.vertical_code = :vc
             RETURNING group_id, seed_key
            )
            INSERT INTO kirana_oltp.category_group_member (group_id, category_id)
            SELECT copied.group_id, m.category_id
              FROM copied
              JOIN kirana_oltp.category_group src
                ON src.store_id IS NULL AND src.vertical_code = :vc
               AND src.seed_key IS NOT DISTINCT FROM copied.seed_key
              JOIN kirana_oltp.category_group_member m ON m.group_id = src.group_id
            ON CONFLICT (group_id, category_id) DO NOTHING
        """),
        {"sid": store_id, "vc": vertical},
    )


def _own_group(conn, store_id: int, group_id: int):
    row = conn.execute(
        text("""
            SELECT group_id FROM kirana_oltp.category_group
             WHERE group_id = :gid AND store_id = :sid
        """),
        {"gid": group_id, "sid": store_id},
    ).first()
    return row[0] if row else None


def resolve_group(conn, store_id: int, group_id: int):
    """Map a client-supplied group id onto this store's own copy.

    A store that hasn't customised yet lists the *template* groups, so the id it
    sends back belongs to a `store_id IS NULL` row. Forking copies those rows
    under new ids, which would leave that id pointing at nothing. Match the copy
    by `seed_key` (or name, for a template row that has none).

    Returns None when the id is neither this store's nor a template for its
    vertical — which is what stops one store addressing another's groups.
    """
    own = _own_group(conn, store_id, group_id)
    if own is not None:
        return own

    vertical = _vertical_of(conn, store_id)
    tpl = conn.execute(
        text("""
            SELECT seed_key, name FROM kirana_oltp.category_group
             WHERE group_id = :gid AND store_id IS NULL AND vertical_code = :vc
        """),
        {"gid": group_id, "vc": vertical},
    ).first()
    if tpl is None:
        return None

    row = conn.execute(
        text("""
            SELECT group_id FROM kirana_oltp.category_group
             WHERE store_id = :sid
               AND (( :key IS NOT NULL AND seed_key = :key )
                 OR ( :key IS NULL AND lower(name) = lower(:name) ))
             LIMIT 1
        """),
        {"sid": store_id, "key": tpl.seed_key, "name": tpl.name},
    ).first()
    return row[0] if row else None


def create_group(conn, store_id: int, name: str, category_ids: list[int]) -> int:
    fork_groups_for_store(conn, store_id)
    vertical = _vertical_of(conn, store_id)
    gid = conn.execute(
        text("""
            INSERT INTO kirana_oltp.category_group
                (store_id, vertical_code, name, seed_key, sort_order)
            VALUES (:sid, :vc, :name, NULL,
                    COALESCE((SELECT MAX(sort_order) + 10
                                FROM kirana_oltp.category_group
                               WHERE store_id = :sid), 10))
         RETURNING group_id
        """),
        {"sid": store_id, "vc": vertical, "name": name.strip()},
    ).scalar()
    set_members(conn, store_id, gid, category_ids)
    return int(gid)


def rename_group(conn, store_id: int, group_id: int, name: str) -> bool:
    fork_groups_for_store(conn, store_id)
    # seed_key is cleared: once an owner picks their own wording, the app must
    # stop substituting a translation for it.
    gid = resolve_group(conn, store_id, group_id)
    if gid is None:
        return False
    result = conn.execute(
        text("""
            UPDATE kirana_oltp.category_group
               SET name = :name, seed_key = NULL
             WHERE group_id = :gid AND store_id = :sid
        """),
        {"name": name.strip(), "gid": gid, "sid": store_id},
    )
    return result.rowcount > 0


def delete_group(conn, store_id: int, group_id: int) -> bool:
    fork_groups_for_store(conn, store_id)
    gid = resolve_group(conn, store_id, group_id)
    if gid is None:
        return False
    result = conn.execute(
        text("""
            DELETE FROM kirana_oltp.category_group
             WHERE group_id = :gid AND store_id = :sid
        """),
        {"gid": gid, "sid": store_id},
    )
    return result.rowcount > 0


def set_members(conn, store_id: int, group_id: int, category_ids: list[int]) -> bool:
    """Replace a group's categories wholesale."""
    fork_groups_for_store(conn, store_id)
    gid = resolve_group(conn, store_id, group_id)
    if gid is None:
        return False
    conn.execute(
        text("DELETE FROM kirana_oltp.category_group_member WHERE group_id = :gid"),
        {"gid": gid},
    )
    ids = [int(c) for c in dict.fromkeys(category_ids or [])]
    if ids:
        conn.execute(
            text("""
                INSERT INTO kirana_oltp.category_group_member (group_id, category_id)
                SELECT :gid, c.category_id
                  FROM kirana_oltp.category c
                 WHERE c.category_id = ANY(:ids)
                ON CONFLICT (group_id, category_id) DO NOTHING
            """),
            {"gid": gid, "ids": ids},
        )
    return True


def reset_to_defaults(conn, store_id: int) -> None:
    """Drop the store's customisation and fall back to the vertical template."""
    conn.execute(
        text("DELETE FROM kirana_oltp.category_group WHERE store_id = :sid"),
        {"sid": store_id},
    )
