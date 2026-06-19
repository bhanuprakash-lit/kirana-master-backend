from __future__ import annotations
import secrets
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class StoreRepositoryMixin:
    def get_vertical_config(self, store_id: int) -> dict:
        """Merged vertical config for a store. Falls back to the 'grocery'
        vertical when the store has no vertical_code (or its row is missing),
        so the app always receives a usable config."""
        sql = """
        SELECT COALESCE(vc.vertical_code, 'grocery') AS vertical_code,
               COALESCE(vc.features, '{}'::jsonb)     AS features,
               vc.unit_set, vc.attribute_set, vc.kpi_set,
               vc.ml_profile, vc.tax_profile,
               COALESCE(vc.copy_pack, '{}'::jsonb)    AS copy_pack
        FROM kirana_oltp.store s
        LEFT JOIN kirana_oltp.vertical_config vc
               ON vc.vertical_code = COALESCE(s.vertical_code, 'grocery')
        WHERE s.store_id = :sid
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
            if row is None or row["unit_set"] is None:
                # Store unknown, or store points at a vertical with no config row
                # yet — return the grocery defaults straight from the table.
                row = (
                    conn.execute(
                        text("""
                    SELECT vertical_code, features, unit_set, attribute_set,
                           kpi_set, ml_profile, tax_profile, copy_pack
                    FROM kirana_oltp.vertical_config
                    WHERE vertical_code = 'grocery'
                """)
                    )
                    .mappings()
                    .first()
                )
        if row is None:
            return {
                "vertical_code": "grocery",
                "features": {},
                "unit_set": [],
                "attribute_set": None,
                "kpi_set": None,
                "ml_profile": None,
                "tax_profile": None,
                "copy_pack": {},
            }
        return {
            "vertical_code": row["vertical_code"],
            "features": row["features"],
            "unit_set": row["unit_set"],
            "attribute_set": row["attribute_set"],
            "kpi_set": row["kpi_set"],
            "ml_profile": row["ml_profile"],
            "tax_profile": row["tax_profile"],
            "copy_pack": row["copy_pack"],
        }

    def register_store_owner_atomic(
        self,
        store_name: str,
        store_type: str,
        footfall: int,
        location: str | None,
        region: str | None,
        username: str,
        password: str,
        full_name: str,
        budget: float | None = None,
        email: str | None = None,
        phone_number: str | None = None,
        firebase_uid: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        vertical_code: str | None = None,
    ) -> tuple[dict, dict]:
        """Create store + user in one transaction. Any failure rolls back both.
        For phone-auth users, password may be empty; a random credential is stored."""
        # For phone-auth users with no password, generate a random internal credential
        if password:
            salt = secrets.token_hex(16)
            ph = self._hash(password, salt)
        else:
            salt = secrets.token_hex(16)
            ph = self._hash(secrets.token_hex(32), salt)  # random, unrecoverable

        with self._conn() as conn:
            # 1. kirana_oltp.store (now holds all metadata)
            # daily_budget is derived from the owner's monthly sales target.
            daily_budget = (budget / 30.0) if budget else None
            store_row = (
                conn.execute(
                    text("""
                INSERT INTO kirana_oltp.store(name, location, region, store_type, vertical_code, footfall, budget, daily_budget, latitude, longitude)
                VALUES(:sn, :location, :region, :st, COALESCE(:vc, 'grocery'), :fp, :budget, :daily_budget, :lat, :lng)
                RETURNING store_id, name, location, region, store_type, vertical_code, footfall, budget, daily_budget, latitude, longitude
            """),
                    {
                        "sn": store_name,
                        "location": location,
                        "region": region,
                        "st": store_type,
                        "vc": vertical_code,
                        "fp": footfall,
                        "budget": budget,
                        "daily_budget": daily_budget,
                        "lat": latitude,
                        "lng": longitude,
                    },
                )
                .mappings()
                .first()
            )
            store_id = store_row["store_id"]

            # 2. kirana_oltp.users — UNIQUE(username) violation rolls back the store too
            user_row = (
                conn.execute(
                    text("""
                INSERT INTO kirana_oltp.users
                    (username, email, full_name, role, store_id,
                     password_salt, password_hash, is_active, phone_number, firebase_uid)
                VALUES(:u, :email, :fn, 'store_owner', :sid, :salt, :ph, TRUE, :phone, :fbuid)
                RETURNING user_id, username, full_name, role, store_id
            """),
                    {
                        "u": username,
                        "email": email or self._default_email(username),
                        "fn": full_name,
                        "sid": store_id,
                        "salt": salt,
                        "ph": ph,
                        "phone": phone_number,
                        "fbuid": firebase_uid,
                    },
                )
                .mappings()
                .first()
            )

            # Advance sequence so auto-inserts never collide with the explicit id
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('kirana_oltp.users','user_id'),"
                    " (SELECT COALESCE(MAX(user_id), 1) FROM kirana_oltp.users))"
                )
            )

            conn.commit()

        store = {**dict(store_row), "store_name": store_row["name"]}
        return store, dict(user_row)

    def create_store(
        self,
        store_name: str,
        store_type: str,
        footfall: int,
        location: str | None = None,
        region: str | None = None,
    ) -> dict:
        sql = """
        INSERT INTO kirana_oltp.store(name, location, region, store_type, footfall)
        VALUES(:sn, :location, :region, :st, :fp)
        RETURNING store_id, name, store_type, footfall
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql),
                    {
                        "sn": store_name,
                        "location": location,
                        "region": region,
                        "st": store_type,
                        "fp": footfall,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return {**dict(row), "store_name": row["name"], "oltp_store_name": row["name"]}

    def list_store_master(self) -> list[dict]:
        sql = """
        SELECT
            store_id,
            name                              AS store_name,
            COALESCE(store_type, 'kirana')    AS store_type,
            COALESCE(footfall, 0)             AS footfall,
            budget,
            daily_budget,
            location,
            region,
            (SELECT COUNT(DISTINCT product_id)
             FROM kirana_oltp.inventory
             WHERE store_id = s.store_id)     AS sku_count
        FROM kirana_oltp.store s
        WHERE COALESCE(is_deleted, FALSE) = FALSE
        ORDER BY store_id
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]

    def get_store(self, store_id: int) -> dict:
        sql = (
            "SELECT *, name AS store_name FROM kirana_oltp.store WHERE store_id = :sid"
        )
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        if not row:
            raise ValueError("Store not found")
        return dict(row)

    def update_store(self, store_id: int, **kwargs) -> dict | None:
        field_map = {
            "store_name": "name",
            "store_type": "store_type",
            "footfall": "footfall",
            "budget": "budget",
            "daily_budget": "daily_budget",
            "location": "location",
            "region": "region",
        }
        sets, params = [], {"sid": store_id}
        for k, v in kwargs.items():
            if v is not None and k in field_map:
                col = field_map[k]
                sets.append(f"{col} = :{k}")
                params[k] = v
        if not sets:
            return None
        sql = (
            f"UPDATE kirana_oltp.store SET {', '.join(sets)} WHERE store_id = :sid "
            f"RETURNING store_id, name AS store_name, store_type, footfall, budget, daily_budget, location, region"
        )
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        return dict(row) if row else None

    def compute_store_footfall(self, store_id: int) -> int:
        """
        Compute average daily footfall based on order volume.
        Logic: Average daily orders in last 30 days * 1.2 multiplier (for non-buying visitors).
        """
        sql = """
        SELECT COUNT(order_id)::float / 30.0 as avg_orders
        FROM kirana_oltp.orders
        WHERE store_id = :sid 
          AND order_date > NOW() - INTERVAL '30 days'
        """
        with self._conn() as conn:
            res = conn.execute(text(sql), {"sid": store_id}).mappings().first()
            avg_orders = float(res["avg_orders"] or 0)

            # Heuristic: 20% of people don't buy anything
            new_footfall = int(max(avg_orders * 1.2, 10))  # Minimum 10

            # Update store table
            conn.execute(
                text(
                    "UPDATE kirana_oltp.store SET footfall = :f WHERE store_id = :sid"
                ),
                {"f": new_footfall, "sid": store_id},
            )
            conn.commit()

        return new_footfall

    def get_store_snapshot(self, store_id: int) -> dict:
        latest_sql = """
        SELECT MAX(snapshot_date)::text AS snapshot_date
        FROM kirana_oltp.inventory_snapshots
        WHERE store_id = :sid
        """
        latest_rows_sql = """
        SELECT
            s.product_id AS sku_id,
            s.snapshot_date::text AS snapshot_date,
            s.units_sold,
            s.stock,
            NULL::numeric AS lost_sales,
            s.revenue,
            s.profit,
            s.price,
            CASE WHEN s.promo_flag IS TRUE THEN 1 WHEN s.promo_flag IS FALSE THEN 0 ELSE NULL END AS promo_flag,
            c.name AS category,
            p.name AS product_name
        FROM kirana_oltp.inventory_snapshots s
        LEFT JOIN kirana_oltp.product p ON p.product_id = s.product_id
        LEFT JOIN kirana_oltp.category c ON c.category_id = p.category_id
        WHERE s.store_id = :sid AND s.snapshot_date = CAST(:snap_date AS date)
        ORDER BY s.product_id
        """
        fallback_sql = """
        SELECT
            i.product_id AS sku_id,
            CURRENT_DATE::text AS snapshot_date,
            NULL::numeric AS units_sold,
            i.quantity::numeric AS stock,
            NULL::numeric AS lost_sales,
            NULL::numeric AS revenue,
            NULL::numeric AS profit,
            pr.price,
            NULL::int AS promo_flag,
            c.name AS category,
            p.name AS product_name
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p ON p.product_id = i.product_id
        LEFT JOIN kirana_oltp.category c ON c.category_id = p.category_id
        LEFT JOIN LATERAL (
            SELECT price FROM kirana_oltp.pricing pr
            WHERE pr.product_id = i.product_id AND pr.store_id = i.store_id
              AND pr.valid_from <= NOW() AND pr.valid_to >= NOW()
            ORDER BY pr.valid_from DESC LIMIT 1
        ) pr ON TRUE
        WHERE i.store_id = :sid
        ORDER BY i.product_id
        """
        today_sales_sql = """
        SELECT oi.product_id, SUM(oi.quantity) as sold_today
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid AND DATE(o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE AT TIME ZONE 'Asia/Kolkata'
        GROUP BY oi.product_id
        """
        with self._conn() as conn:
            sales_rows = (
                conn.execute(text(today_sales_sql), {"sid": store_id}).mappings().all()
            )
            sales_map = {r["product_id"]: int(r["sold_today"]) for r in sales_rows}

            latest = conn.execute(text(latest_sql), {"sid": store_id}).scalar()
            if latest:
                rows = (
                    conn.execute(
                        text(latest_rows_sql), {"sid": store_id, "snap_date": latest}
                    )
                    .mappings()
                    .all()
                )
                items = [dict(r) for r in rows]
                for item in items:
                    item["units_sold"] = sales_map.get(item["sku_id"], 0)
                return {
                    "store_id": store_id,
                    "snapshot_count": len(rows),
                    "snapshot_date": latest,
                    "items": items,
                }

            rows = conn.execute(text(fallback_sql), {"sid": store_id}).mappings().all()
            items = [dict(r) for r in rows]
            for item in items:
                item["units_sold"] = sales_map.get(item["sku_id"], 0)
            return {
                "store_id": store_id,
                "snapshot_count": len(rows),
                "snapshot_date": rows[0]["snapshot_date"] if rows else None,
                "items": items,
            }

    def get_store_deep_dive(self, store_id: int) -> dict:
        # 1. Basic Store & Subscription Info
        #    Surface the full subscription lifecycle (trial vs paid, end dates,
        #    requested tier) so the admin panel can render status + actions.
        info_sql = """
            SELECT s.*,
                   sub.tier, sub.started_at AS sub_started, sub.trial_ends_at,
                   sub.ended_at AS sub_ended, sub.is_trial, sub.trial_tier,
                   sub.requested_tier, sub.monthly_price,
                   u.username, u.phone_number, COALESCE(u.full_name, u.username) AS owner_name
            FROM kirana_oltp.store s
            LEFT JOIN LATERAL (
                SELECT * FROM kirana_oltp.subscription
                WHERE store_id = s.store_id
                ORDER BY started_at DESC
                LIMIT 1
            ) sub ON TRUE
            LEFT JOIN kirana_oltp.users u ON u.store_id = s.store_id AND u.role = 'store_owner'
            WHERE s.store_id = :sid
        """
        # 2. Inventory Stats
        inv_sql = """
            SELECT 
                COUNT(*) AS total_skus,
                COALESCE(SUM(quantity), 0) AS total_stock_units,
                COUNT(*) FILTER (WHERE quantity <= 0) AS out_of_stock_count
            FROM kirana_oltp.inventory
            WHERE store_id = :sid
        """
        # 3. Recent Sales (7 days)
        sales_sql = """
            SELECT 
                DATE(order_date AT TIME ZONE 'Asia/Kolkata')::text AS date,
                COALESCE(SUM(total_amount), 0) AS revenue,
                COUNT(*) AS orders
            FROM kirana_oltp.orders
            WHERE store_id = :sid AND order_date > NOW() - INTERVAL '7 days'
            GROUP BY 1 ORDER BY 1
        """
        # 4. Udhaar Stats
        udhaar_sql = """
            SELECT 
                COALESCE(SUM(amount), 0) AS total_given,
                COALESCE(SUM(amount_paid), 0) AS total_recovered,
                COALESCE(SUM(amount - amount_paid), 0) AS total_pending
            FROM kirana_oltp.khata
            WHERE store_id = :sid AND status != 'written_off'
        """
        # 5. Top Customers (Last 30 Days)
        cust_sql = """
            SELECT c.name, c.phone, COALESCE(SUM(o.total_amount), 0) as total_spent, COUNT(o.order_id) as total_orders
            FROM kirana_oltp.customer c
            JOIN kirana_oltp.orders o ON c.customer_id = o.customer_id
            WHERE c.store_id = :sid AND o.order_date > NOW() - INTERVAL '30 days'
            GROUP BY c.customer_id
            ORDER BY total_spent DESC
            LIMIT 5
        """
        with self._conn() as conn:
            store = conn.execute(text(info_sql), {"sid": store_id}).mappings().first()
            if not store:
                return {}
            inv = conn.execute(text(inv_sql), {"sid": store_id}).mappings().first()
            sales = conn.execute(text(sales_sql), {"sid": store_id}).mappings().all()
            udhaar = (
                conn.execute(text(udhaar_sql), {"sid": store_id}).mappings().first()
            )
            top_customers = (
                conn.execute(text(cust_sql), {"sid": store_id}).mappings().all()
            )

            # Fetch owner id for AI Status
            owner = (
                conn.execute(
                    text(
                        "SELECT user_id FROM kirana_oltp.users WHERE store_id = :sid AND role = 'store_owner' LIMIT 1"
                    ),
                    {"sid": store_id},
                )
                .mappings()
                .first()
            )

        ai_status = self.get_ai_status(owner["user_id"]) if owner else {}
        expiring = self.get_near_expiry_batches(store_id, days=30)

        # Derive a single subscription status + trial countdown for the admin UI.
        store_d = dict(store)
        from datetime import datetime

        now = datetime.now()
        tier = store_d.get("tier")
        sub_ended = store_d.get("sub_ended")
        trial_ends = store_d.get("trial_ends_at")
        ended = bool(sub_ended and sub_ended <= now)
        days_left = None
        if not tier:
            status = "none"
        elif tier == "pending_trial":
            status = "pending_trial"
        elif tier == "trial":
            if ended:
                status, days_left = "cancelled", 0
            elif trial_ends and trial_ends > now:
                status, days_left = "trial", (trial_ends - now).days
            else:
                status, days_left = "trial_expired", 0
        else:  # paid tier (basic / pro / …)
            status = "cancelled" if ended else "active"
        store_d["sub_status"] = status
        store_d["trial_days_left"] = days_left
        store_d["is_paid"] = status == "active"

        return {
            "store": store_d,
            "inventory": dict(inv),
            "sales_history": [dict(s) for s in sales],
            "udhaar": dict(udhaar),
            "top_customers": [dict(c) for c in top_customers],
            "ai_status": ai_status,
            "expiring_batches": expiring,
        }
