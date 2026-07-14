"""Read-only analytics aggregations for the director dashboard.

One function per feature domain, each with the signature
``(engine, store_id: int | None, days: int) -> dict``. ``store_id=None`` means
fleet-wide (all stores); a value scopes to that store. ``days`` is the trailing
window. Every query is a plain SELECT — nothing here writes.

The window/scoping idiom mirrors ``vision/repository.py::get_analytics``.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text

SCHEMA = "kirana_oltp"


# ── helpers ──────────────────────────────────────────────────────────────────
def _pred(col: str, store_id: Optional[int]) -> str:
    """Store scoping for a query.

    - A specific store selected → `AND <col> = :sid`.
    - Fleet-wide (store_id None) → restrict to stores flagged for the director,
      so dev/test/internal stores (include_in_director = false) are excluded.
    """
    if store_id is not None:
        return f" AND {col} = :sid "
    return (f" AND {col} IN "
            f"(SELECT store_id FROM {SCHEMA}.store WHERE include_in_director = true) ")


def _params(store_id: Optional[int], days: int) -> dict:
    return {"sid": store_id, "days": days}


def _rows(conn, sql: str, params: dict) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _one(conn, sql: str, params: dict) -> dict:
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else {}


# ── overview (headline strip) ────────────────────────────────────────────────
def overview(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    with engine.connect() as conn:
        sales = _one(conn, f"""
            SELECT COALESCE(SUM(total_amount), 0)::float8 AS revenue,
                   COUNT(*)                               AS orders,
                   COALESCE(AVG(total_amount), 0)::float8 AS avg_order_value
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day'
              {_pred('store_id', store_id)}
        """, p)

        stores = _one(conn, f"""
            SELECT COUNT(*) AS active_stores
            FROM {SCHEMA}.store
            WHERE COALESCE(is_deleted, false) = false
              {_pred('store_id', store_id)}
        """, p)

        customers = _one(conn, f"""
            SELECT COUNT(*) AS total_customers
            FROM {SCHEMA}.customer
            WHERE 1 = 1 {_pred('store_id', store_id)}
        """, p)

        subs = _one(conn, f"""
            SELECT COALESCE(SUM(monthly_price) FILTER (
                       WHERE ended_at IS NULL AND is_trial = false), 0)::float8 AS mrr,
                   COUNT(*) FILTER (
                       WHERE is_trial = true
                         AND (trial_ends_at IS NULL OR trial_ends_at > NOW()))     AS active_trials,
                   COUNT(*) FILTER (
                       WHERE ended_at IS NULL AND is_trial = false)                AS paying_stores
            FROM {SCHEMA}.subscription
            WHERE 1 = 1 {_pred('store_id', store_id)}
        """, p)

        ai = _one(conn, f"""
            SELECT COUNT(DISTINCT u.store_id) AS ai_active_stores
            FROM {SCHEMA}.ai_usage a
            JOIN {SCHEMA}.users u ON u.user_id = a.user_id
            WHERE a.usage_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {_pred('u.store_id', store_id)}
        """, p)

        udhaar = _one(conn, f"""
            SELECT COALESCE(SUM(amount - amount_paid), 0)::float8 AS udhaar_outstanding
            FROM {SCHEMA}.khata
            WHERE status <> 'written_off' {_pred('store_id', store_id)}
        """, p)

    return {**sales, **stores, **customers, **subs, **ai, **udhaar}


# ── sales & POS ──────────────────────────────────────────────────────────────
def sales(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    sp = _pred("store_id", store_id)
    with engine.connect() as conn:
        totals = _one(conn, f"""
            SELECT COALESCE(SUM(total_amount), 0)::float8 AS revenue,
                   COUNT(*)                               AS orders,
                   COALESCE(AVG(total_amount), 0)::float8 AS avg_order_value
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
        """, p)

        daily = _rows(conn, f"""
            SELECT DATE(order_date)::text            AS date,
                   COALESCE(SUM(total_amount),0)::float8 AS revenue,
                   COUNT(*)                          AS orders
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
            GROUP BY 1 ORDER BY 1
        """, p)

        channels = _rows(conn, f"""
            SELECT COALESCE(order_channel, 'walk_in') AS channel,
                   COUNT(*)                           AS orders,
                   COALESCE(SUM(total_amount),0)::float8 AS revenue
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
            GROUP BY 1 ORDER BY revenue DESC
        """, p)

        payments = _rows(conn, f"""
            SELECT COALESCE(pm.payment_method, 'unknown') AS method,
                   COUNT(*)                               AS count,
                   COALESCE(SUM(pm.amount),0)::float8     AS amount
            FROM {SCHEMA}.payments pm
            JOIN {SCHEMA}.orders o ON o.order_id = pm.order_id
            WHERE o.order_date >= NOW() - :days * INTERVAL '1 day'
              {_pred('o.store_id', store_id)}
            GROUP BY 1 ORDER BY amount DESC
        """, p)

        top_categories = _rows(conn, f"""
            SELECT c.name                                              AS category,
                   COALESCE(SUM(oi.unit_price * oi.quantity),0)::float8 AS revenue
            FROM {SCHEMA}.order_item oi
            JOIN {SCHEMA}.orders o   ON o.order_id = oi.order_id
            JOIN {SCHEMA}.product p  ON p.product_id = oi.product_id
            JOIN {SCHEMA}.category c ON c.category_id = p.category_id
            WHERE o.order_status = 'completed'
              AND o.order_date >= NOW() - :days * INTERVAL '1 day'
              {_pred('o.store_id', store_id)}
            GROUP BY 1 ORDER BY revenue DESC LIMIT 8
        """, p)

        top_products = _rows(conn, f"""
            SELECT p.name                                              AS product,
                   COALESCE(SUM(oi.unit_price * oi.quantity),0)::float8 AS revenue,
                   COALESCE(SUM(oi.quantity),0)::float8                 AS units
            FROM {SCHEMA}.order_item oi
            JOIN {SCHEMA}.orders o  ON o.order_id = oi.order_id
            JOIN {SCHEMA}.product p ON p.product_id = oi.product_id
            WHERE o.order_status = 'completed'
              AND o.order_date >= NOW() - :days * INTERVAL '1 day'
              {_pred('o.store_id', store_id)}
            GROUP BY 1 ORDER BY revenue DESC LIMIT 10
        """, p)

        margin = _one(conn, f"""
            SELECT COALESCE(SUM((oi.unit_price - oi.cost_price) * oi.quantity),0)::float8 AS gross_profit,
                   COALESCE(SUM(oi.unit_price * oi.quantity),0)::float8                   AS gross_revenue
            FROM {SCHEMA}.order_item oi
            JOIN {SCHEMA}.orders o ON o.order_id = oi.order_id
            WHERE o.order_status = 'completed'
              AND o.order_date >= NOW() - :days * INTERVAL '1 day'
              {_pred('o.store_id', store_id)}
        """, p)
        gr = margin.get("gross_revenue") or 0
        margin["gross_margin_pct"] = round(100 * margin.get("gross_profit", 0) / gr, 1) if gr else 0.0

        by_hour = _rows(conn, f"""
            SELECT EXTRACT(HOUR FROM order_date)::int  AS hour,
                   COUNT(*)                            AS orders,
                   COALESCE(SUM(total_amount),0)::float8 AS revenue
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
            GROUP BY 1 ORDER BY 1
        """, p)

    return {
        "totals": totals, "daily": daily, "channels": channels, "payments": payments,
        "top_categories": top_categories, "top_products": top_products,
        "margin": margin, "by_hour": by_hour,
    }


# ── customers / CRM ──────────────────────────────────────────────────────────
def customers(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    sp = _pred("store_id", store_id)
    with engine.connect() as conn:
        totals = _one(conn, f"""
            SELECT COUNT(*)                                                       AS total_customers,
                   COUNT(*) FILTER (
                       WHERE created_at >= NOW() - :days * INTERVAL '1 day')      AS new_customers
            FROM {SCHEMA}.customer
            WHERE 1 = 1 {sp}
        """, p)

        active = _one(conn, f"""
            SELECT COUNT(DISTINCT customer_id) AS active_customers
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND customer_id IS NOT NULL
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
        """, p)

        # New vs repeat + LTV over all-time completed orders (per customer).
        split = _one(conn, f"""
            WITH per_cust AS (
                SELECT customer_id,
                       COUNT(*)         AS orders,
                       SUM(total_amount) AS spend
                FROM {SCHEMA}.orders
                WHERE order_status = 'completed' AND customer_id IS NOT NULL {sp}
                GROUP BY customer_id
            )
            SELECT COUNT(*) FILTER (WHERE orders = 1)  AS one_time,
                   COUNT(*) FILTER (WHERE orders > 1)  AS repeat,
                   COALESCE(AVG(spend),0)::float8      AS avg_ltv,
                   COALESCE(MAX(spend),0)::float8      AS max_ltv
            FROM per_cust
        """, p)

        # Recency distribution (days since last order).
        recency = _rows(conn, f"""
            WITH last_order AS (
                SELECT customer_id, MAX(order_date) AS last_dt
                FROM {SCHEMA}.orders
                WHERE order_status = 'completed' AND customer_id IS NOT NULL {sp}
                GROUP BY customer_id
            )
            SELECT CASE
                     WHEN last_dt >= NOW() - INTERVAL '7 days'  THEN '0-7 days'
                     WHEN last_dt >= NOW() - INTERVAL '30 days' THEN '8-30 days'
                     WHEN last_dt >= NOW() - INTERVAL '90 days' THEN '31-90 days'
                     ELSE '90+ days'
                   END AS bucket,
                   COUNT(*) AS customers
            FROM last_order
            GROUP BY 1
            ORDER BY MIN(last_dt) DESC
        """, p)

        at_risk = _one(conn, f"""
            WITH last_order AS (
                SELECT customer_id, MAX(order_date) AS last_dt
                FROM {SCHEMA}.orders
                WHERE order_status = 'completed' AND customer_id IS NOT NULL {sp}
                GROUP BY customer_id
            )
            SELECT COUNT(*) AS at_risk_customers
            FROM last_order
            WHERE last_dt < NOW() - INTERVAL '60 days'
        """, p)

        top = _rows(conn, f"""
            SELECT c.name, c.phone,
                   COALESCE(SUM(o.total_amount),0)::float8 AS spend,
                   COUNT(o.order_id)                       AS orders
            FROM {SCHEMA}.customer c
            JOIN {SCHEMA}.orders o ON o.customer_id = c.customer_id
                                  AND o.order_status = 'completed'
            WHERE 1 = 1 {_pred('c.store_id', store_id)}
            GROUP BY c.customer_id, c.name, c.phone
            ORDER BY spend DESC LIMIT 10
        """, p)

        credit = _one(conn, f"""
            SELECT COALESCE(SUM(amount - amount_paid),0)::float8 AS outstanding,
                   COUNT(DISTINCT customer_id) FILTER (
                       WHERE amount > amount_paid AND status <> 'written_off') AS customers_with_credit
            FROM {SCHEMA}.khata
            WHERE status <> 'written_off' {sp}
        """, p)

    return {
        "totals": {**totals, **active}, "split": split, "recency": recency,
        "at_risk": at_risk, "top_customers": top, "credit": credit,
    }


# ── baskets / bundles ────────────────────────────────────────────────────────
def baskets(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    sp = _pred("store_id", store_id)
    with engine.connect() as conn:
        defined = _one(conn, f"""
            SELECT COUNT(*)                                    AS total_baskets,
                   COUNT(*) FILTER (WHERE is_active = true)    AS active_baskets
            FROM {SCHEMA}.basket
            WHERE 1 = 1 {sp}
        """, p)

        usage = _one(conn, f"""
            SELECT COUNT(*)                                              AS total_orders,
                   COUNT(*) FILTER (WHERE basket_id IS NOT NULL)         AS basket_orders,
                   COALESCE(SUM(basket_gross)  FILTER (WHERE basket_id IS NOT NULL),0)::float8 AS basket_revenue,
                   COALESCE(SUM(basket_savings) FILTER (WHERE basket_id IS NOT NULL),0)::float8 AS basket_savings
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
        """, p)
        to = usage.get("total_orders") or 0
        usage["attach_rate_pct"] = round(100 * (usage.get("basket_orders", 0)) / to, 1) if to else 0.0

        top = _rows(conn, f"""
            SELECT COALESCE(basket_name, 'Unnamed')     AS basket,
                   COUNT(*)                             AS orders,
                   COALESCE(SUM(total_amount),0)::float8 AS revenue,
                   COALESCE(SUM(basket_savings),0)::float8 AS savings
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND basket_id IS NOT NULL
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
            GROUP BY 1 ORDER BY revenue DESC LIMIT 8
        """, p)

    return {"defined": defined, "usage": usage, "top_baskets": top}


# ── referrals & marketing ────────────────────────────────────────────────────
def referrals(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    sp = _pred("store_id", store_id)
    with engine.connect() as conn:
        campaigns = _one(conn, f"""
            SELECT COUNT(*)                                 AS total_campaigns,
                   COUNT(*) FILTER (WHERE is_active = true) AS active_campaigns
            FROM {SCHEMA}.referral_campaigns
            WHERE 1 = 1 {sp}
        """, p)

        funnel = _one(conn, f"""
            SELECT
              (SELECT COUNT(*) FROM {SCHEMA}.referral_tokens
                 WHERE created_at >= NOW() - :days * INTERVAL '1 day' {sp})            AS tokens_issued,
              (SELECT COUNT(*) FROM {SCHEMA}.referrals r
                 JOIN {SCHEMA}.referral_tokens t ON t.token_id = r.token_id
                 WHERE r.created_at >= NOW() - :days * INTERVAL '1 day'
                 {_pred('t.store_id', store_id)})                                      AS referrals_made,
              (SELECT COUNT(*) FROM {SCHEMA}.referrals r
                 JOIN {SCHEMA}.referral_tokens t ON t.token_id = r.token_id
                 WHERE r.order_id IS NOT NULL
                   AND r.created_at >= NOW() - :days * INTERVAL '1 day'
                 {_pred('t.store_id', store_id)})                                      AS referrals_converted
        """, p)

        vouchers = _one(conn, f"""
            SELECT COUNT(*)                                        AS earned,
                   COUNT(*) FILTER (WHERE used_at IS NOT NULL)     AS used,
                   COUNT(*) FILTER (WHERE status = 'pending')      AS pending
            FROM {SCHEMA}.referral_vouchers
            WHERE earned_at >= NOW() - :days * INTERVAL '1 day' {sp}
        """, p)

        discount = _one(conn, f"""
            SELECT COALESCE(SUM(r.discount_applied),0)::float8 AS discount_given
            FROM {SCHEMA}.referrals r
            JOIN {SCHEMA}.referral_tokens t ON t.token_id = r.token_id
            WHERE r.created_at >= NOW() - :days * INTERVAL '1 day'
              {_pred('t.store_id', store_id)}
        """, p)

    return {"campaigns": campaigns, "funnel": funnel, "vouchers": vouchers, "discount": discount}


# ── AI usage & ROI ───────────────────────────────────────────────────────────
def ai(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    with engine.connect() as conn:
        by_feature = _rows(conn, f"""
            SELECT a.feature,
                   COALESCE(SUM(a.count),0)::float8   AS actions,
                   COUNT(DISTINCT a.user_id)          AS users
            FROM {SCHEMA}.ai_usage a
            JOIN {SCHEMA}.users u ON u.user_id = a.user_id
            WHERE a.usage_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {_pred('u.store_id', store_id)}
            GROUP BY 1 ORDER BY actions DESC
        """, p)

        totals = _one(conn, f"""
            SELECT COALESCE(SUM(a.count),0)::float8     AS total_actions,
                   COUNT(DISTINCT a.user_id)            AS active_users,
                   COUNT(DISTINCT u.store_id)           AS active_stores
            FROM {SCHEMA}.ai_usage a
            JOIN {SCHEMA}.users u ON u.user_id = a.user_id
            WHERE a.usage_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {_pred('u.store_id', store_id)}
        """, p)

        total_users = _one(conn, f"""
            SELECT COUNT(*) AS total_users
            FROM {SCHEMA}.users
            WHERE COALESCE(is_deleted, false) = false
              AND role = 'store_owner' {_pred('store_id', store_id)}
        """, p)
        tu = total_users.get("total_users") or 0
        totals["adoption_pct"] = round(100 * (totals.get("active_users", 0)) / tu, 1) if tu else 0.0

        daily = _rows(conn, f"""
            SELECT a.usage_date::text              AS date,
                   COALESCE(SUM(a.count),0)::float8 AS actions
            FROM {SCHEMA}.ai_usage a
            JOIN {SCHEMA}.users u ON u.user_id = a.user_id
            WHERE a.usage_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
              {_pred('u.store_id', store_id)}
            GROUP BY 1 ORDER BY 1
        """, p)

        credits = _rows(conn, f"""
            SELECT c.feature,
                   COALESCE(SUM(c.balance),0)::float8 AS balance
            FROM {SCHEMA}.ai_credits c
            JOIN {SCHEMA}.users u ON u.user_id = c.user_id
            WHERE 1 = 1 {_pred('u.store_id', store_id)}
            GROUP BY 1 ORDER BY balance DESC
        """, p)

    return {"totals": totals, "by_feature": by_feature, "daily": daily, "credits": credits}


# ── subscriptions & trials ───────────────────────────────────────────────────
def subscriptions(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    sp = _pred("store_id", store_id)
    with engine.connect() as conn:
        by_tier = _rows(conn, f"""
            SELECT tier,
                   COUNT(*) AS stores,
                   COALESCE(SUM(monthly_price),0)::float8 AS mrr
            FROM {SCHEMA}.subscription
            WHERE ended_at IS NULL AND is_trial = false {sp}
            GROUP BY tier ORDER BY mrr DESC
        """, p)

        totals = _one(conn, f"""
            SELECT COALESCE(SUM(monthly_price) FILTER (
                       WHERE ended_at IS NULL AND is_trial = false),0)::float8 AS mrr,
                   COUNT(*) FILTER (WHERE ended_at IS NULL AND is_trial = false) AS paying_stores,
                   COUNT(*) FILTER (WHERE is_trial = true
                                      AND (trial_ends_at IS NULL OR trial_ends_at > NOW())) AS active_trials,
                   COUNT(*) FILTER (WHERE ended_at >= NOW() - :days * INTERVAL '1 day')      AS churned,
                   COALESCE(SUM(savings_to_date),0)::float8                                  AS savings_to_date
            FROM {SCHEMA}.subscription
            WHERE 1 = 1 {sp}
        """, p)
        # Snapshot conversion: paying vs (paying + active trials).
        denom = (totals.get("paying_stores", 0) or 0) + (totals.get("active_trials", 0) or 0)
        totals["trial_conversion_pct"] = round(
            100 * (totals.get("paying_stores", 0)) / denom, 1) if denom else 0.0

    return {"totals": totals, "by_tier": by_tier}


# ── app engagement ───────────────────────────────────────────────────────────
def engagement(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    join = f"JOIN {SCHEMA}.users u ON u.user_id = a.user_id"
    up = _pred("u.store_id", store_id)
    with engine.connect() as conn:
        active = _one(conn, f"""
            SELECT COUNT(DISTINCT a.user_id) FILTER (WHERE a.created_at >= NOW() - INTERVAL '1 day')  AS dau,
                   COUNT(DISTINCT a.user_id) FILTER (WHERE a.created_at >= NOW() - INTERVAL '7 days') AS wau,
                   COUNT(DISTINCT a.user_id) FILTER (WHERE a.created_at >= NOW() - INTERVAL '30 days') AS mau
            FROM {SCHEMA}.app_activity a {join}
            WHERE 1 = 1 {up}
        """, p)

        totals = _one(conn, f"""
            SELECT COUNT(*)                              AS events,
                   COALESCE(AVG(a.duration_sec),0)::float8 AS avg_duration_sec
            FROM {SCHEMA}.app_activity a {join}
            WHERE a.created_at >= NOW() - :days * INTERVAL '1 day' {up}
        """, p)

        by_event = _rows(conn, f"""
            SELECT a.event,
                   COUNT(*)                              AS count,
                   COALESCE(SUM(a.duration_sec),0)::float8 AS total_seconds
            FROM {SCHEMA}.app_activity a {join}
            WHERE a.created_at >= NOW() - :days * INTERVAL '1 day' {up}
            GROUP BY 1 ORDER BY count DESC
        """, p)

        daily = _rows(conn, f"""
            SELECT DATE(a.created_at)::text          AS date,
                   COUNT(DISTINCT a.user_id)         AS active_users,
                   COUNT(*)                          AS events
            FROM {SCHEMA}.app_activity a {join}
            WHERE a.created_at >= NOW() - :days * INTERVAL '1 day' {up}
            GROUP BY 1 ORDER BY 1
        """, p)

    return {"active": active, "totals": totals, "by_event": by_event, "daily": daily}


# ── footfall & schemes ───────────────────────────────────────────────────────
def footfall(engine, store_id: Optional[int], days: int) -> dict:
    p = _params(store_id, days)
    sp = _pred("store_id", store_id)
    with engine.connect() as conn:
        totals = _one(conn, f"""
            SELECT COALESCE(SUM(visitors),0)::float8 AS total_visitors
            FROM {SCHEMA}.footfall
            WHERE ts >= NOW() - :days * INTERVAL '1 day' {sp}
        """, p)

        orders = _one(conn, f"""
            SELECT COUNT(*) AS orders
            FROM {SCHEMA}.orders
            WHERE order_status = 'completed'
              AND order_date >= NOW() - :days * INTERVAL '1 day' {sp}
        """, p)
        v = totals.get("total_visitors") or 0
        totals["orders"] = orders.get("orders", 0)
        totals["conversion_pct"] = round(100 * (orders.get("orders", 0)) / v, 1) if v else 0.0

        daily = _rows(conn, f"""
            SELECT DATE(ts)::text                AS date,
                   COALESCE(SUM(visitors),0)::float8 AS visitors
            FROM {SCHEMA}.footfall
            WHERE ts >= NOW() - :days * INTERVAL '1 day' {sp}
            GROUP BY 1 ORDER BY 1
        """, p)

        by_hour = _rows(conn, f"""
            SELECT hour,
                   COALESCE(SUM(visitors),0)::float8 AS visitors
            FROM {SCHEMA}.footfall
            WHERE ts >= NOW() - :days * INTERVAL '1 day' {sp}
            GROUP BY 1 ORDER BY 1
        """, p)

        schemes = _one(conn, f"""
            SELECT COUNT(*)                                AS claims,
                   COALESCE(SUM(amount_saved),0)::float8   AS amount_saved
            FROM {SCHEMA}.scheme_claim
            WHERE claim_date >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day' {sp}
        """, p)

    return {"totals": totals, "daily": daily, "by_hour": by_hour, "schemes": schemes}


# ── stores (for the filter dropdown) ─────────────────────────────────────────
def stores(engine) -> list[dict]:
    # Only stores flagged for the director appear in the filter dropdown.
    with engine.connect() as conn:
        return _rows(conn, f"""
            SELECT store_id, name
            FROM {SCHEMA}.store
            WHERE COALESCE(is_deleted, false) = false
              AND include_in_director = true
            ORDER BY name
        """, {})


def included_store_ids(engine) -> list[int]:
    """Store ids flagged for the director — used to scope the Vision aggregation."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT store_id FROM {SCHEMA}.store "
            f"WHERE COALESCE(is_deleted, false) = false AND include_in_director = true"
        )).scalars().all()
    return [int(x) for x in rows]
