from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class JobCardsRepositoryMixin:
    """Module M9 — Job cards: alteration (apparel), repair (mobile/optical),
    and pre-order / custom orders (bakery). One model, a job_type discriminator."""

    def list_job_cards(self, store_id: int, status: str | None = None,
                       job_type: str | None = None) -> list[dict]:
        sql = ("SELECT job_id, customer_id, customer_name, customer_phone, job_type, "
               "item_desc, details, charge, status, promised_date, created_at "
               "FROM kirana_oltp.job_card WHERE store_id = :sid")
        params: dict = {"sid": store_id}
        if status:
            sql += " AND status = :st"; params["st"] = status
        if job_type:
            sql += " AND job_type = :jt"; params["jt"] = job_type
        sql += " ORDER BY CASE WHEN status IN ('received','in_progress','ready') THEN 0 ELSE 1 END, promised_date NULLS LAST, job_id DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]

    def create_job_card(self, store_id: int, *, job_type: str = "repair",
                        customer_id: int | None = None, customer_name: str | None = None,
                        customer_phone: str | None = None, item_desc: str | None = None,
                        details: str | None = None, charge: float | None = None,
                        promised_date: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.job_card
                    (store_id, customer_id, customer_name, customer_phone, job_type,
                     item_desc, details, charge, promised_date)
                VALUES (:sid, :cid, :cname, :cphone, :jt, :item, :det, :charge, CAST(:pd AS DATE))
                RETURNING job_id, customer_id, customer_name, customer_phone, job_type,
                          item_desc, details, charge, status, promised_date, created_at
            """), {"sid": store_id, "cid": customer_id, "cname": customer_name,
                   "cphone": customer_phone, "jt": job_type, "item": item_desc,
                   "det": details, "charge": charge, "pd": promised_date}).mappings().first()
            conn.commit()
        return dict(row)

    def update_job_card(self, job_id: int, store_id: int, **fields) -> dict | None:
        """Patch editable job-card fields (item_desc/details/charge/promised_date/
        customer_*/status). Ignores None values. promised_date is cast to DATE."""
        allowed = {"item_desc", "details", "charge", "promised_date", "status",
                   "customer_id", "customer_name", "customer_phone"}
        sets, params = [], {"id": job_id, "sid": store_id}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "promised_date":
                sets.append("promised_date = CAST(:promised_date AS DATE)")
                params["promised_date"] = v
            else:
                sets.append(f"{k} = :{k}")
                params[k] = v
        if not sets:
            return None
        sql = ("UPDATE kirana_oltp.job_card SET " + ", ".join(sets) +
               " WHERE job_id = :id AND store_id = :sid "
               "RETURNING job_id, customer_id, customer_name, customer_phone, "
               "job_type, item_desc, details, charge, status, promised_date, created_at")
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        return dict(row) if row else None

    def set_job_status(self, job_id: int, store_id: int, status: str) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "UPDATE kirana_oltp.job_card SET status = :st WHERE job_id = :id AND store_id = :sid"),
                {"st": status, "id": job_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    def link_job_to_order(self, job_id: int, store_id: int, order_id: int) -> bool:
        """POS deep-link: attach a finished job card to the sale that billed it
        and mark it delivered."""
        with self._conn() as conn:
            n = conn.execute(text(
                "UPDATE kirana_oltp.job_card SET order_id = :oid, status = 'delivered' "
                "WHERE job_id = :id AND store_id = :sid"),
                {"oid": order_id, "id": job_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0
