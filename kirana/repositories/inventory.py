from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class InventoryRepositoryMixin:
    def upsert_inventory_snapshot(
        self, store_id: int, snapshot_date: str, items: list[dict]
    ) -> int:
        sql = """
        INSERT INTO kirana_oltp.inventory_snapshots
            (snapshot_date, store_id, product_id,
             stock_on_hand, units_sold, stock, revenue, profit, price, promo_flag)
        VALUES
            (:d, :sid, :skuid,
             :soh, :us, :st, :rev, :prof, :price, :pf)
        ON CONFLICT (snapshot_date, store_id, product_id)
        DO UPDATE SET
            stock_on_hand = EXCLUDED.stock_on_hand,
            units_sold    = EXCLUDED.units_sold,
            stock         = EXCLUDED.stock,
            revenue       = EXCLUDED.revenue,
            profit        = EXCLUDED.profit,
            price         = EXCLUDED.price,
            promo_flag    = EXCLUDED.promo_flag,
            upserted_at   = NOW()
        """
        if not items:
            return 0
        params = [
            {
                "d": snapshot_date,
                "sid": store_id,
                "skuid": item.get("sku_id"),
                # stock_on_hand is the authoritative live quantity — fall back to
                # the "stock" key (used by the engine snapshot query) if absent.
                "soh": item.get("stock_on_hand", item.get("stock")),
                "us": item.get("units_sold"),
                "st": item.get("stock"),
                "rev": item.get("revenue"),
                "prof": item.get("profit"),
                "price": item.get("price"),
                "pf": item.get("promo_flag"),
            }
            for item in items
        ]
        with self._conn() as conn:
            conn.execute(text(sql), params)
            conn.commit()
        return len(params)

    def get_today_items_sold(self, store_id: int) -> int:
        """Total units sold today (IST) for the given store."""
        sql = """
        SELECT COALESCE(SUM(oi.quantity), 0) AS items_sold
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid
          AND DATE(o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')
              = CURRENT_DATE AT TIME ZONE 'Asia/Kolkata'
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).first()
        return int(row[0]) if row and row[0] else 0

    def get_near_expiry_batches(self, store_id: int, days: int = 7) -> list[dict]:
        """Active batches expiring within `days`, with value-at-risk and a
        suggested clearance markdown. Drives the Expiry Loss Prevention screen."""
        sql = """
            SELECT ib.batch_id, ib.product_id, p.name AS product_name, p.unit,
                   ib.batch_no, ib.expiry_date::text AS expiry_date,
                   (ib.expiry_date - CURRENT_DATE) AS days_left,
                   ib.qty_in_stock, COALESCE(ib.markdown_pct, 0)::float AS markdown_pct,
                   COALESCE(ib.wasted_units, 0) AS wasted_units,
                   COALESCE(ib.recovered_units, 0) AS recovered_units,
                   COALESCE(pr.price, 0)::float AS price,
                   COALESCE(ps.cost_price, 0)::float AS cost_price
            FROM kirana_oltp.inventory_batch ib
            JOIN kirana_oltp.product p ON p.product_id = ib.product_id
            LEFT JOIN LATERAL (
                SELECT price FROM kirana_oltp.pricing
                WHERE product_id = ib.product_id AND store_id = ib.store_id
                  AND (valid_to IS NULL OR valid_to >= now())
                ORDER BY valid_from DESC LIMIT 1
            ) pr ON TRUE
            LEFT JOIN LATERAL (
                SELECT cost_price FROM kirana_oltp.product_supplier
                WHERE product_id = ib.product_id LIMIT 1
            ) ps ON TRUE
            WHERE ib.store_id = :sid
              AND ib.qty_in_stock > 0
              AND ib.expiry_date <= CURRENT_DATE + make_interval(days => :days)
            ORDER BY ib.expiry_date ASC
        """
        with self._conn() as conn:
            rows = (
                conn.execute(text(sql), {"sid": store_id, "days": days})
                .mappings()
                .all()
            )
        out = []
        for r in rows:
            d = dict(r)
            days_left = d.get("days_left")
            d["suggested_markdown_pct"] = self._suggested_markdown(days_left)
            d["value_at_risk"] = round(
                float(d["qty_in_stock"]) * float(d["cost_price"] or 0), 2
            )
            price = float(d["price"] or 0)
            d["marked_down_price"] = round(
                price * (1 - float(d["markdown_pct"]) / 100.0), 2
            )
            out.append(d)
        return out

    def set_batch_markdown(
        self, store_id: int, batch_id: int, markdown_pct: float
    ) -> dict:
        markdown_pct = max(0.0, min(float(markdown_pct), 90.0))  # clamp 0–90%
        with self._conn() as conn:
            row = (
                conn.execute(
                    text("""
                UPDATE kirana_oltp.inventory_batch
                SET markdown_pct = :pct
                WHERE batch_id = :bid AND store_id = :sid
                RETURNING batch_id, product_id, markdown_pct, qty_in_stock
            """),
                    {"pct": markdown_pct, "bid": batch_id, "sid": store_id},
                )
                .mappings()
                .first()
            )
            if not row:
                raise ValueError("Batch not found")
            conn.commit()
        return dict(row)

    def record_batch_waste(self, store_id: int, batch_id: int, units: int) -> dict:
        """Write off spoiled units: reduce the batch and the store inventory,
        and track wasted_units for the perishable-waste KPI."""
        units = max(0, int(units))
        with self._conn() as conn:
            row = (
                conn.execute(
                    text("""
                UPDATE kirana_oltp.inventory_batch
                SET qty_in_stock = GREATEST(qty_in_stock - :u, 0),
                    wasted_units = COALESCE(wasted_units, 0) + :u
                WHERE batch_id = :bid AND store_id = :sid
                RETURNING batch_id, product_id, qty_in_stock, wasted_units
            """),
                    {"u": units, "bid": batch_id, "sid": store_id},
                )
                .mappings()
                .first()
            )
            if not row:
                raise ValueError("Batch not found")
            conn.execute(
                text("""
                UPDATE kirana_oltp.inventory
                SET quantity = GREATEST(quantity - :u, 0)
                WHERE product_id = :pid AND store_id = :sid
            """),
                {"u": units, "pid": row["product_id"], "sid": store_id},
            )
            conn.commit()
        return dict(row)

    def get_reorder_suggestions(
        self, store_id: int, cover_days: int = 14, lookback_days: int = 30
    ) -> list[dict]:
        """Products running low relative to their sales velocity, with a suggested
        reorder quantity and the cheapest known supplier.

        Suggested qty targets `cover_days + supplier lead time` of cover:
            qty = ceil(avg_daily_sales * (cover_days + lead_time) - current_stock)

        NOTE: avg_daily is a simple last-`lookback_days` velocity. A future ML
        hook can replace the `sales` CTE / avg_daily with demand-forecast output
        without changing the response shape.
        """
        sql = """
            WITH sales AS (
                SELECT oi.product_id,
                       SUM(oi.quantity)::float / :lookback AS avg_daily
                FROM kirana_oltp.order_item oi
                JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
                WHERE o.store_id = :sid
                  AND o.order_date >= now() - make_interval(days => :lookback)
                  AND COALESCE(o.order_status, 'completed') <> 'cancelled'
                GROUP BY oi.product_id
            )
            SELECT p.product_id, p.name AS product_name, p.unit,
                   COALESCE(inv.quantity, 0) AS stock,
                   s.avg_daily,
                   ps.supplier_id, sup.name AS supplier_name,
                   COALESCE(ps.cost_price, 0)::float AS cost_price,
                   COALESCE(ps.lead_time_days, 0) AS lead_time_days
            FROM kirana_oltp.inventory inv
            JOIN kirana_oltp.product p ON p.product_id = inv.product_id
            JOIN sales s ON s.product_id = inv.product_id
            LEFT JOIN LATERAL (
                SELECT supplier_id, cost_price, lead_time_days
                FROM kirana_oltp.product_supplier
                WHERE product_id = inv.product_id
                ORDER BY cost_price ASC NULLS LAST
                LIMIT 1
            ) ps ON TRUE
            LEFT JOIN kirana_oltp.supplier sup ON sup.supplier_id = ps.supplier_id
            WHERE inv.store_id = :sid
              AND s.avg_daily > 0
              AND COALESCE(inv.quantity, 0)
                  < s.avg_daily * (:cover + COALESCE(ps.lead_time_days, 0))
            ORDER BY (COALESCE(inv.quantity, 0) / NULLIF(s.avg_daily, 0)) ASC
        """
        with self._conn() as conn:
            rows = (
                conn.execute(
                    text(sql),
                    {
                        "sid": store_id,
                        "lookback": lookback_days,
                        "cover": cover_days,
                    },
                )
                .mappings()
                .all()
            )
        out = []
        for r in rows:
            d = dict(r)
            avg = float(d["avg_daily"] or 0)
            stock = float(d["stock"] or 0)
            lead = int(d["lead_time_days"] or 0)
            target = avg * (cover_days + lead)
            diff = target - stock
            suggested = max(0, int(diff) + (1 if diff > int(diff) else 0))
            if suggested <= 0:
                continue
            d["avg_daily"] = round(avg, 2)
            d["days_of_cover"] = round(stock / avg, 1) if avg > 0 else None
            d["suggested_qty"] = suggested
            d["reorder_cost"] = round(suggested * float(d["cost_price"] or 0), 2)
            out.append(d)
        return out

    def get_missing_prices(self, store_id: int) -> list[dict]:
        """Products in stock with no active selling price (₹0 or unset).
        Suggests a price from the store's last known price, else the MRP."""
        sql = """
        WITH active_price AS (
            SELECT DISTINCT ON (product_id) product_id, price, mrp
            FROM kirana_oltp.pricing
            WHERE store_id = :sid AND valid_from <= now()
              AND (valid_to IS NULL OR valid_to >= now())
            ORDER BY product_id, valid_from DESC
        ),
        last_known AS (
            SELECT DISTINCT ON (product_id) product_id, price, mrp
            FROM kirana_oltp.pricing
            WHERE store_id = :sid
            ORDER BY product_id, valid_from DESC
        )
        SELECT p.product_id, p.name AS product_name, p.unit,
               COALESCE(inv.quantity, 0) AS stock,
               ap.price::float AS active_price,
               lk.price::float AS last_known_price,
               COALESCE(ap.mrp, lk.mrp)::float AS mrp
        FROM kirana_oltp.inventory inv
        JOIN kirana_oltp.product p ON p.product_id = inv.product_id
        LEFT JOIN active_price ap ON ap.product_id = inv.product_id
        LEFT JOIN last_known  lk ON lk.product_id = inv.product_id
        WHERE inv.store_id = :sid
          AND (ap.price IS NULL OR ap.price = 0)
        ORDER BY p.name ASC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            last_known = d.get("last_known_price")
            mrp = d.get("mrp")
            if last_known and float(last_known) > 0:
                d["suggested_price"] = round(float(last_known), 2)
                d["suggestion_source"] = "your last price"
            elif mrp and float(mrp) > 0:
                d["suggested_price"] = round(float(mrp), 2)
                d["suggestion_source"] = "MRP"
            else:
                d["suggested_price"] = None
                d["suggestion_source"] = None
            out.append(d)
        return out

    def set_product_cost(
        self, product_id: int, cost_price: float, supplier_id: int | None = None
    ) -> dict:
        """Capture a product's real purchase cost into product_supplier so future
        sales snapshot a true cost (no estimate needed). Updates the relevant
        existing row if present, else inserts one."""
        with self._conn() as conn:
            if supplier_id is not None:
                updated = conn.execute(
                    text("""
                    UPDATE kirana_oltp.product_supplier SET cost_price = :c
                    WHERE product_id = :pid AND supplier_id = :sup
                    RETURNING id
                """),
                    {"c": cost_price, "pid": product_id, "sup": supplier_id},
                ).first()
                if not updated:
                    conn.execute(
                        text("""
                        INSERT INTO kirana_oltp.product_supplier (product_id, supplier_id, cost_price)
                        VALUES (:pid, :sup, :c)
                    """),
                        {"pid": product_id, "sup": supplier_id, "c": cost_price},
                    )
            else:
                updated = conn.execute(
                    text("""
                    UPDATE kirana_oltp.product_supplier SET cost_price = :c
                    WHERE id = (SELECT id FROM kirana_oltp.product_supplier
                                WHERE product_id = :pid ORDER BY id LIMIT 1)
                    RETURNING id
                """),
                    {"c": cost_price, "pid": product_id},
                ).first()
                if not updated:
                    conn.execute(
                        text("""
                        INSERT INTO kirana_oltp.product_supplier (product_id, supplier_id, cost_price)
                        VALUES (:pid, NULL, :c)
                    """),
                        {"pid": product_id, "c": cost_price},
                    )
            conn.commit()
        return {"product_id": product_id, "cost_price": cost_price}

    def set_product_price(
        self, store_id: int, product_id: int, price: float, mrp: float | None = None
    ) -> dict:
        """Set a product's selling price by opening a new pricing window and
        closing any currently-open one."""
        with self._conn() as conn:
            conn.execute(
                text("""
                UPDATE kirana_oltp.pricing
                SET valid_to = now()
                WHERE store_id = :sid AND product_id = :pid AND valid_to IS NULL
            """),
                {"sid": store_id, "pid": product_id},
            )
            row = (
                conn.execute(
                    text("""
                INSERT INTO kirana_oltp.pricing (product_id, store_id, price, mrp, valid_from)
                VALUES (:pid, :sid, :price, :mrp, now())
                RETURNING pricing_id, product_id, price::float AS price, mrp::float AS mrp
            """),
                    {"pid": product_id, "sid": store_id, "price": price, "mrp": mrp},
                )
                .mappings()
                .first()
            )
            conn.commit()
        return dict(row)

    def record_return(
        self,
        store_id: int,
        order_id: int | None,
        items: list[dict],
        reason: str | None = None,
        *,
        refund_amount: float = 0,
        is_exchange: bool = False,
        customer_id: int | None = None,
    ) -> dict:
        """Record a customer return/exchange.

        Resaleable units go back into store inventory. Damaged units are logged
        to `return_to_vendor` (so they feed the Return-to-Vendor recovery KPI and
        the owner can claim credit from the distributor). The SAME transaction
        writes the `sales_return` header + per-item rows, so the Returns history
        (fulfilment tab) always reflects what POS recorded — one source of truth.
        items: [{product_id, qty, resaleable}].
        """
        restocked = 0
        to_vendor = 0
        with self._conn() as conn:
            for it in items:
                pid = int(it["product_id"])
                qty = int(it.get("qty") or 0)
                if qty <= 0:
                    continue
                resaleable = bool(it.get("resaleable", True))
                if resaleable:
                    conn.execute(
                        text("""
                        UPDATE kirana_oltp.inventory
                        SET quantity = quantity + :q
                        WHERE product_id = :pid AND store_id = :sid
                    """),
                        {"q": qty, "pid": pid, "sid": store_id},
                    )
                    restocked += qty
                else:
                    row = (
                        conn.execute(
                            text("""
                        SELECT supplier_id, COALESCE(cost_price, 0) AS cost_price
                        FROM kirana_oltp.product_supplier
                        WHERE product_id = :pid
                        ORDER BY cost_price ASC NULLS LAST
                        LIMIT 1
                    """),
                            {"pid": pid},
                        )
                        .mappings()
                        .first()
                    )
                    supplier_id = row["supplier_id"] if row else None
                    cost = float(row["cost_price"]) if row else 0.0
                    conn.execute(
                        text("""
                        INSERT INTO kirana_oltp.return_to_vendor
                            (store_id, supplier_id, product_id, return_date,
                             qty_returned, unit_cost, recovery_pct, amount_recovered, reason)
                        VALUES (:sid, :sup, :pid, CURRENT_DATE, :q, :cost, 0, 0, :reason)
                    """),
                        {
                            "sid": store_id,
                            "sup": supplier_id,
                            "pid": pid,
                            "q": qty,
                            "cost": cost,
                            "reason": (reason or "customer_return")[:60],
                        },
                    )
                    to_vendor += qty

            # Unified history: header + item detail in the same transaction.
            return_id = conn.execute(
                text("""
                INSERT INTO kirana_oltp.sales_return
                    (store_id, order_id, customer_id, reason, refund_amount, is_exchange)
                VALUES (:sid, :oid, :cid, :reason, :refund, :exch)
                RETURNING return_id
            """),
                {
                    "sid": store_id,
                    "oid": order_id,
                    "cid": customer_id,
                    "reason": reason[:50] if reason else None,
                    "refund": round(float(refund_amount or 0), 2),
                    "exch": bool(is_exchange),
                },
            ).scalar()
            for it in items:
                qty = int(it.get("qty") or 0)
                if qty <= 0:
                    continue
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.sales_return_item
                        (return_id, product_id, name, qty, resaleable)
                    SELECT :rid, :pid, p.name, :q, :resale
                    FROM kirana_oltp.product p WHERE p.product_id = :pid
                """),
                    {
                        "rid": return_id,
                        "pid": int(it["product_id"]),
                        "q": qty,
                        "resale": bool(it.get("resaleable", True)),
                    },
                )
            conn.commit()
        return {
            "return_id": int(return_id),
            "order_id": order_id,
            "restocked_units": restocked,
            "to_vendor_units": to_vendor,
        }

    @staticmethod
    def _suggested_markdown(days_left) -> float:
        """Urgency-based clearance markdown so near-expiry stock actually sells."""
        if days_left is None:
            return 0.0
        if days_left <= 1:
            return 40.0
        if days_left <= 3:
            return 25.0
        if days_left <= 5:
            return 15.0
        return 10.0
