from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class FinanceRepositoryMixin:
    def get_finance_overview(self, store_id: int) -> dict:
        sales_sql = """
        SELECT
            COALESCE(SUM(total_amount), 0) AS amount
        FROM kirana_oltp.orders
        WHERE store_id = :sid
          AND DATE_TRUNC('month', order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') = 
              DATE_TRUNC('month', CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')
        """
        sku_count_sql = """
        SELECT
            COUNT(DISTINCT product_id) AS sku_count
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid
          AND DATE_TRUNC('month', o.order_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') = 
              DATE_TRUNC('month', CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')
        """
        udhaar_sql = """
        SELECT
            COALESCE(SUM(amount - amount_paid), 0) AS total_pending,
            COALESCE(SUM(amount_paid), 0)          AS total_recovered,
            -- customers who STILL owe (balance > 0), not everyone ever given udhaar
            COUNT(DISTINCT customer_id) FILTER (WHERE amount > amount_paid) AS customer_count
        FROM kirana_oltp.khata
        WHERE store_id = :sid
        """
        # Credit *extended this month* — the numerator for the Credit-vs-Sales
        # ratio. This must be scoped to the current month so the ratio answers
        # "how much of THIS month's sales went on credit", not "all-time
        # outstanding vs this month's sales" (which let the ratio sit at 100%
        # even in a month with zero udhaar sales).
        monthly_credit_sql = """
        SELECT COALESCE(SUM(amount), 0) AS monthly_credit
        FROM kirana_oltp.khata
        WHERE store_id = :sid
          AND DATE_TRUNC('month', issue_date) =
              DATE_TRUNC('month', (NOW() AT TIME ZONE 'Asia/Kolkata')::date)
        """
        with self._conn() as conn:
            sales = conn.execute(text(sales_sql), {"sid": store_id}).mappings().first()
            skus = (
                conn.execute(text(sku_count_sql), {"sid": store_id}).mappings().first()
            )
            udhaar = (
                conn.execute(text(udhaar_sql), {"sid": store_id}).mappings().first()
            )
            mcredit = (
                conn.execute(text(monthly_credit_sql), {"sid": store_id})
                .mappings()
                .first()
            )

        return {
            "monthly_sales": {
                "amount": float(sales["amount"]),
                "sku_count": int(skus["sku_count"]),
                # Credit given this month (used for the Credit-vs-Sales ratio).
                "credit_amount": float(mcredit["monthly_credit"]),
            },
            "udhaar_stats": {
                "total_pending": float(udhaar["total_pending"]),
                "total_recovered": float(udhaar["total_recovered"]),
                "customer_count": int(udhaar["customer_count"]),
            },
        }

    def get_udhaar_list(
        self, store_id: int, include_recovered: bool = False
    ) -> list[dict]:
        sql = """
        SELECT
            k.khata_id,
            k.customer_id,
            k.order_id,
            c.name AS customer_name,
            c.phone,
            k.amount          AS original_amount,
            k.amount_paid,
            (k.amount - k.amount_paid) AS balance,
            k.issue_date::text AS date_taken,
            k.due_date::text   AS due_date,
            k.status,
            (CURRENT_DATE - k.issue_date) AS days_pending,
            (c.last_udhaar_reminded_at IS NOT NULL
             AND (c.last_udhaar_reminded_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date
                 = (NOW() AT TIME ZONE 'Asia/Kolkata')::date) AS reminded_today
        FROM kirana_oltp.khata k
        JOIN kirana_oltp.customer c ON k.customer_id = c.customer_id
        WHERE k.store_id = :sid
        """
        if not include_recovered:
            sql += " AND k.status IN ('open', 'overdue', 'pending')"
        else:
            sql += " AND k.status != 'written_off'"

        sql += " ORDER BY k.issue_date DESC"

        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def udhaar_reminded_today(self, store_id: int, customer_id: int) -> bool:
        """True if this customer already got a udhaar reminder today (IST day)."""
        sql = """
        SELECT (last_udhaar_reminded_at IS NOT NULL
                AND (last_udhaar_reminded_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date
                    = (NOW() AT TIME ZONE 'Asia/Kolkata')::date) AS reminded
        FROM kirana_oltp.customer
        WHERE customer_id = :cid AND store_id = :sid
        """
        with self._conn() as conn:
            row = (
                conn.execute(text(sql), {"cid": customer_id, "sid": store_id})
                .mappings()
                .first()
            )
        return bool(row and row["reminded"])

    def mark_udhaar_reminded(self, store_id: int, customer_id: int) -> None:
        """Stamp the customer's last reminder time (after a successful send)."""
        with self._conn() as conn:
            conn.execute(
                text(
                    "UPDATE kirana_oltp.customer SET last_udhaar_reminded_at = NOW() "
                    "WHERE customer_id = :cid AND store_id = :sid"
                ),
                {"cid": customer_id, "sid": store_id},
            )
            conn.commit()

    def get_smart_udhaar(self, store_id: int) -> list[dict]:
        """Open udhaar ranked by recovery risk, with a suggested action.

        Risk (0-100, higher = more at risk) blends:
          - how long it's been outstanding (up to 40 pts),
          - the customer's past repayment ratio (up to 30 pts),
          - how long since they last shopped here (up to 30 pts).
        Replaces the old purely days-based reminder ordering.
        """
        sql = """
        WITH cust_hist AS (
            SELECT customer_id,
                   SUM(amount)      AS total_khata,
                   SUM(amount_paid) AS total_paid
            FROM kirana_oltp.khata
            WHERE store_id = :sid
            GROUP BY customer_id
        ),
        last_order AS (
            SELECT customer_id, MAX(order_date)::date AS last_order_date
            FROM kirana_oltp.orders
            WHERE store_id = :sid AND customer_id IS NOT NULL
            GROUP BY customer_id
        )
        SELECT k.khata_id, k.customer_id, c.name AS customer_name, c.phone,
               (k.amount - k.amount_paid)::float AS balance,
               k.issue_date::text AS date_taken,
               (CURRENT_DATE - k.issue_date) AS days_pending,
               COALESCE(ch.total_khata, 0)::float AS total_khata,
               COALESCE(ch.total_paid, 0)::float  AS total_paid,
               (CURRENT_DATE - lo.last_order_date) AS days_since_order
        FROM kirana_oltp.khata k
        JOIN kirana_oltp.customer c ON k.customer_id = c.customer_id
        LEFT JOIN cust_hist ch ON ch.customer_id = k.customer_id
        LEFT JOIN last_order lo ON lo.customer_id = k.customer_id
        WHERE k.store_id = :sid
          AND k.status IN ('open', 'overdue', 'pending')
          AND (k.amount - k.amount_paid) > 0
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            days = int(d.get("days_pending") or 0)
            total_khata = float(d.get("total_khata") or 0)
            total_paid = float(d.get("total_paid") or 0)
            paid_ratio = (total_paid / total_khata) if total_khata > 0 else 0.0
            dso = d.get("days_since_order")
            days_score = min(days, 90) / 90 * 40
            hist_score = (1 - min(max(paid_ratio, 0.0), 1.0)) * 30
            inact_score = 30 if dso is None else min(int(dso), 90) / 90 * 30
            risk = max(0, min(100, round(days_score + hist_score + inact_score)))
            band = "high" if risk >= 67 else ("medium" if risk >= 34 else "low")
            if band == "high":
                action = "Call or visit — high risk of non-recovery"
            elif band == "medium":
                action = "Send a WhatsApp reminder"
            else:
                action = "Likely to pay — a gentle nudge is enough"
            d["risk_score"] = risk
            d["risk_band"] = band
            d["recovery_likelihood"] = 100 - risk
            d["suggested_action"] = action
            d.pop("total_khata", None)
            d.pop("total_paid", None)
            out.append(d)
        out.sort(key=lambda x: x["risk_score"], reverse=True)
        return out

    def record_udhaar_recovery(
        self, store_id: int, khata_id: int, recovery_amount: float
    ) -> dict:
        # 1. Fetch current record
        sql_fetch = "SELECT amount, amount_paid FROM kirana_oltp.khata WHERE khata_id = :kid AND store_id = :sid"
        with self._conn() as conn:
            row = (
                conn.execute(text(sql_fetch), {"kid": khata_id, "sid": store_id})
                .mappings()
                .first()
            )
            if not row:
                raise ValueError("Udhaar record not found")

            new_paid = float(row["amount_paid"]) + recovery_amount
            status = "settled" if new_paid >= float(row["amount"]) else "open"

            sql_update = """
            UPDATE kirana_oltp.khata
            SET amount_paid = :p, status = :s
            WHERE khata_id = :kid AND store_id = :sid
            """
            conn.execute(
                text(sql_update),
                {"p": new_paid, "s": status, "kid": khata_id, "sid": store_id},
            )
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.khata_payments(khata_id, store_id, amount, paid_at)
                VALUES (:kid, :sid, :amt, NOW())
            """),
                {"kid": khata_id, "sid": store_id, "amt": recovery_amount},
            )
            conn.commit()

            # 2. Return the updated record with customer info
            sql_final = """
            SELECT
                k.khata_id,
                k.customer_id,
                c.name AS customer_name,
                c.phone,
                (k.amount - k.amount_paid) AS balance,
                k.issue_date::text AS date_taken,
                (CURRENT_DATE - k.issue_date) AS days_pending
            FROM kirana_oltp.khata k
            JOIN kirana_oltp.customer c ON k.customer_id = c.customer_id
            WHERE k.khata_id = :kid
            """
            result = conn.execute(text(sql_final), {"kid": khata_id}).mappings().first()

        return dict(result)

    def get_khata_payments(self, store_id: int, khata_id: int) -> list[dict]:
        sql = """
            SELECT payment_id, amount, paid_at::text AS paid_at, notes
            FROM kirana_oltp.khata_payments
            WHERE khata_id = :kid AND store_id = :sid
            ORDER BY paid_at DESC
        """
        with self._conn() as conn:
            rows = (
                conn.execute(text(sql), {"kid": khata_id, "sid": store_id})
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def add_udhaar(
        self,
        store_id: int,
        customer_name: str,
        phone: str,
        amount: float,
        due_date: str | None = None,
    ) -> dict:
        with self._conn() as conn:
            # 1. Find or create customer (scoped to store_id)
            cust_sql = "SELECT customer_id FROM kirana_oltp.customer WHERE phone = :p AND store_id = :sid"
            cust_row = (
                conn.execute(text(cust_sql), {"p": phone, "sid": store_id})
                .mappings()
                .first()
            )

            if not cust_row:
                ins_cust = "INSERT INTO kirana_oltp.customer(name, phone, store_id) VALUES(:n, :p, :sid) RETURNING customer_id"
                customer_id = conn.execute(
                    text(ins_cust), {"n": customer_name, "p": phone, "sid": store_id}
                ).scalar()
            else:
                customer_id = cust_row["customer_id"]
                # Re-transacting with this phone revives a soft-deleted customer
                # (and refreshes the name) — otherwise a "deleted" customer would
                # silently collect new udhaar while staying hidden from the
                # customer directory.
                conn.execute(
                    text(
                        "UPDATE kirana_oltp.customer "
                        "SET is_deleted = FALSE, deleted_at = NULL, name = :n "
                        "WHERE customer_id = :cid"
                    ),
                    {"n": customer_name, "cid": customer_id},
                )

            # 2. Create khata entry
            # Note: Using 'pending' as status per request, though 'open' was the previous convention
            ins_khata = """
            INSERT INTO kirana_oltp.khata(customer_id, store_id, amount, amount_paid, issue_date, due_date, status)
            VALUES(:cid, :sid, :amt, 0, CURRENT_DATE,
                   COALESCE(CAST(:due AS DATE), CURRENT_DATE + INTERVAL '30 days'), 'pending')
            RETURNING khata_id, customer_id, amount, amount_paid, status,
                      issue_date::text AS date_taken, due_date::text AS due_date
            """
            khata = (
                conn.execute(
                    text(ins_khata),
                    {
                        "cid": customer_id,
                        "sid": store_id,
                        "amt": amount,
                        "due": due_date,
                    },
                )
                .mappings()
                .first()
            )

            conn.commit()

        res = dict(khata)
        res.update(
            {
                "customer_name": customer_name,
                "phone": phone,
                "balance": float(khata["amount"]) - float(khata["amount_paid"]),
            }
        )
        return res

    def create_udhaar_consent(
        self,
        store_id: int,
        audio_blob: str,
        order_id: int | None = None,
        khata_id: int | None = None,
        customer_id: int | None = None,
        duration_sec: float | None = None,
        language: str | None = None,
        agreed_total: float | None = None,
        agreed_udhaar: float | None = None,
        promised_date: str | None = None,
    ) -> dict:
        """Record an uploaded consent clip. Resolves khata_id/customer_id from the
        order when not supplied. Analysis stays NULL (status 'pending') until the
        in-house voice model fills it."""
        sql = """
        INSERT INTO kirana_oltp.udhaar_consent
            (store_id, order_id, khata_id, customer_id, audio_blob,
             duration_sec, language, agreed_total, agreed_udhaar, promised_date, status)
        VALUES
            (:sid, :oid,
             COALESCE(:kid, (SELECT khata_id FROM kirana_oltp.khata
                             WHERE order_id = :oid ORDER BY khata_id LIMIT 1)),
             COALESCE(:cid, (SELECT customer_id FROM kirana_oltp.orders
                             WHERE order_id = :oid)),
             :blob, :dur, :lang, :atot, :audh, CAST(:pdate AS DATE), 'pending')
        RETURNING consent_id, status, created_at::text AS created_at
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql),
                    {
                        "sid": store_id,
                        "oid": order_id,
                        "kid": khata_id,
                        "cid": customer_id,
                        "blob": audio_blob,
                        "dur": duration_sec,
                        "lang": language,
                        "atot": agreed_total,
                        "audh": agreed_udhaar,
                        "pdate": promised_date,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return dict(row)

    def get_consent_for_order(self, store_id: int, order_id: int) -> dict | None:
        """Latest consent record for an order (for the order-details screen)."""
        sql = """
        SELECT consent_id, order_id, khata_id, customer_id,
               audio_blob, duration_sec, language,
               agreed_total::float AS agreed_total,
               agreed_udhaar::float AS agreed_udhaar,
               promised_date::text AS promised_date,
               status, analysis,
               voice_match_score::float AS voice_match_score,
               created_at::text AS created_at,
               analyzed_at::text AS analyzed_at
        FROM kirana_oltp.udhaar_consent
        WHERE store_id = :sid AND order_id = :oid
        ORDER BY consent_id DESC
        LIMIT 1
        """
        with self._conn() as conn:
            row = (
                conn.execute(text(sql), {"sid": store_id, "oid": order_id})
                .mappings()
                .first()
            )
        if not row:
            return None
        d = dict(row)
        # Expose the authed proxy URL the app fetches the clip through.
        d["audio_url"] = f"/kirana/finance/udhaar/consent/audio/{d['audio_blob']}"
        return d

    def create_cashflow_request(
        self, store_id: int, user_id: int, amount: float, selected_bank: str | None
    ) -> dict:
        store = self.get_store(store_id)
        sql = """
        INSERT INTO kirana_oltp.cashflow_requests
            (store_id, user_id, amount_requested, selected_bank,
             store_name, location, avg_footfall)
        VALUES (:sid, :uid, :amt, :bank, :sname, :loc, :ff)
        RETURNING request_id, status, created_at
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql),
                    {
                        "sid": store_id,
                        "uid": user_id,
                        "amt": amount,
                        "bank": selected_bank,
                        "sname": store.get("name"),
                        "loc": store.get("location"),
                        "ff": store.get("footfall"),
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return dict(row)

    def get_cashflow_status(self, store_id: int) -> dict:
        sql = """
        SELECT request_id, status, amount_requested, selected_bank, created_at
        FROM kirana_oltp.cashflow_requests
        WHERE store_id = :sid
        ORDER BY created_at DESC
        LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"sid": store_id}).mappings().first()
        if not row:
            return {"has_request": False}
        return {
            "has_request": True,
            "request_id": row["request_id"],
            "status": row["status"],
            "amount": float(row["amount_requested"]),
            "selected_bank": row["selected_bank"],
            "created_at": str(row["created_at"]),
        }
