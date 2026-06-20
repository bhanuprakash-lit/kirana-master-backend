from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class WarrantyRepositoryMixin:
    """Module M7 — serial/IMEI register + warranty-claim tracking (electronics)."""

    # ── Serials ───────────────────────────────────────────────────────────────
    def list_serials(self, store_id: int, product_id: int | None = None,
                     status: str | None = None) -> list[dict]:
        sql = ("SELECT serial_id, product_id, variant_id, serial_no, status, "
               "order_id, customer_id, warranty_until, sold_at "
               "FROM kirana_oltp.product_serial WHERE store_id = :sid")
        params: dict = {"sid": store_id}
        if product_id is not None:
            sql += " AND product_id = :pid"; params["pid"] = product_id
        if status:
            sql += " AND status = :st"; params["st"] = status
        sql += " ORDER BY serial_id DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]

    def add_serial(self, store_id: int, product_id: int, serial_no: str,
                   variant_id: int | None = None, warranty_until: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.product_serial
                    (store_id, product_id, variant_id, serial_no, warranty_until)
                VALUES (:sid, :pid, :vid, :sn, CAST(:wu AS DATE))
                ON CONFLICT (store_id, serial_no) DO UPDATE SET product_id = EXCLUDED.product_id
                RETURNING serial_id, product_id, variant_id, serial_no, status, warranty_until
            """), {"sid": store_id, "pid": product_id, "vid": variant_id,
                   "sn": serial_no, "wu": warranty_until}).mappings().first()
            conn.commit()
        return dict(row)

    def mark_serial_sold(self, store_id: int, serial_no: str, order_id: int | None,
                         customer_id: int | None) -> bool:
        with self._conn() as conn:
            n = conn.execute(text("""
                UPDATE kirana_oltp.product_serial
                SET status = 'sold', order_id = :oid, customer_id = :cid, sold_at = NOW()
                WHERE store_id = :sid AND serial_no = :sn
            """), {"oid": order_id, "cid": customer_id, "sid": store_id, "sn": serial_no}).rowcount
            conn.commit()
        return n > 0

    # ── Warranty claims ─────────────────────────────────────────────────────
    def list_claims(self, store_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT w.claim_id, w.product_id, w.serial_id, w.customer_id, w.issue,
                       w.status, w.claim_date, ps.serial_no, p.name AS product_name
                FROM kirana_oltp.warranty_claim w
                LEFT JOIN kirana_oltp.product_serial ps ON w.serial_id = ps.serial_id
                LEFT JOIN kirana_oltp.product p ON w.product_id = p.product_id
                WHERE w.store_id = :sid ORDER BY w.created_at DESC
            """), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def create_claim(self, store_id: int, *, product_id: int | None = None,
                     serial_id: int | None = None, customer_id: int | None = None,
                     issue: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.warranty_claim (store_id, product_id, serial_id, customer_id, issue)
                VALUES (:sid, :pid, :ser, :cid, :issue)
                RETURNING claim_id, product_id, serial_id, customer_id, issue, status, claim_date
            """), {"sid": store_id, "pid": product_id, "ser": serial_id,
                   "cid": customer_id, "issue": issue}).mappings().first()
            conn.commit()
        return dict(row)

    def set_claim_status(self, claim_id: int, store_id: int, status: str) -> bool:
        with self._conn() as conn:
            n = conn.execute(text("""
                UPDATE kirana_oltp.warranty_claim
                SET status = :st, resolved_at = CASE WHEN :st <> 'open' THEN NOW() ELSE resolved_at END
                WHERE claim_id = :id AND store_id = :sid
            """), {"st": status, "id": claim_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    def warranty_claim_rate(self, store_id: int, days: int = 90) -> dict:
        """Claims vs serials sold over the window (drives the F4 KPI)."""
        with self._conn() as conn:
            sold = conn.execute(text("""
                SELECT COUNT(*) FROM kirana_oltp.product_serial
                WHERE store_id = :sid AND status = 'sold'
                  AND sold_at >= NOW() - (:days || ' days')::interval
            """), {"sid": store_id, "days": days}).scalar() or 0
            claims = conn.execute(text("""
                SELECT COUNT(*) FROM kirana_oltp.warranty_claim
                WHERE store_id = :sid
                  AND created_at >= NOW() - (:days || ' days')::interval
            """), {"sid": store_id, "days": days}).scalar() or 0
        sold = int(sold); claims = int(claims)
        rate = round(claims / sold * 100, 2) if sold else 0.0
        return {"units_sold": sold, "claims": claims, "claim_rate_pct": rate}
