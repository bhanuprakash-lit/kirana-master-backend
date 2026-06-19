from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class AssociationsRepositoryMixin:
    def list_associations(self, store_id: int) -> list[dict]:
        sql = """
        SELECT association_id, store_id, name, area_type,
               estimated_households, notes, is_active, created_at
        FROM kirana_oltp.store_association
        WHERE store_id = :sid
        ORDER BY created_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result

    def add_association(
        self,
        store_id: int,
        name: str,
        area_type: str,
        estimated_households: int | None,
        notes: str | None,
    ) -> dict:
        sql = """
        INSERT INTO kirana_oltp.store_association
            (store_id, name, area_type, estimated_households, notes)
        VALUES (:sid, :name, :atype, :hh, :notes)
        RETURNING *
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql),
                    {
                        "sid": store_id,
                        "name": name,
                        "atype": area_type,
                        "hh": estimated_households,
                        "notes": notes,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        d = dict(row)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        return d

    def update_association(
        self, association_id: int, store_id: int, **fields
    ) -> dict | None:
        allowed = {"name", "area_type", "estimated_households", "notes", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        sql = f"""
        UPDATE kirana_oltp.store_association
        SET {set_clause}
        WHERE association_id = :aid AND store_id = :sid
        RETURNING *
        """
        params = {**updates, "aid": association_id, "sid": store_id}
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        if not row:
            return None
        d = dict(row)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        return d

    def delete_association(self, association_id: int, store_id: int) -> bool:
        sql = """
        DELETE FROM kirana_oltp.store_association
        WHERE association_id = :aid AND store_id = :sid
        """
        with self._conn() as conn:
            result = conn.execute(text(sql), {"aid": association_id, "sid": store_id})
            conn.commit()
        return result.rowcount > 0

    def get_association_heatmap(self, store_id: int) -> list[dict]:
        """Per-association sales metrics derived from customer purchase history."""
        sql = """
        SELECT
            a.association_id,
            a.name                  AS area_name,
            a.area_type,
            a.estimated_households,
            COUNT(DISTINCT c.customer_id)               AS customer_count,
            COUNT(o.order_id)                           AS total_orders,
            COALESCE(SUM(o.total_amount), 0)::float     AS total_revenue,
            COALESCE(AVG(o.total_amount), 0)::float     AS avg_order_value,
            MAX(o.order_date)                           AS last_order_at
        FROM kirana_oltp.store_association a
        LEFT JOIN kirana_oltp.customer c
            ON c.association_id = a.association_id
        LEFT JOIN kirana_oltp.orders o
            ON o.customer_id = c.customer_id
           AND o.store_id = :sid
           AND o.order_date >= NOW() - INTERVAL '90 days'
        WHERE a.store_id = :sid AND a.is_active = TRUE
        GROUP BY a.association_id, a.name, a.area_type, a.estimated_households
        ORDER BY total_revenue DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("last_order_at"):
                d["last_order_at"] = d["last_order_at"].isoformat()
            result.append(d)
        return result

    def get_kpi_tier_config(self) -> dict[str, str]:
        """Returns {kpi_id: required_tier} for all configured KPIs."""
        sql = "SELECT kpi_id, required_tier FROM kirana_oltp.kpi_tier_config"
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return {r["kpi_id"]: r["required_tier"] for r in rows}

    def upsert_kpi_tier_config(self, configs: list[dict]) -> None:
        """Bulk upsert [{kpi_id, required_tier}]. Replaces all existing entries."""
        if not configs:
            return
        sql = """
        INSERT INTO kirana_oltp.kpi_tier_config (kpi_id, required_tier, updated_at)
        VALUES (:kpi_id, :required_tier, NOW())
        ON CONFLICT (kpi_id) DO UPDATE
            SET required_tier = EXCLUDED.required_tier,
                updated_at    = NOW()
        """
        with self._conn() as conn:
            conn.execute(text(sql), configs)
            conn.commit()
