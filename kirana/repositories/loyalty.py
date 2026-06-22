from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")

_DEFAULT_CONFIG = {
    "is_active": False,
    "points_per_100": 1,
    "redeem_paise_per_point": 100,
    "silver_threshold": 500,
    "gold_threshold": 2000,
}


class LoyaltyRepositoryMixin:
    """Module M1 — Loyalty & Offers (points ledger, tiers, coupons, occasions).

    Opt-in per store via loyalty_config.is_active, so stores that don't enable it
    are unaffected. Points balance is SUM(loyalty_transaction.points).
    """

    # ── Config ────────────────────────────────────────────────────────────────
    def get_loyalty_config(self, store_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                SELECT is_active, points_per_100, redeem_paise_per_point,
                       silver_threshold, gold_threshold
                FROM kirana_oltp.loyalty_config WHERE store_id = :sid
                """),
                {"sid": store_id},
            ).mappings().first()
        cfg = dict(row) if row else dict(_DEFAULT_CONFIG)
        cfg["store_id"] = store_id
        return cfg

    def upsert_loyalty_config(self, store_id: int, **fields) -> dict:
        allowed = {
            "is_active", "points_per_100", "redeem_paise_per_point",
            "silver_threshold", "gold_threshold",
        }
        merged = {**_DEFAULT_CONFIG, **self.get_loyalty_config(store_id)}
        for k, v in fields.items():
            if k in allowed and v is not None:
                merged[k] = v
        with self._conn() as conn:
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.loyalty_config
                    (store_id, is_active, points_per_100, redeem_paise_per_point,
                     silver_threshold, gold_threshold, updated_at)
                VALUES (:sid, :is_active, :ppr, :rpp, :silver, :gold, NOW())
                ON CONFLICT (store_id) DO UPDATE SET
                    is_active = EXCLUDED.is_active,
                    points_per_100 = EXCLUDED.points_per_100,
                    redeem_paise_per_point = EXCLUDED.redeem_paise_per_point,
                    silver_threshold = EXCLUDED.silver_threshold,
                    gold_threshold = EXCLUDED.gold_threshold,
                    updated_at = NOW()
                """),
                {
                    "sid": store_id,
                    "is_active": merged["is_active"],
                    "ppr": merged["points_per_100"],
                    "rpp": merged["redeem_paise_per_point"],
                    "silver": merged["silver_threshold"],
                    "gold": merged["gold_threshold"],
                },
            )
            conn.commit()
        return self.get_loyalty_config(store_id)

    # ── Points ────────────────────────────────────────────────────────────────
    def get_customer_points(self, customer_id: int) -> float:
        with self._conn() as conn:
            bal = conn.execute(
                text("SELECT COALESCE(SUM(points), 0) FROM kirana_oltp.loyalty_transaction WHERE customer_id = :cid"),
                {"cid": customer_id},
            ).scalar()
        return float(bal or 0)

    def tier_for(self, points: float, config: dict | None = None) -> str:
        cfg = config or _DEFAULT_CONFIG
        if points >= cfg["gold_threshold"]:
            return "gold"
        if points >= cfg["silver_threshold"]:
            return "silver"
        return "bronze"

    def _record_txn(self, store_id, customer_id, points, kind, order_id=None, note=None) -> None:
        with self._conn() as conn:
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.loyalty_transaction
                    (store_id, customer_id, order_id, points, kind, note)
                VALUES (:sid, :cid, :oid, :pts, :kind, :note)
                """),
                {"sid": store_id, "cid": customer_id, "oid": order_id,
                 "pts": points, "kind": kind, "note": note},
            )
            conn.commit()

    def earn_points(self, store_id: int, customer_id: int, order_id: int | None, order_amount: float) -> float:
        """Award points for a purchase per the store's config. No-op if inactive."""
        cfg = self.get_loyalty_config(store_id)
        if not cfg["is_active"] or order_amount <= 0:
            return 0.0
        pts = round(order_amount / 100.0 * float(cfg["points_per_100"]), 2)
        if pts <= 0:
            return 0.0
        self._record_txn(store_id, customer_id, pts, "earn", order_id, "Purchase")
        return pts

    def redeem_points(self, store_id: int, customer_id: int, points: float,
                      order_id: int | None = None, note: str | None = None) -> dict:
        if points <= 0:
            raise ValueError("Points to redeem must be positive")
        balance = self.get_customer_points(customer_id)
        if points > balance:
            raise ValueError(f"Insufficient points (have {balance:g}, need {points:g})")
        cfg = self.get_loyalty_config(store_id)
        value = round(points * cfg["redeem_paise_per_point"] / 100.0, 2)
        self._record_txn(store_id, customer_id, -points, "redeem", order_id,
                         note or "Redeemed at billing")
        return {"redeemed_points": points, "value": value,
                "balance": self.get_customer_points(customer_id)}

    def get_customer_loyalty(self, store_id: int, customer_id: int) -> dict:
        cfg = self.get_loyalty_config(store_id)
        balance = self.get_customer_points(customer_id)
        with self._conn() as conn:
            rows = conn.execute(
                text("""
                SELECT points, kind, note, order_id, created_at
                FROM kirana_oltp.loyalty_transaction
                WHERE customer_id = :cid ORDER BY created_at DESC LIMIT 50
                """),
                {"cid": customer_id},
            ).mappings().all()
        return {
            "customer_id": customer_id,
            "points": balance,
            "tier": self.tier_for(balance, cfg),
            "redeem_value": round(balance * cfg["redeem_paise_per_point"] / 100.0, 2),
            "history": [dict(r) for r in rows],
        }

    # ── Coupons ───────────────────────────────────────────────────────────────
    def list_coupons(self, store_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                text("""
                SELECT coupon_id, code, discount_type, value, min_order, max_discount,
                       valid_from, valid_to, usage_limit, used_count, is_active
                FROM kirana_oltp.coupon WHERE store_id = :sid ORDER BY coupon_id DESC
                """),
                {"sid": store_id},
            ).mappings().all()
        return [dict(r) for r in rows]

    def create_coupon(self, store_id: int, code: str, discount_type: str, value: float,
                      min_order: float = 0, max_discount: float | None = None,
                      valid_from=None, valid_to=None, usage_limit: int | None = None) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                INSERT INTO kirana_oltp.coupon
                    (store_id, code, discount_type, value, min_order, max_discount,
                     valid_from, valid_to, usage_limit)
                VALUES (:sid, :code, :dt, :val, :mino, :maxd, :vf, :vt, :ul)
                RETURNING coupon_id, code, discount_type, value, min_order, max_discount,
                          valid_from, valid_to, usage_limit, used_count, is_active
                """),
                {"sid": store_id, "code": code.strip().upper(), "dt": discount_type,
                 "val": value, "mino": min_order, "maxd": max_discount,
                 "vf": valid_from, "vt": valid_to, "ul": usage_limit},
            ).mappings().first()
            conn.commit()
        return dict(row)

    def set_coupon_active(self, coupon_id: int, store_id: int, is_active: bool) -> bool:
        with self._conn() as conn:
            n = conn.execute(
                text("UPDATE kirana_oltp.coupon SET is_active = :a WHERE coupon_id = :cid AND store_id = :sid"),
                {"a": is_active, "cid": coupon_id, "sid": store_id},
            ).rowcount
            conn.commit()
        return n > 0

    def validate_coupon(self, store_id: int, code: str, order_amount: float) -> dict:
        with self._conn() as conn:
            c = conn.execute(
                text("""
                SELECT coupon_id, discount_type, value, min_order, max_discount,
                       valid_from, valid_to, usage_limit, used_count, is_active
                FROM kirana_oltp.coupon
                WHERE store_id = :sid AND UPPER(code) = UPPER(:code)
                """),
                {"sid": store_id, "code": code.strip()},
            ).mappings().first()
        if not c:
            return {"valid": False, "reason": "Coupon not found", "discount": 0}
        if not c["is_active"]:
            return {"valid": False, "reason": "Coupon inactive", "discount": 0}
        from datetime import date
        today = date.today()
        if c["valid_from"] and today < c["valid_from"]:
            return {"valid": False, "reason": "Coupon not yet valid", "discount": 0}
        if c["valid_to"] and today > c["valid_to"]:
            return {"valid": False, "reason": "Coupon expired", "discount": 0}
        if c["usage_limit"] is not None and c["used_count"] >= c["usage_limit"]:
            return {"valid": False, "reason": "Coupon usage limit reached", "discount": 0}
        if order_amount < float(c["min_order"] or 0):
            return {"valid": False, "reason": f"Minimum order ₹{c['min_order']:g}", "discount": 0}
        if c["discount_type"] == "percent":
            discount = order_amount * float(c["value"]) / 100.0
            if c["max_discount"]:
                discount = min(discount, float(c["max_discount"]))
        else:
            discount = float(c["value"])
        discount = round(min(discount, order_amount), 2)
        return {"valid": True, "reason": "OK", "discount": discount, "coupon_id": c["coupon_id"]}

    def redeem_coupon(self, coupon_id: int, store_id: int, discount: float,
                      order_id: int | None = None, customer_id: int | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                text("""
                INSERT INTO kirana_oltp.coupon_redemption
                    (coupon_id, store_id, order_id, customer_id, discount)
                VALUES (:cid, :sid, :oid, :cust, :disc)
                """),
                {"cid": coupon_id, "sid": store_id, "oid": order_id,
                 "cust": customer_id, "disc": discount},
            )
            conn.execute(
                text("UPDATE kirana_oltp.coupon SET used_count = used_count + 1 WHERE coupon_id = :cid"),
                {"cid": coupon_id},
            )
            conn.commit()

    # ── Admin overview ──────────────────────────────────────────────────────────
    def loyalty_admin_overview(self) -> list[dict]:
        """Admin: per-store loyalty snapshot — adoption, rates, members, liability,
        coupon counts. One row per store that has a loyalty_config row."""
        with self._conn() as conn:
            rows = conn.execute(
                text("""
                SELECT
                    s.store_id, s.name AS store_name,
                    COALESCE(lc.is_active, FALSE)         AS is_active,
                    COALESCE(lc.points_per_100, 0)        AS points_per_100,
                    COALESCE(lc.redeem_paise_per_point, 0) AS redeem_paise_per_point,
                    COALESCE(lc.silver_threshold, 0)      AS silver_threshold,
                    COALESCE(lc.gold_threshold, 0)        AS gold_threshold,
                    (SELECT COUNT(DISTINCT lt.customer_id)
                       FROM kirana_oltp.loyalty_transaction lt
                       WHERE lt.store_id = s.store_id)    AS members,
                    (SELECT COALESCE(SUM(lt.points), 0)
                       FROM kirana_oltp.loyalty_transaction lt
                       WHERE lt.store_id = s.store_id)    AS points_outstanding,
                    (SELECT COUNT(*) FROM kirana_oltp.coupon c
                       WHERE c.store_id = s.store_id)     AS coupons_total,
                    (SELECT COUNT(*) FROM kirana_oltp.coupon c
                       WHERE c.store_id = s.store_id AND c.is_active) AS coupons_active
                FROM kirana_oltp.store s
                JOIN kirana_oltp.loyalty_config lc ON lc.store_id = s.store_id
                WHERE NOT s.is_deleted
                ORDER BY is_active DESC, members DESC
                """)
            ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["liability"] = round(
                float(d["points_outstanding"] or 0)
                * float(d["redeem_paise_per_point"] or 0) / 100.0, 2)
            out.append(d)
        return out

    # ── Occasions (birthday / anniversary offers) ──────────────────────────────
    def offers_due(self, store_id: int, days: int = 7) -> list[dict]:
        """Customers whose birthday or anniversary falls in the next [days]."""
        with self._conn() as conn:
            rows = conn.execute(
                text("""
                SELECT customer_id, name, phone, birthday, anniversary
                FROM kirana_oltp.customer
                WHERE store_id = :sid AND is_deleted = FALSE
                  AND (
                    (birthday IS NOT NULL AND
                     TO_CHAR(birthday, 'MMDD') BETWEEN TO_CHAR(CURRENT_DATE, 'MMDD')
                                                  AND TO_CHAR(CURRENT_DATE + (:days || ' days')::interval, 'MMDD'))
                    OR
                    (anniversary IS NOT NULL AND
                     TO_CHAR(anniversary, 'MMDD') BETWEEN TO_CHAR(CURRENT_DATE, 'MMDD')
                                                     AND TO_CHAR(CURRENT_DATE + (:days || ' days')::interval, 'MMDD'))
                  )
                ORDER BY name
                """),
                {"sid": store_id, "days": days},
            ).mappings().all()
        return [dict(r) for r in rows]
