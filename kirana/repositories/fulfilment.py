from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class FulfilmentRepositoryMixin:
    """Module M6 — Orders & Fulfilment: estimates/proforma, customer returns
    & exchanges, and order delivery status. (Purchase orders + return-to-vendor
    already exist in the inventory layer.)"""

    # ── Estimates / proforma ─────────────────────────────────────────────────
    def list_estimates(self, store_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT estimate_id, customer_id, customer_name, total, status,
                       valid_until, order_id, created_at
                FROM kirana_oltp.estimate WHERE store_id = :sid
                ORDER BY created_at DESC
            """), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def get_estimate(self, estimate_id: int, store_id: int) -> dict | None:
        with self._conn() as conn:
            head = conn.execute(text("""
                SELECT estimate_id, customer_id, customer_name, total, status,
                       valid_until, order_id, created_at
                FROM kirana_oltp.estimate WHERE estimate_id = :id AND store_id = :sid
            """), {"id": estimate_id, "sid": store_id}).mappings().first()
            if not head:
                return None
            items = conn.execute(text("""
                SELECT id, product_id, name, quantity, unit_price
                FROM kirana_oltp.estimate_item WHERE estimate_id = :id
            """), {"id": estimate_id}).mappings().all()
        out = dict(head)
        out["items"] = [dict(r) for r in items]
        return out

    def create_estimate(self, store_id: int, items: list[dict], *,
                        customer_id: int | None = None, customer_name: str | None = None,
                        valid_until: str | None = None) -> dict:
        total = sum(float(i.get("unit_price") or 0) * float(i.get("quantity") or 1) for i in items)
        with self._conn() as conn:
            eid = conn.execute(text("""
                INSERT INTO kirana_oltp.estimate (store_id, customer_id, customer_name, total, status, valid_until)
                VALUES (:sid, :cid, :cname, :total, 'draft', CAST(:vu AS DATE))
                RETURNING estimate_id
            """), {"sid": store_id, "cid": customer_id, "cname": customer_name,
                   "total": round(total, 2), "vu": valid_until}).scalar()
            for i in items:
                conn.execute(text("""
                    INSERT INTO kirana_oltp.estimate_item (estimate_id, product_id, name, quantity, unit_price)
                    VALUES (:eid, :pid, :name, :qty, :price)
                """), {"eid": eid, "pid": i.get("product_id"), "name": i.get("name") or "Item",
                       "qty": i.get("quantity") or 1, "price": i.get("unit_price") or 0})
            conn.commit()
        return self.get_estimate(int(eid), store_id)

    def set_estimate_status(self, estimate_id: int, store_id: int, status: str,
                            order_id: int | None = None) -> bool:
        with self._conn() as conn:
            n = conn.execute(text("""
                UPDATE kirana_oltp.estimate SET status = :st, order_id = COALESCE(:oid, order_id)
                WHERE estimate_id = :id AND store_id = :sid
            """), {"st": status, "oid": order_id, "id": estimate_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    # ── Customer returns / exchanges ─────────────────────────────────────────
    def list_sales_returns(self, store_id: int, days: int = 90) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT return_id, order_id, customer_id, reason, refund_amount,
                       is_exchange, notes, created_at
                FROM kirana_oltp.sales_return WHERE store_id = :sid
                  AND created_at >= NOW() - (:days || ' days')::interval
                ORDER BY created_at DESC
            """), {"sid": store_id, "days": days}).mappings().all()
        return [dict(r) for r in rows]

    def create_sales_return(self, store_id: int, *, order_id: int | None = None,
                           customer_id: int | None = None, reason: str | None = None,
                           refund_amount: float = 0, is_exchange: bool = False,
                           notes: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.sales_return
                    (store_id, order_id, customer_id, reason, refund_amount, is_exchange, notes)
                VALUES (:sid, :oid, :cid, :reason, :amt, :exch, :notes)
                RETURNING return_id, order_id, customer_id, reason, refund_amount, is_exchange, notes, created_at
            """), {"sid": store_id, "oid": order_id, "cid": customer_id, "reason": reason,
                   "amt": refund_amount, "exch": is_exchange, "notes": notes}).mappings().first()
            conn.commit()
        return dict(row)

    # ── Delivery ─────────────────────────────────────────────────────────────
    def set_delivery_status(self, order_id: int, store_id: int, status: str) -> bool:
        with self._conn() as conn:
            n = conn.execute(text("""
                UPDATE kirana_oltp.orders SET delivery_status = :st
                WHERE order_id = :oid AND store_id = :sid
            """), {"st": status, "oid": order_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0
