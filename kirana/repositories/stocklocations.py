from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class StockLocationsRepositoryMixin:
    """Module M3 — multi-location / multi-rack stock. A SKU can sit in several
    racks/bins; each row is the quantity at one rack."""

    def list_locations(self, store_id: int, product_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT id, product_id, variant_id, rack, quantity
                FROM kirana_oltp.inventory_location
                WHERE store_id = :sid AND product_id = :pid
                ORDER BY rack
            """), {"sid": store_id, "pid": product_id}).mappings().all()
        return [dict(r) for r in rows]

    def product_exists(self, product_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute(text(
                "SELECT 1 FROM kirana_oltp.product WHERE product_id = :pid"),
                {"pid": product_id}).first() is not None

    def upsert_location(self, store_id: int, product_id: int, rack: str,
                        quantity: float, variant_id: int | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.inventory_location
                    (store_id, product_id, variant_id, rack, quantity)
                VALUES (:sid, :pid, :vid, :rack, :qty)
                ON CONFLICT (store_id, product_id, variant_id, rack)
                DO UPDATE SET quantity = EXCLUDED.quantity
                RETURNING id, product_id, variant_id, rack, quantity
            """), {"sid": store_id, "pid": product_id, "vid": variant_id,
                   "rack": rack, "qty": quantity}).mappings().first()
            conn.commit()
        return dict(row)

    def delete_location(self, location_id: int, store_id: int) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "DELETE FROM kirana_oltp.inventory_location WHERE id = :id AND store_id = :sid"),
                {"id": location_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    def find_by_rack(self, store_id: int, rack: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT il.id, il.product_id, p.name AS product_name, il.variant_id,
                       il.rack, il.quantity
                FROM kirana_oltp.inventory_location il
                JOIN kirana_oltp.product p ON p.product_id = il.product_id
                WHERE il.store_id = :sid AND il.rack ILIKE :rack
                ORDER BY il.rack, p.name
            """), {"sid": store_id, "rack": f"%{rack}%"}).mappings().all()
        return [dict(r) for r in rows]

    def list_all_locations(self, store_id: int) -> list[dict]:
        """Every placement in the store (id + product name), for the rack-browsing
        view. The app groups these by rack to show 'what's in each rack'."""
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT il.id, il.product_id, p.name AS product_name, il.variant_id,
                       il.rack, il.quantity
                FROM kirana_oltp.inventory_location il
                JOIN kirana_oltp.product p ON p.product_id = il.product_id
                WHERE il.store_id = :sid
                ORDER BY il.rack, p.name
            """), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]
