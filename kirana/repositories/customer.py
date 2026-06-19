from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class CustomerRepositoryMixin:
    def list_customers_with_segments(self, store_id: int) -> list[dict]:
        sql = """
        SELECT
            c.customer_id,
            c.name,
            c.phone,
            c.email,
            c.household_size,
            c.store_id,
            c.created_at,
            c.association_id,
            COALESCE(ord.total_orders, 0)   AS total_orders,
            COALESCE(ord.total_spent, 0)    AS total_spent,
            ord.last_order_date,
            COALESCE(ord.orders_30d, 0)     AS orders_30d,
            COALESCE(ord.orders_90d, 0)     AS orders_90d,
            COALESCE(kh.balance, 0)         AS balance
        FROM kirana_oltp.customer c
        LEFT JOIN (
            SELECT
                o.customer_id,
                COUNT(*)                                                                     AS total_orders,
                SUM(o.total_amount)                                                          AS total_spent,
                MAX(o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')            AS last_order_date,
                COUNT(CASE WHEN o.order_date >= NOW() - INTERVAL '30 days' THEN 1 END)      AS orders_30d,
                COUNT(CASE WHEN o.order_date >= NOW() - INTERVAL '90 days' THEN 1 END)      AS orders_90d
            FROM kirana_oltp.orders o
            WHERE o.store_id = :sid AND o.customer_id IS NOT NULL
            GROUP BY o.customer_id
        ) ord ON ord.customer_id = c.customer_id
        LEFT JOIN (
            SELECT customer_id, SUM(amount) AS balance
            FROM kirana_oltp.khata
            WHERE store_id = :sid
            GROUP BY customer_id
        ) kh ON kh.customer_id = c.customer_id
        WHERE c.store_id = :sid AND COALESCE(c.is_deleted, FALSE) = FALSE
        ORDER BY c.name ASC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("last_order_date"):
                d["last_order_date"] = d["last_order_date"].isoformat()
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result

    def get_customer_purchases(
        self, store_id: int, customer_id: int, limit: int = 50
    ) -> list[dict]:
        """Recall what a customer bought — for resolving return/exchange disputes."""
        sql = """
            SELECT o.order_id, o.order_date::text AS order_date,
                   oi.product_id, p.name AS product_name,
                   oi.quantity, oi.unit_price::float AS unit_price
            FROM kirana_oltp.orders o
            JOIN kirana_oltp.order_item oi ON oi.order_id = o.order_id
            JOIN kirana_oltp.product p ON p.product_id = oi.product_id
            WHERE o.store_id = :sid AND o.customer_id = :cid
            ORDER BY o.order_date DESC
            LIMIT :lim
        """
        with self._conn() as conn:
            rows = (
                conn.execute(
                    text(sql), {"sid": store_id, "cid": customer_id, "lim": limit}
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def get_customer_price_memory(self, store_id: int, customer_id: int) -> list[dict]:
        """Per-customer price memory. For each product, the customer's effective
        personal price = an explicitly pinned price if set, else the most recent
        price they actually paid. Returned only where it differs from catalog
        (pinned prices are always returned). Powers POS customer-specific pricing.

        Each row carries `source` = 'pinned' | 'last_paid' so the UI can label it.
        """
        sql = """
        WITH active_price AS (
            SELECT DISTINCT ON (product_id) product_id, price
            FROM kirana_oltp.pricing
            WHERE store_id = :sid AND valid_from <= now()
              AND (valid_to IS NULL OR valid_to >= now())
            ORDER BY product_id, valid_from DESC
        ),
        last_paid AS (
            SELECT DISTINCT ON (oi.product_id)
                   oi.product_id,
                   oi.unit_price::float AS unit_price,
                   o.order_date
            FROM kirana_oltp.order_item oi
            JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
            WHERE o.store_id = :sid AND o.customer_id = :cid
            ORDER BY oi.product_id, o.order_date DESC
        ),
        pinned AS (
            SELECT product_id, price::float AS price
            FROM kirana_oltp.customer_product_price
            WHERE store_id = :sid AND customer_id = :cid
        ),
        candidates AS (
            SELECT product_id FROM pinned
            UNION
            SELECT product_id FROM last_paid
        )
        SELECT c.product_id,
               p.name AS product_name,
               p.unit,
               COALESCE(pin.price, lp.unit_price) AS price,
               CASE WHEN pin.price IS NOT NULL THEN 'pinned' ELSE 'last_paid' END AS source,
               lp.unit_price AS last_paid_price,
               lp.order_date::text AS last_paid_date,
               ap.price::float AS catalog_price
        FROM candidates c
        JOIN kirana_oltp.product p ON p.product_id = c.product_id
        LEFT JOIN pinned     pin ON pin.product_id = c.product_id
        LEFT JOIN last_paid  lp  ON lp.product_id  = c.product_id
        LEFT JOIN active_price ap ON ap.product_id = c.product_id
        WHERE COALESCE(pin.price, lp.unit_price) IS NOT NULL
          AND COALESCE(pin.price, lp.unit_price) > 0
          AND (
                pin.price IS NOT NULL  -- always surface explicit pins
                OR ap.price IS NULL
                OR abs(lp.unit_price - ap.price) > 0.01
              )
        ORDER BY p.name ASC
        """
        with self._conn() as conn:
            rows = (
                conn.execute(text(sql), {"sid": store_id, "cid": customer_id})
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def set_customer_product_price(
        self, store_id: int, customer_id: int, product_id: int, price: float | None
    ) -> dict:
        """Pin (upsert) or, when price is None, remove a customer-specific price
        for a product."""
        with self._conn() as conn:
            if price is None:
                conn.execute(
                    text("""
                    DELETE FROM kirana_oltp.customer_product_price
                    WHERE store_id = :sid AND customer_id = :cid AND product_id = :pid
                """),
                    {"sid": store_id, "cid": customer_id, "pid": product_id},
                )
                conn.commit()
                return {"product_id": product_id, "price": None, "removed": True}
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.customer_product_price
                    (store_id, customer_id, product_id, price, updated_at)
                VALUES (:sid, :cid, :pid, :price, now())
                ON CONFLICT (store_id, customer_id, product_id)
                DO UPDATE SET price = EXCLUDED.price, updated_at = now()
            """),
                {
                    "sid": store_id,
                    "cid": customer_id,
                    "pid": product_id,
                    "price": price,
                },
            )
            conn.commit()
        return {"product_id": product_id, "price": price, "removed": False}

    def sync_customers(self, store_id: int, contacts: list[dict]) -> int:
        if not contacts:
            return 0
        insert_sql = """
        INSERT INTO kirana_oltp.customer (name, phone, store_id)
        SELECT :n, :p, :sid
        WHERE NOT EXISTS (
            SELECT 1 FROM kirana_oltp.customer
            WHERE store_id = :sid AND phone = :p
              AND COALESCE(is_deleted, FALSE) = FALSE
        )
        """
        params = [{"n": c["name"], "p": c["phone"], "sid": store_id} for c in contacts]
        with self._conn() as conn:
            conn.execute(text(insert_sql), params)
            conn.commit()
        return len(params)
