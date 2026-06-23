from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class MultiStoreRepositoryMixin:
    """Module M2 — multi-store rollup (chains / multi-outlet owners).

    A store_group is one owner's chain; member stores carry store.group_id. The
    rollup compares member stores and aggregates by city/region over a window.
    Single-store owners (no group) just see their own store.
    """

    # ── Group membership ────────────────────────────────────────────────────
    def get_group_for_store(self, store_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                SELECT g.group_id, g.name, g.owner_user_id
                FROM kirana_oltp.store s
                JOIN kirana_oltp.store_group g ON g.group_id = s.group_id
                WHERE s.store_id = :sid
                """),
                {"sid": store_id},
            ).mappings().first()
        return dict(row) if row else None

    def group_store_ids(self, store_id: int) -> list[int]:
        """All store_ids in the caller's group, or just [store_id] if ungrouped."""
        grp = self.get_group_for_store(store_id)
        if not grp:
            return [store_id]
        with self._conn() as conn:
            rows = conn.execute(
                text("SELECT store_id FROM kirana_oltp.store WHERE group_id = :gid"),
                {"gid": grp["group_id"]},
            ).scalars().all()
        return [int(r) for r in rows] or [store_id]

    def create_store_group(self, name: str, owner_user_id: int | None, store_ids: list[int]) -> dict:
        with self._conn() as conn:
            gid = conn.execute(
                text("""
                INSERT INTO kirana_oltp.store_group (name, owner_user_id)
                VALUES (:name, :owner) RETURNING group_id
                """),
                {"name": name, "owner": owner_user_id},
            ).scalar()
            if store_ids:
                conn.execute(
                    text("UPDATE kirana_oltp.store SET group_id = :gid "
                         "WHERE store_id = ANY(:ids)"),
                    {"gid": gid, "ids": store_ids},
                )
            conn.commit()
        return {"group_id": int(gid), "name": name, "store_ids": store_ids}

    def ensure_owner_group(self, user_id: int) -> dict | None:
        """If the owner runs 2+ stores, make sure they have a store_group and that
        their ungrouped stores are assigned to it. Called after add-store, so the
        group appears automatically on the owner's second outlet. Never clobbers
        a store already manually assigned to another group. Returns the group, or
        None for single-store owners."""
        with self._conn() as conn:
            store_ids = [int(r) for r in conn.execute(
                text("""
                SELECT su.store_id FROM kirana_oltp.store_user su
                JOIN kirana_oltp.store s ON s.store_id = su.store_id
                 AND NOT COALESCE(s.is_deleted, FALSE)
                WHERE su.user_id = :uid AND su.role = 'owner'
                """),
                {"uid": user_id},
            ).scalars().all()]
            if len(store_ids) < 2:
                return None
            grp = conn.execute(
                text("SELECT group_id, name FROM kirana_oltp.store_group "
                     "WHERE owner_user_id = :uid ORDER BY group_id LIMIT 1"),
                {"uid": user_id},
            ).mappings().first()
            if grp:
                gid, name = grp["group_id"], grp["name"]
            else:
                owner = conn.execute(
                    text("SELECT COALESCE(full_name, username) FROM kirana_oltp.users "
                         "WHERE user_id = :uid"),
                    {"uid": user_id},
                ).scalar()
                name = f"{owner or 'Owner'}'s stores"
                gid = conn.execute(
                    text("INSERT INTO kirana_oltp.store_group (name, owner_user_id) "
                         "VALUES (:n, :uid) RETURNING group_id"),
                    {"n": name, "uid": user_id},
                ).scalar()
            conn.execute(
                text("UPDATE kirana_oltp.store SET group_id = :gid "
                     "WHERE store_id = ANY(:ids) AND group_id IS NULL"),
                {"gid": gid, "ids": store_ids},
            )
            conn.commit()
        return {"group_id": int(gid), "name": name, "store_ids": store_ids}

    def assign_store_to_group(self, store_id: int, group_id: int | None) -> bool:
        with self._conn() as conn:
            n = conn.execute(
                text("UPDATE kirana_oltp.store SET group_id = :gid WHERE store_id = :sid"),
                {"gid": group_id, "sid": store_id},
            ).rowcount
            conn.commit()
        return n > 0

    def list_store_groups(self) -> list[dict]:
        """Admin: every group with its member stores (for the back-office UI)."""
        with self._conn() as conn:
            groups = conn.execute(
                text("""
                SELECT g.group_id, g.name, g.owner_user_id,
                       COALESCE(u.full_name, u.username) AS owner_name
                FROM kirana_oltp.store_group g
                LEFT JOIN kirana_oltp.users u ON u.user_id = g.owner_user_id
                ORDER BY g.group_id DESC
                """)
            ).mappings().all()
            members = conn.execute(
                text("""
                SELECT store_id, name AS store_name,
                       COALESCE(city, region, location, '—') AS area, group_id
                FROM kirana_oltp.store
                WHERE group_id IS NOT NULL AND NOT is_deleted
                ORDER BY name
                """)
            ).mappings().all()
        by_group: dict[int, list] = {}
        for m in members:
            by_group.setdefault(m["group_id"], []).append(dict(m))
        return [
            {**dict(g), "stores": by_group.get(g["group_id"], [])}
            for g in groups
        ]

    # ── Rollup ──────────────────────────────────────────────────────────────
    def store_rollup(self, store_id: int, days: int = 30) -> dict:
        """Per-store and per-city/region sales comparison across the group."""
        ids = self.group_store_ids(store_id)
        grp = self.get_group_for_store(store_id)
        per_store_sql = """
        SELECT s.store_id, s.name AS store_name,
               COALESCE(s.city, s.region, s.location, '—') AS area,
               s.region, s.city,
               COUNT(DISTINCT o.order_id) AS orders,
               ROUND(COALESCE(SUM(o.total_amount), 0)::numeric, 2) AS revenue,
               ROUND(COALESCE(AVG(o.total_amount), 0)::numeric, 2) AS avg_bill
        FROM kirana_oltp.store s
        LEFT JOIN kirana_oltp.orders o
            ON o.store_id = s.store_id AND o.order_status = 'completed'
            AND o.order_date >= NOW() - (:days || ' days')::interval
        WHERE s.store_id = ANY(:ids)
        GROUP BY s.store_id, s.name, s.city, s.region, s.location
        ORDER BY revenue DESC
        """
        by_area_sql = """
        SELECT COALESCE(s.city, s.region, '—') AS area,
               COUNT(DISTINCT s.store_id) AS stores,
               COUNT(DISTINCT o.order_id) AS orders,
               ROUND(COALESCE(SUM(o.total_amount), 0)::numeric, 2) AS revenue
        FROM kirana_oltp.store s
        LEFT JOIN kirana_oltp.orders o
            ON o.store_id = s.store_id AND o.order_status = 'completed'
            AND o.order_date >= NOW() - (:days || ' days')::interval
        WHERE s.store_id = ANY(:ids)
        GROUP BY COALESCE(s.city, s.region, '—')
        ORDER BY revenue DESC
        """
        with self._conn() as conn:
            per_store = [dict(r) for r in conn.execute(
                text(per_store_sql), {"ids": ids, "days": days}).mappings().all()]
            by_area = [dict(r) for r in conn.execute(
                text(by_area_sql), {"ids": ids, "days": days}).mappings().all()]
        total_rev = sum(float(r["revenue"] or 0) for r in per_store)
        return {
            "group_name": grp["name"] if grp else None,
            "store_count": len(ids),
            "is_multi_store": len(ids) > 1,
            "total_revenue": round(total_rev, 2),
            "days": days,
            "by_store": per_store,
            "by_area": by_area,
        }
