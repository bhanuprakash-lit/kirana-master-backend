from __future__ import annotations
import secrets
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class ReferralRepositoryMixin:
    def create_referral_campaign(
        self,
        store_id: int,
        name: str,
        referral_discount_pct: float,
        milestone_every_n: int,
        milestone_reward_pct: float,
        max_referrals_per_referrer: int = 50,
    ) -> dict:
        sql = """
        INSERT INTO kirana_oltp.referral_campaigns
            (store_id, name, referral_discount_pct, milestone_every_n,
             milestone_reward_pct, max_referrals_per_referrer)
        VALUES (:sid, :name, :rdp, :men, :mrp, :maxr)
        RETURNING *
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(sql),
                    {
                        "sid": store_id,
                        "name": name,
                        "rdp": referral_discount_pct,
                        "men": milestone_every_n,
                        "mrp": milestone_reward_pct,
                        "maxr": max_referrals_per_referrer,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return dict(row)

    def list_referral_campaigns(self, store_id: int) -> list[dict]:
        sql = """
        SELECT
            c.*,
            COALESCE(tok.token_count, 0)  AS token_count,
            COALESCE(ref.total_referrals, 0) AS total_referrals
        FROM kirana_oltp.referral_campaigns c
        LEFT JOIN (
            SELECT campaign_id, COUNT(*) AS token_count
            FROM kirana_oltp.referral_tokens
            GROUP BY campaign_id
        ) tok ON tok.campaign_id = c.campaign_id
        LEFT JOIN (
            SELECT t.campaign_id, COUNT(*) AS total_referrals
            FROM kirana_oltp.referrals r
            JOIN kirana_oltp.referral_tokens t ON r.token_id = t.token_id
            WHERE r.status = 'rewarded'
            GROUP BY t.campaign_id
        ) ref ON ref.campaign_id = c.campaign_id
        WHERE c.store_id = :sid
        ORDER BY c.created_at DESC
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def toggle_referral_campaign(self, campaign_id: int, is_active: bool) -> dict:
        sql = """
        UPDATE kirana_oltp.referral_campaigns SET is_active = :active
        WHERE campaign_id = :cid RETURNING *
        """
        with self._conn() as conn:
            row = (
                conn.execute(text(sql), {"cid": campaign_id, "active": is_active})
                .mappings()
                .first()
            )
            conn.commit()
        return dict(row) if row else {}

    def get_or_create_referral_token(
        self, store_id: int, referrer_customer_id: int, campaign_id: int
    ) -> dict:
        check_sql = """
        SELECT token_id, token_hash FROM kirana_oltp.referral_tokens
        WHERE referrer_customer_id = :cid AND campaign_id = :camp
        """
        with self._conn() as conn:
            row = (
                conn.execute(
                    text(check_sql), {"cid": referrer_customer_id, "camp": campaign_id}
                )
                .mappings()
                .first()
            )
            if row:
                return {
                    "token_id": row["token_id"],
                    "token_hash": row["token_hash"],
                    "is_new": False,
                }

            token_hash = secrets.token_hex(24)
            ins_sql = """
            INSERT INTO kirana_oltp.referral_tokens
                (store_id, referrer_customer_id, campaign_id, token_hash)
            VALUES (:sid, :cid, :camp, :tok)
            RETURNING token_id, token_hash
            """
            row = (
                conn.execute(
                    text(ins_sql),
                    {
                        "sid": store_id,
                        "cid": referrer_customer_id,
                        "camp": campaign_id,
                        "tok": token_hash,
                    },
                )
                .mappings()
                .first()
            )
            conn.commit()
        return {
            "token_id": row["token_id"],
            "token_hash": row["token_hash"],
            "is_new": True,
        }

    def get_token_info(self, token_hash: str) -> dict | None:
        sql = """
        SELECT t.token_id, t.store_id, t.referrer_customer_id, t.campaign_id,
               cu.name AS referrer_name, cu.phone AS referrer_phone,
               cu.referral_count,
               c.name AS campaign_name, c.referral_discount_pct,
               c.milestone_every_n, c.milestone_reward_pct, c.is_active,
               c.max_referrals_per_referrer
        FROM kirana_oltp.referral_tokens t
        JOIN kirana_oltp.customer cu ON cu.customer_id = t.referrer_customer_id
        JOIN kirana_oltp.referral_campaigns c ON c.campaign_id = t.campaign_id
        WHERE t.token_hash = :tok
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"tok": token_hash}).mappings().first()
        return dict(row) if row else None

    def process_referral(
        self,
        token_hash: str,
        new_phone: str,
        new_name: str,
        order_id: int | None = None,
    ) -> dict:
        info = self.get_token_info(token_hash)
        if not info:
            raise ValueError("Invalid or expired referral QR code")
        if not info["is_active"]:
            raise ValueError("This referral campaign is no longer active")

        store_id = info["store_id"]
        referrer_id = info["referrer_customer_id"]
        campaign_id = info["campaign_id"]
        discount_pct = float(info["referral_discount_pct"])

        # ── Referral cap check ────────────────────────────────────────────────
        max_refs = info.get("max_referrals_per_referrer", 50)
        current_count = int(info.get("referral_count", 0))
        if max_refs is not None and current_count >= int(max_refs):
            raise ValueError(
                f"{info['referrer_name']} has reached the referral limit "
                f"({int(max_refs)} referrals) for this campaign."
            )

        with self._conn() as conn:
            cust_row = (
                conn.execute(
                    text("""
                SELECT customer_id FROM kirana_oltp.customer
                WHERE phone = :phone AND store_id = :sid
            """),
                    {"phone": new_phone, "sid": store_id},
                )
                .mappings()
                .first()
            )

            if cust_row:
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.referrals (token_id, new_customer_id, order_id, discount_applied, status)
                    VALUES (:tid, :ncid, :oid, 0, 'skipped_existing')
                """),
                    {
                        "tid": info["token_id"],
                        "ncid": cust_row["customer_id"],
                        "oid": order_id,
                    },
                )
                conn.commit()
                return {
                    "status": "existing_customer",
                    "referrer_name": info["referrer_name"],
                    "campaign_name": info["campaign_name"],
                    "new_customer_id": cust_row["customer_id"],
                    "discount_pct": 0,
                    "voucher_earned": False,
                    "message": f"{new_phone} is already a customer. No referral reward.",
                }

            new_cust = (
                conn.execute(
                    text("""
                INSERT INTO kirana_oltp.customer (name, phone, store_id)
                VALUES (:name, :phone, :sid) RETURNING customer_id
            """),
                    {
                        "name": new_name or new_phone,
                        "phone": new_phone,
                        "sid": store_id,
                    },
                )
                .mappings()
                .first()
            )
            new_customer_id = new_cust["customer_id"]

            conn.execute(
                text("""
                INSERT INTO kirana_oltp.referrals (token_id, new_customer_id, order_id, discount_applied, status)
                VALUES (:tid, :ncid, :oid, :disc, 'rewarded')
            """),
                {
                    "tid": info["token_id"],
                    "ncid": new_customer_id,
                    "oid": order_id,
                    "disc": discount_pct,
                },
            )

            ref_count_row = (
                conn.execute(
                    text("""
                UPDATE kirana_oltp.customer SET referral_count = referral_count + 1
                WHERE customer_id = :cid RETURNING referral_count
            """),
                    {"cid": referrer_id},
                )
                .mappings()
                .first()
            )
            new_count = ref_count_row["referral_count"]

            milestone_n = info["milestone_every_n"]
            milestone_reward = float(info["milestone_reward_pct"])
            voucher_earned = False

            if new_count > 0 and new_count % milestone_n == 0:
                conn.execute(
                    text("""
                    INSERT INTO kirana_oltp.referral_vouchers (customer_id, store_id, campaign_id, discount_pct)
                    VALUES (:cid, :sid, :camp, :disc)
                """),
                    {
                        "cid": referrer_id,
                        "sid": store_id,
                        "camp": campaign_id,
                        "disc": milestone_reward,
                    },
                )
                voucher_earned = True

            conn.commit()

        return {
            "status": "new_customer",
            "referrer_name": info["referrer_name"],
            "campaign_name": info["campaign_name"],
            "new_customer_id": new_customer_id,
            "new_customer_name": new_name or new_phone,
            "discount_pct": discount_pct,
            "referrer_total_referrals": new_count,
            "voucher_earned": voucher_earned,
            "milestone_reward_pct": milestone_reward if voucher_earned else None,
            "message": f"New customer added! Apply {discount_pct}% discount on this order.",
        }

    def get_pending_vouchers(self, customer_id: int, store_id: int) -> list[dict]:
        sql = """
        SELECT v.*, c.name AS campaign_name
        FROM kirana_oltp.referral_vouchers v
        JOIN kirana_oltp.referral_campaigns c ON c.campaign_id = v.campaign_id
        WHERE v.customer_id = :cid AND v.store_id = :sid AND v.status = 'pending'
        ORDER BY v.earned_at DESC
        """
        with self._conn() as conn:
            rows = (
                conn.execute(text(sql), {"cid": customer_id, "sid": store_id})
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def use_voucher(self, voucher_id: int, order_id: int | None = None) -> bool:
        sql = """
        UPDATE kirana_oltp.referral_vouchers
        SET status = 'used', used_at = NOW(), used_on_order_id = :oid
        WHERE voucher_id = :vid AND status = 'pending'
        RETURNING voucher_id
        """
        with self._conn() as conn:
            row = conn.execute(text(sql), {"vid": voucher_id, "oid": order_id}).first()
            conn.commit()
        return row is not None

    def list_vouchers(self, limit: int = 100) -> list[dict]:
        sql = """
        SELECT v.*, c.name AS customer_name, s.name AS store_name, cp.name AS campaign_name
        FROM kirana_oltp.referral_vouchers v
        JOIN kirana_oltp.customer c ON v.customer_id = c.customer_id
        JOIN kirana_oltp.store s ON v.store_id = s.store_id
        JOIN kirana_oltp.referral_campaigns cp ON v.campaign_id = cp.campaign_id
        ORDER BY v.earned_at DESC
        LIMIT :lim
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"lim": limit}).mappings().all()
        result = []
        for r in rows:
            d = dict(r)
            if d["earned_at"]:
                d["earned_at"] = d["earned_at"].isoformat()
            if d["used_at"]:
                d["used_at"] = d["used_at"].isoformat()
            result.append(d)
        return result
