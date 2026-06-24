from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class StaffRepositoryMixin:
    """Module M5 — Staff Operations (roster, attendance, tasks, commission)."""

    def list_staff(self, store_id: int, include_inactive: bool = False) -> list[dict]:
        sql = ("SELECT staff_id, user_id, name, phone, role, commission_pct, is_active "
               "FROM kirana_oltp.staff WHERE store_id = :sid")
        if not include_inactive:
            sql += " AND is_active = TRUE"
        sql += " ORDER BY name"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(text(sql), {"sid": store_id}).mappings().all()]

    def create_staff(self, store_id: int, name: str, phone: str | None = None,
                     role: str | None = None, commission_pct: float = 0,
                     user_id: int | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.staff (store_id, user_id, name, phone, role, commission_pct)
                VALUES (:sid, :uid, :name, :phone, :role, :comm)
                RETURNING staff_id, user_id, name, phone, role, commission_pct, is_active
            """), {"sid": store_id, "uid": user_id, "name": name, "phone": phone,
                   "role": role, "comm": commission_pct}).mappings().first()
            conn.commit()
        return dict(row)

    def update_staff(self, staff_id: int, store_id: int, **fields) -> dict | None:
        allowed = {"name", "phone", "role", "commission_pct", "is_active"}
        sets, params = [], {"id": staff_id, "sid": store_id}
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k} = :{k}")
                params[k] = v
        if not sets:
            return None
        with self._conn() as conn:
            row = conn.execute(text(
                "UPDATE kirana_oltp.staff SET " + ", ".join(sets) +
                " WHERE staff_id = :id AND store_id = :sid "
                "RETURNING staff_id, user_id, name, phone, role, commission_pct, is_active"),
                params).mappings().first()
            conn.commit()
        return dict(row) if row else None

    # ── Attendance ──────────────────────────────────────────────────────────
    def mark_attendance(self, store_id: int, staff_id: int, att_date: str, status: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.staff_attendance (staff_id, store_id, att_date, status, check_in)
                VALUES (:stf, :sid, CAST(:d AS DATE), :st, NOW())
                ON CONFLICT (staff_id, att_date) DO UPDATE SET status = EXCLUDED.status
                RETURNING id, staff_id, att_date, status
            """), {"stf": staff_id, "sid": store_id, "d": att_date, "st": status}).mappings().first()
            conn.commit()
        return dict(row)

    def list_attendance(self, store_id: int, att_date: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT s.staff_id, s.name, a.status
                FROM kirana_oltp.staff s
                LEFT JOIN kirana_oltp.staff_attendance a
                       ON a.staff_id = s.staff_id AND a.att_date = CAST(:d AS DATE)
                WHERE s.store_id = :sid AND s.is_active = TRUE
                ORDER BY s.name
            """), {"sid": store_id, "d": att_date}).mappings().all()
        return [dict(r) for r in rows]

    def attendance_history(self, store_id: int, staff_id: int, days: int = 30) -> dict:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT att_date, status
                FROM kirana_oltp.staff_attendance
                WHERE store_id = :sid AND staff_id = :stf
                  AND att_date >= CURRENT_DATE - (:days || ' days')::interval
                ORDER BY att_date DESC
            """), {"sid": store_id, "stf": staff_id, "days": days}).mappings().all()
        history = [{"att_date": str(r["att_date"]), "status": r["status"]} for r in rows]
        counts = {"present": 0, "absent": 0, "half_day": 0}
        for r in history:
            if r["status"] in counts:
                counts[r["status"]] += 1
        return {"history": history, "counts": counts}

    # ── Tasks ───────────────────────────────────────────────────────────────
    def list_tasks(self, store_id: int, include_done: bool = True) -> list[dict]:
        sql = ("SELECT t.task_id, t.staff_id, t.title, t.due_date, t.is_done, s.name AS staff_name "
               "FROM kirana_oltp.staff_task t LEFT JOIN kirana_oltp.staff s ON t.staff_id = s.staff_id "
               "WHERE t.store_id = :sid")
        if not include_done:
            sql += " AND t.is_done = FALSE"
        sql += " ORDER BY t.is_done, t.due_date NULLS LAST, t.task_id DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(text(sql), {"sid": store_id}).mappings().all()]

    def create_task(self, store_id: int, title: str, staff_id: int | None = None,
                    due_date: str | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.staff_task (store_id, staff_id, title, due_date)
                VALUES (:sid, :stf, :title, CAST(:due AS DATE))
                RETURNING task_id, staff_id, title, due_date, is_done
            """), {"sid": store_id, "stf": staff_id, "title": title, "due": due_date}).mappings().first()
            conn.commit()
        return dict(row)

    def set_task_done(self, task_id: int, store_id: int, is_done: bool) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "UPDATE kirana_oltp.staff_task SET is_done = :d WHERE task_id = :id AND store_id = :sid"),
                {"d": is_done, "id": task_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    # ── Performance (drives F4 staff KPI) ───────────────────────────────────
    def staff_performance(self, store_id: int, days: int = 30) -> dict:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT COALESCE(u.full_name, u.username, 'Unknown') AS staff_name,
                       COUNT(DISTINCT o.order_id) AS orders,
                       ROUND(COALESCE(SUM(o.total_amount), 0)::numeric, 2) AS revenue
                FROM kirana_oltp.orders o
                LEFT JOIN kirana_oltp.users u ON o.user_id = u.user_id
                WHERE o.store_id = :sid AND o.order_status = 'completed'
                  AND o.order_date >= NOW() - (:days || ' days')::interval
                GROUP BY u.full_name, u.username
                ORDER BY revenue DESC
            """), {"sid": store_id, "days": days}).mappings().all()
        items = [dict(r) for r in rows]
        return {"staff_count": len(items),
                "total_revenue": round(sum(float(r["revenue"] or 0) for r in items), 2),
                "by_staff": items}
