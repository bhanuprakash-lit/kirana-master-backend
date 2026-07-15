from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class ServicesRepositoryMixin:
    """Module M4 — Services & Appointments (salon / fitness / optical).

    A priced service catalogue, calendar bookings with status, and prepaid
    membership/package bundles. Verticals gate this via features.appointments.
    """

    # ── Service catalogue ───────────────────────────────────────────────────
    def list_services(self, store_id: int, include_inactive: bool = False) -> list[dict]:
        sql = """
        SELECT service_id, name, price, duration_min, category, is_active, product_id
        FROM kirana_oltp.service WHERE store_id = :sid
        """
        if not include_inactive:
            sql += " AND is_active = TRUE"
        sql += " ORDER BY name"
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def create_service(self, store_id: int, name: str, price: float,
                       duration_min: int = 30, category: str | None = None) -> dict:
        """Create a service AND its linked is_service product (same txn).

        The product row is what makes the service sellable at POS through the
        normal order pipeline (order_item FK, revenue, KPIs). It carries no
        stock — the sale trigger skips inventory for is_service products.
        """
        with self._conn() as conn:
            row = conn.execute(
                text("""
                INSERT INTO kirana_oltp.service (store_id, name, price, duration_min, category)
                VALUES (:sid, :name, :price, :dur, :cat)
                RETURNING service_id, name, price, duration_min, category, is_active
                """),
                {"sid": store_id, "name": name, "price": price,
                 "dur": duration_min, "cat": category},
            ).mappings().first()
            pid = conn.execute(
                text("""
                INSERT INTO kirana_oltp.product (category_id, name, unit, is_service)
                SELECT category_id, :name, 'service', TRUE
                FROM kirana_oltp.category
                WHERE name = 'Services' AND vertical_code IS NULL
                LIMIT 1
                RETURNING product_id
                """),
                {"name": name},
            ).scalar()
            if pid is not None:
                conn.execute(
                    text("UPDATE kirana_oltp.service SET product_id = :pid "
                         "WHERE service_id = :sid_id"),
                    {"pid": pid, "sid_id": row["service_id"]},
                )
            conn.commit()
        return {**dict(row), "product_id": pid}

    def update_service(self, service_id: int, store_id: int, **fields) -> dict | None:
        allowed = {"name", "price", "duration_min", "category", "is_active"}
        sets, params = [], {"sid_id": service_id, "sid": store_id}
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k} = :{k}")
                params[k] = v
        if not sets:
            return None
        sql = ("UPDATE kirana_oltp.service SET " + ", ".join(sets) +
               " WHERE service_id = :sid_id AND store_id = :sid "
               "RETURNING service_id, name, price, duration_min, category, is_active, product_id")
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            # Keep the linked product's name in sync so receipts/order history
            # show the renamed service correctly.
            if row and row["product_id"] is not None and "name" in params:
                conn.execute(
                    text("UPDATE kirana_oltp.product SET name = :name "
                         "WHERE product_id = :pid AND is_service"),
                    {"name": params["name"], "pid": row["product_id"]},
                )
            conn.commit()
        return dict(row) if row else None

    # ── Appointments ────────────────────────────────────────────────────────
    def list_appointments(self, store_id: int, day: str | None = None,
                          date_from: str | None = None, date_to: str | None = None) -> list[dict]:
        sql = """
        SELECT a.appointment_id, a.customer_id, a.service_id, a.staff_user_id,
               a.customer_name, a.customer_phone, a.starts_at, a.duration_min,
               a.status, a.price, a.order_id, a.notes,
               s.name AS service_name,
               COALESCE(c.name, a.customer_name) AS display_name
        FROM kirana_oltp.appointment a
        LEFT JOIN kirana_oltp.service s ON a.service_id = s.service_id
        LEFT JOIN kirana_oltp.customer c ON a.customer_id = c.customer_id
        WHERE a.store_id = :sid
        """
        params: dict = {"sid": store_id}
        if day:
            sql += " AND a.starts_at::date = CAST(:day AS DATE)"
            params["day"] = day
        elif date_from and date_to:
            sql += " AND a.starts_at::date BETWEEN CAST(:df AS DATE) AND CAST(:dt AS DATE)"
            params["df"], params["dt"] = date_from, date_to
        sql += " ORDER BY a.starts_at"
        with self._conn() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

    def create_appointment(self, store_id: int, starts_at: str, *,
                           service_id: int | None = None, customer_id: int | None = None,
                           customer_name: str | None = None, customer_phone: str | None = None,
                           staff_user_id: int | None = None, duration_min: int | None = None,
                           price: float | None = None, notes: str | None = None) -> dict:
        # Default duration + price from the service when not supplied.
        if service_id and (duration_min is None or price is None):
            with self._conn() as conn:
                r = conn.execute(text(
                    "SELECT price, duration_min FROM kirana_oltp.service WHERE service_id = :id"),
                    {"id": service_id}).mappings().first()
            svc = dict(r) if r else {}
            if duration_min is None:
                duration_min = int(svc.get("duration_min") or 30)
            if price is None:
                price = float(svc.get("price") or 0)
        with self._conn() as conn:
            row = conn.execute(
                text("""
                INSERT INTO kirana_oltp.appointment
                    (store_id, customer_id, service_id, staff_user_id, customer_name,
                     customer_phone, starts_at, duration_min, price, notes)
                VALUES (:sid, :cid, :svc, :staff, :cname, :cphone,
                        CAST(:starts AS TIMESTAMPTZ), :dur, :price, :notes)
                RETURNING appointment_id, customer_id, service_id, staff_user_id,
                          customer_name, customer_phone, starts_at, duration_min,
                          status, price, order_id, notes
                """),
                {"sid": store_id, "cid": customer_id, "svc": service_id,
                 "staff": staff_user_id, "cname": customer_name, "cphone": customer_phone,
                 "starts": starts_at, "dur": duration_min or 30, "price": price, "notes": notes},
            ).mappings().first()
            conn.commit()
        return dict(row)

    def update_appointment_status(self, appointment_id: int, store_id: int,
                                  status: str, order_id: int | None = None) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                UPDATE kirana_oltp.appointment
                SET status = :st, order_id = COALESCE(:oid, order_id)
                WHERE appointment_id = :aid AND store_id = :sid
                RETURNING appointment_id, status, order_id
                """),
                {"st": status, "oid": order_id, "aid": appointment_id, "sid": store_id},
            ).mappings().first()
            conn.commit()
        return dict(row) if row else None

    def appointment_utilisation(self, store_id: int, days: int = 30) -> dict:
        """Booked vs completed vs no-show over the window (drives the F4 KPI)."""
        with self._conn() as conn:
            row = conn.execute(
                text("""
                SELECT
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                  COUNT(*) FILTER (WHERE status = 'no_show')   AS no_show,
                  COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
                FROM kirana_oltp.appointment
                WHERE store_id = :sid
                  AND starts_at >= NOW() - (:days || ' days')::interval
                """),
                {"sid": store_id, "days": days},
            ).mappings().first()
        d = dict(row) if row else {}
        total = int(d.get("total") or 0)
        completed = int(d.get("completed") or 0)
        util = round(completed / total * 100, 1) if total else 0.0
        return {"total": total, "completed": completed,
                "no_show": int(d.get("no_show") or 0),
                "cancelled": int(d.get("cancelled") or 0),
                "utilisation_pct": util}

    # ── Memberships / packages ──────────────────────────────────────────────
    def list_memberships(self, store_id: int, customer_id: int | None = None) -> list[dict]:
        sql = """
        SELECT m.membership_id, m.customer_id, m.name, m.total_sessions,
               m.used_sessions, m.price, m.valid_until, m.is_active,
               c.name AS customer_name
        FROM kirana_oltp.membership m
        LEFT JOIN kirana_oltp.customer c ON m.customer_id = c.customer_id
        WHERE m.store_id = :sid
        """
        params: dict = {"sid": store_id}
        if customer_id is not None:
            sql += " AND m.customer_id = :cid"
            params["cid"] = customer_id
        sql += " ORDER BY m.is_active DESC, m.created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

    def create_membership(self, store_id: int, customer_id: int, name: str,
                          total_sessions: int, price: float, valid_until: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                INSERT INTO kirana_oltp.membership
                    (store_id, customer_id, name, total_sessions, price, valid_until)
                VALUES (:sid, :cid, :name, :tot, :price, CAST(:vu AS DATE))
                RETURNING membership_id, customer_id, name, total_sessions,
                          used_sessions, price, valid_until, is_active
                """),
                {"sid": store_id, "cid": customer_id, "name": name,
                 "tot": total_sessions, "price": price, "vu": valid_until},
            ).mappings().first()
            conn.commit()
        return dict(row)

    def use_membership_session(self, membership_id: int, store_id: int) -> dict | None:
        """Consume one session; deactivate when the bundle is exhausted."""
        with self._conn() as conn:
            row = conn.execute(
                text("""
                UPDATE kirana_oltp.membership
                SET used_sessions = used_sessions + 1,
                    is_active = CASE
                        WHEN total_sessions > 0 AND used_sessions + 1 >= total_sessions
                        THEN FALSE ELSE is_active END
                WHERE membership_id = :mid AND store_id = :sid AND is_active = TRUE
                  AND (total_sessions = 0 OR used_sessions < total_sessions)
                RETURNING membership_id, total_sessions, used_sessions, is_active
                """),
                {"mid": membership_id, "sid": store_id},
            ).mappings().first()
            conn.commit()
        return dict(row) if row else None

    def service_revenue(self, store_id: int, days: int = 30) -> dict:
        """Revenue by service from completed appointments (drives the F4 KPI)."""
        with self._conn() as conn:
            rows = conn.execute(
                text("""
                SELECT COALESCE(s.name, 'Service') AS service_name,
                       COUNT(*) AS count,
                       ROUND(SUM(COALESCE(a.price, 0))::numeric, 2) AS revenue
                FROM kirana_oltp.appointment a
                LEFT JOIN kirana_oltp.service s ON a.service_id = s.service_id
                WHERE a.store_id = :sid AND a.status = 'completed'
                  AND a.starts_at >= NOW() - (:days || ' days')::interval
                GROUP BY s.name ORDER BY revenue DESC
                """),
                {"sid": store_id, "days": days},
            ).mappings().all()
        items = [dict(r) for r in rows]
        total = sum(float(r["revenue"] or 0) for r in items)
        return {"total_revenue": round(total, 2), "by_service": items}
