from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class Customer360RepositoryMixin:
    """Module M8 — Customer 360+: wishlist + prescription / style-size profiles.
    (Referral tracking already exists in the referral layer.)"""

    # ── Wishlist / saved cart ────────────────────────────────────────────────
    def list_wishlist(self, store_id: int, customer_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT w.id, w.product_id, w.note, w.created_at, p.name AS product_name
                FROM kirana_oltp.wishlist w
                LEFT JOIN kirana_oltp.product p ON w.product_id = p.product_id
                WHERE w.store_id = :sid AND w.customer_id = :cid
                ORDER BY w.created_at DESC
            """), {"sid": store_id, "cid": customer_id}).mappings().all()
        return [dict(r) for r in rows]

    def add_wishlist(self, store_id: int, customer_id: int,
                     product_id: int | None = None, note: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.wishlist (store_id, customer_id, product_id, note)
                VALUES (:sid, :cid, :pid, :note)
                RETURNING id, product_id, note, created_at
            """), {"sid": store_id, "cid": customer_id, "pid": product_id, "note": note}).mappings().first()
            conn.commit()
        return dict(row)

    def remove_wishlist(self, item_id: int, store_id: int) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "DELETE FROM kirana_oltp.wishlist WHERE id = :id AND store_id = :sid"),
                {"id": item_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    # ── Profiles (prescription / style / size) ───────────────────────────────
    def get_customer_profile(self, store_id: int, customer_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                SELECT customer_id, name, phone, prescription, style_profile, size_profile,
                       prescription_date, prescription_valid_months
                FROM kirana_oltp.customer WHERE customer_id = :cid AND store_id = :sid
            """), {"cid": customer_id, "sid": store_id}).mappings().first()
        return dict(row) if row else {}

    def update_customer_profile(self, store_id: int, customer_id: int, **fields) -> dict:
        allowed = {"prescription", "style_profile", "size_profile",
                   "prescription_date", "prescription_valid_months"}
        sets, params = [], {"cid": customer_id, "sid": store_id}
        for k, v in fields.items():
            # None = field omitted (skip); '' is allowed to clear a value.
            if k in allowed and v is not None:
                sets.append(f"{k} = :{k}")
                params[k] = v
        if not sets:
            return self.get_customer_profile(store_id, customer_id)
        with self._conn() as conn:
            conn.execute(text(
                "UPDATE kirana_oltp.customer SET " + ", ".join(sets) +
                " WHERE customer_id = :cid AND store_id = :sid"), params)
            conn.commit()
        return self.get_customer_profile(store_id, customer_id)
