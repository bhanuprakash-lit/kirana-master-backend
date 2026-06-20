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

    def assign_store_to_group(self, store_id: int, group_id: int | None) -> bool:
        with self._conn() as conn:
            n = conn.execute(
                text("UPDATE kirana_oltp.store SET group_id = :gid WHERE store_id = :sid"),
                {"gid": group_id, "sid": store_id},
            ).rowcount
            conn.commit()
        return n > 0

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
