from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class TaxRepositoryMixin:
    """Foundation 3 — tax / GST.

    Resolution order for a line's GST rate: the product's own ``gst_rate`` →
    the best-matching store ``tax_rule`` (by category / HSN / price band) →
    0 (no tax). Retail prices are treated as GST-inclusive; callers extract the
    tax component for the bill breakup.
    """

    def resolve_gst_rate(self, store_id: int, product_id: int, price: float) -> float:
        with self._conn() as conn:
            own = conn.execute(
                text("SELECT gst_rate FROM kirana_oltp.product WHERE product_id = :pid"),
                {"pid": product_id},
            ).scalar()
            if own is not None:
                return float(own)
            rule = conn.execute(
                text("""
                SELECT gst_rate
                FROM kirana_oltp.tax_rule
                WHERE (store_id = :sid OR store_id IS NULL)
                  AND (category_id IS NULL
                       OR category_id = (SELECT category_id FROM kirana_oltp.product WHERE product_id = :pid))
                  AND (min_price IS NULL OR :price >= min_price)
                  AND (max_price IS NULL OR :price <= max_price)
                ORDER BY store_id NULLS LAST, category_id NULLS LAST, hsn_code NULLS LAST
                LIMIT 1
                """),
                {"sid": store_id, "pid": product_id, "price": price},
            ).scalar()
        return float(rule) if rule is not None else 0.0

    def set_product_tax(
        self, product_id: int, hsn_code: str | None, gst_rate: float | None
    ) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                UPDATE kirana_oltp.product
                SET hsn_code = :hsn, gst_rate = :rate
                WHERE product_id = :pid
                RETURNING product_id, hsn_code, gst_rate
                """),
                {"pid": product_id, "hsn": hsn_code, "rate": gst_rate},
            ).mappings().first()
            conn.commit()
        return dict(row) if row else {}

    def list_tax_rules(self, store_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                text("""
                SELECT rule_id, store_id, category_id, hsn_code,
                       min_price, max_price, gst_rate, created_at
                FROM kirana_oltp.tax_rule
                WHERE store_id = :sid OR store_id IS NULL
                ORDER BY store_id NULLS LAST, rule_id
                """),
                {"sid": store_id},
            ).mappings().all()
        return [dict(r) for r in rows]

    def create_tax_rule(
        self,
        store_id: int,
        gst_rate: float,
        category_id: int | None = None,
        hsn_code: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                INSERT INTO kirana_oltp.tax_rule
                    (store_id, category_id, hsn_code, min_price, max_price, gst_rate)
                VALUES (:sid, :cid, :hsn, :minp, :maxp, :rate)
                RETURNING rule_id, store_id, category_id, hsn_code,
                          min_price, max_price, gst_rate, created_at
                """),
                {
                    "sid": store_id,
                    "cid": category_id,
                    "hsn": hsn_code,
                    "minp": min_price,
                    "maxp": max_price,
                    "rate": gst_rate,
                },
            ).mappings().first()
            conn.commit()
        return dict(row)

    def delete_tax_rule(self, rule_id: int, store_id: int) -> bool:
        with self._conn() as conn:
            n = conn.execute(
                text(
                    "DELETE FROM kirana_oltp.tax_rule "
                    "WHERE rule_id = :rid AND store_id = :sid"
                ),
                {"rid": rule_id, "sid": store_id},
            ).rowcount
            conn.commit()
        return n > 0
