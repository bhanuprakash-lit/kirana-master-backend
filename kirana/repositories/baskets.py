from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class BasketsRepositoryMixin:
    _BASKET_COLS = """
        b.basket_id, b.name, b.description, b.price,
        b.tier, b.gross_total, b.discount_pct,
        b.valid_from::text, b.valid_to::text, b.is_active,
        b.archived_at::text, b.last_alerted_at::text, b.created_at::text,
        (b.last_alerted_at IS NOT NULL AND DATE(
            b.last_alerted_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata'
        ) = DATE(NOW() AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')) AS alerted_today,
        COALESCE(
          json_agg(json_build_object(
            'id', bi.id, 'product_id', bi.product_id,
            'product_name', bi.product_name, 'qty', bi.qty
          )) FILTER (WHERE bi.id IS NOT NULL), '[]'
        ) AS items
    """

    def get_baskets(self, store_id: int, include_archived: bool = False) -> list[dict]:
        archived_filter = "" if include_archived else "AND b.archived_at IS NULL"
        sql = f"""
            SELECT {self._BASKET_COLS}
            FROM kirana_oltp.basket b
            LEFT JOIN kirana_oltp.basket_item bi ON bi.basket_id = b.basket_id
            WHERE b.store_id = :sid AND b.is_active = TRUE {archived_filter}
            GROUP BY b.basket_id
            ORDER BY b.archived_at IS NOT NULL, b.created_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def get_basket(self, store_id: int, basket_id: int) -> dict | None:
        sql = f"""
            SELECT {self._BASKET_COLS}
            FROM kirana_oltp.basket b
            LEFT JOIN kirana_oltp.basket_item bi ON bi.basket_id = b.basket_id
            WHERE b.store_id = :sid AND b.basket_id = :bid AND b.is_active = TRUE
            GROUP BY b.basket_id
        """
        with self._conn() as conn:
            row = (
                conn.execute(text(sql), {"sid": store_id, "bid": basket_id})
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def get_tier_config(self, store_id: int) -> dict:
        """Return the store's basket tier config, or the system defaults."""
        from kirana.basket_tiers import DEFAULT_TIER_CONFIG, normalize_tier_config

        with self._conn() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT config FROM kirana_oltp.basket_tier_config WHERE store_id = :sid"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .first()
            )
        if not row:
            return DEFAULT_TIER_CONFIG
        return normalize_tier_config(row["config"])

    def set_tier_config(self, store_id: int, config: dict) -> dict:
        from kirana.basket_tiers import normalize_tier_config
        import json

        clean = normalize_tier_config(config)
        with self._conn() as conn:
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.basket_tier_config(store_id, config, updated_at)
                VALUES(:sid, CAST(:cfg AS JSONB), NOW())
                ON CONFLICT (store_id) DO UPDATE
                  SET config = CAST(:cfg AS JSONB), updated_at = NOW()
            """),
                {"sid": store_id, "cfg": json.dumps(clean)},
            )
            conn.commit()
        return clean

    def _gross_total_for_items(self, conn, store_id: int, items: list[dict]) -> float:
        """Sum qty × current selling price for the given items (server is the
        source of truth for price — the client value is ignored)."""
        gross = 0.0
        for item in items:
            pid = item["product_id"]
            qty = float(item.get("qty", 1) or 1)
            price_row = (
                conn.execute(
                    text("""
                SELECT price FROM kirana_oltp.pricing
                WHERE product_id = :pid AND store_id = :sid AND valid_from <= NOW()
                ORDER BY valid_from DESC LIMIT 1
            """),
                    {"pid": pid, "sid": store_id},
                )
                .mappings()
                .first()
            )
            unit = (
                float(price_row["price"])
                if price_row and price_row["price"] is not None
                else 0.0
            )
            gross += unit * qty
        return gross

    def create_basket(self, store_id: int, data: dict) -> dict:
        from kirana.basket_tiers import price_for

        config = self.get_tier_config(store_id)
        with self._conn() as conn:
            items = data.get("items", [])
            gross = self._gross_total_for_items(conn, store_id, items)
            pricing = price_for(gross, config)
            row = (
                conn.execute(
                    text("""
                INSERT INTO kirana_oltp.basket(
                    store_id, name, description, price, tier, gross_total, discount_pct, valid_from, valid_to)
                VALUES(:sid, :name, :desc, :price, :tier, :gross, :disc, :vf, :vt)
                RETURNING basket_id
            """),
                    {
                        "sid": store_id,
                        "name": data["name"],
                        "desc": data.get("description"),
                        "price": pricing["price"],
                        "tier": pricing["tier"],
                        "gross": pricing["gross_total"],
                        "disc": pricing["discount_pct"],
                        "vf": data.get("valid_from"),
                        "vt": data.get("valid_to"),
                    },
                )
                .mappings()
                .first()
            )
            basket_id = row["basket_id"]
            if items:
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.basket_item(basket_id, product_id, product_name, qty)
                    VALUES(:bid, :pid, :pname, :qty)
                """),
                    [
                        {
                            "bid": basket_id,
                            "pid": item["product_id"],
                            "pname": item.get("product_name"),
                            "qty": item.get("qty", 1),
                        }
                        for item in items
                    ],
                )
            conn.commit()
        return self.get_basket(store_id, basket_id) or {
            "basket_id": basket_id,
            **pricing,
        }

    def update_basket(self, store_id: int, basket_id: int, data: dict) -> dict | None:
        """Replace a basket's fields + items, recomputing tier from current config."""
        from kirana.basket_tiers import price_for

        config = self.get_tier_config(store_id)
        with self._conn() as conn:
            owns = conn.execute(
                text(
                    "SELECT 1 FROM kirana_oltp.basket WHERE basket_id = :bid AND store_id = :sid AND is_active = TRUE"
                ),
                {"bid": basket_id, "sid": store_id},
            ).first()
            if not owns:
                return None
            items = data.get("items", [])
            gross = self._gross_total_for_items(conn, store_id, items)
            pricing = price_for(gross, config)
            conn.execute(
                text("""
                UPDATE kirana_oltp.basket SET
                    name = :name, description = :desc, price = :price, tier = :tier,
                    gross_total = :gross, discount_pct = :disc, valid_from = :vf, valid_to = :vt
                WHERE basket_id = :bid AND store_id = :sid
            """),
                {
                    "bid": basket_id,
                    "sid": store_id,
                    "name": data["name"],
                    "desc": data.get("description"),
                    "price": pricing["price"],
                    "tier": pricing["tier"],
                    "gross": pricing["gross_total"],
                    "disc": pricing["discount_pct"],
                    "vf": data.get("valid_from"),
                    "vt": data.get("valid_to"),
                },
            )
            conn.execute(
                text("DELETE FROM kirana_oltp.basket_item WHERE basket_id = :bid"),
                {"bid": basket_id},
            )
            if items:
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.basket_item(basket_id, product_id, product_name, qty)
                    VALUES(:bid, :pid, :pname, :qty)
                """),
                    [
                        {
                            "bid": basket_id,
                            "pid": item["product_id"],
                            "pname": item.get("product_name"),
                            "qty": item.get("qty", 1),
                        }
                        for item in items
                    ],
                )
            conn.commit()
        return self.get_basket(store_id, basket_id)

    def retier_baskets(self, store_id: int) -> int:
        """Recompute tier/discount/price for all active, non-archived baskets
        under the store's current config. Returns the count updated."""
        from kirana.basket_tiers import price_for

        config = self.get_tier_config(store_id)
        with self._conn() as conn:
            ids = [
                r["basket_id"]
                for r in conn.execute(
                    text(
                        "SELECT basket_id FROM kirana_oltp.basket "
                        "WHERE store_id = :sid AND is_active = TRUE AND archived_at IS NULL"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .all()
            ]
            for bid in ids:
                items = (
                    conn.execute(
                        text(
                            "SELECT product_id, qty FROM kirana_oltp.basket_item WHERE basket_id = :bid"
                        ),
                        {"bid": bid},
                    )
                    .mappings()
                    .all()
                )
                gross = self._gross_total_for_items(
                    conn, store_id, [dict(i) for i in items]
                )
                pricing = price_for(gross, config)
                conn.execute(
                    text("""
                    UPDATE kirana_oltp.basket SET price = :price, tier = :tier,
                        gross_total = :gross, discount_pct = :disc
                    WHERE basket_id = :bid
                """),
                    {
                        "bid": bid,
                        "price": pricing["price"],
                        "tier": pricing["tier"],
                        "gross": pricing["gross_total"],
                        "disc": pricing["discount_pct"],
                    },
                )
            conn.commit()
        return len(ids)

    def count_active_baskets(self, store_id: int) -> int:
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT COUNT(*) AS n FROM kirana_oltp.basket "
                        "WHERE store_id = :sid AND is_active = TRUE AND archived_at IS NULL"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .first()
            )
        return int(row["n"]) if row else 0

    def set_basket_archived(
        self, store_id: int, basket_id: int, archived: bool
    ) -> bool:
        with self._conn() as conn:
            res = conn.execute(
                text(
                    "UPDATE kirana_oltp.basket SET archived_at = "
                    + ("NOW()" if archived else "NULL")
                    + " WHERE basket_id = :bid AND store_id = :sid AND is_active = TRUE"
                ),
                {"bid": basket_id, "sid": store_id},
            )
            conn.commit()
            return res.rowcount > 0

    def basket_alerted_today(self, store_id: int, basket_id: int) -> bool:
        with self._conn() as conn:
            row = (
                conn.execute(
                    text("""
                SELECT (last_alerted_at IS NOT NULL AND DATE(
                    last_alerted_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata'
                ) = DATE(NOW() AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')) AS yes
                FROM kirana_oltp.basket WHERE basket_id = :bid AND store_id = :sid
            """),
                    {"bid": basket_id, "sid": store_id},
                )
                .mappings()
                .first()
            )
        return bool(row and row["yes"])

    def mark_basket_alerted(self, store_id: int, basket_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                text(
                    "UPDATE kirana_oltp.basket SET last_alerted_at = NOW() "
                    "WHERE basket_id = :bid AND store_id = :sid"
                ),
                {"bid": basket_id, "sid": store_id},
            )
            conn.commit()

    def delete_basket(self, store_id: int, basket_id: int) -> bool:
        with self._conn() as conn:
            conn.execute(
                text(
                    "UPDATE kirana_oltp.basket SET is_active = FALSE WHERE basket_id = :bid AND store_id = :sid"
                ),
                {"bid": basket_id, "sid": store_id},
            )
            conn.commit()
        return True
