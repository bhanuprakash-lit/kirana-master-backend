"""
KPI Calculator — pure SQL analytics against lit_db.
Each function returns a dict that maps directly to the corresponding KPI schema.
All queries are optimised CTEs; no N+1 queries.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger("kpis.calculator")

_HOLDING_RATE = 0.20   # 20% annual holding cost


def _period(days: int) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=days), today


def _prev_period(days: int) -> tuple[date, date]:
    end   = date.today() - timedelta(days=days)
    start = end - timedelta(days=days)
    return start, end


def _row(engine, sql: str, params: dict) -> dict:
    with engine.connect() as conn:
        r = conn.execute(text(sql), params).mappings().first()
    return dict(r) if r else {}


def _rows(engine, sql: str, params: dict) -> list[dict]:
    with engine.connect() as conn:
        rs = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rs]


def _scalar(engine, sql: str, params: dict):
    with engine.connect() as conn:
        r = conn.execute(text(sql), params).scalar()
    return r


def _store_name(engine, store_id: int) -> str:
    r = _scalar(engine,
        "SELECT name FROM kirana_oltp.store WHERE store_id = :sid",
        {"sid": store_id})
    return r or f"Store {store_id}"


def _trend(current: float | None, previous: float | None, higher_is_better: bool = True) -> dict:
    if current is None or previous is None or previous == 0:
        return {"direction": "stable", "pct_change": None,
                "current_value": current, "previous_value": previous,
                "interpretation": "Insufficient data for trend"}
    pct = round((current - previous) / abs(previous) * 100, 2)
    if abs(pct) < 1:
        direction = "stable"
    elif (pct > 0 and higher_is_better) or (pct < 0 and not higher_is_better):
        direction = "up"
    else:
        direction = "down"
    interp = {
        "up":     "Improving — moving towards target.",
        "down":   "Declining — action needed.",
        "stable": "No significant change.",
    }[direction]
    return {"direction": direction, "pct_change": pct,
            "current_value": current, "previous_value": previous,
            "interpretation": interp}


# ── 1. Repeat Customer Frequency ──────────────────────────────────────────────

def calc_repeat_customer(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    WITH all_orders AS (
        SELECT customer_id, order_date::date AS od, total_amount
        FROM kirana_oltp.orders
        WHERE store_id = :sid AND order_status = 'completed'
          AND customer_id IS NOT NULL
    ),
    intervals AS (
        SELECT customer_id, od, total_amount,
               LAG(od) OVER (PARTITION BY customer_id ORDER BY od) AS prev_od
        FROM all_orders
    ),
    stats AS (
        SELECT customer_id,
               COUNT(*)                     AS order_count,
               MAX(od)                      AS last_visit,
               AVG(total_amount)            AS avg_basket,
               AVG(od - prev_od)            AS avg_interval
        FROM intervals
        WHERE od BETWEEN :p_from AND :p_to
        GROUP BY customer_id
    ),
    agg AS (
        SELECT AVG(avg_interval) AS global_avg_interval FROM stats
    )
    SELECT
        COUNT(DISTINCT s.customer_id)                                              AS total,
        COUNT(DISTINCT s.customer_id) FILTER(WHERE s.order_count > 1)             AS repeat_cust,
        ROUND(AVG(s.avg_interval)::numeric, 1)                                    AS avg_interval,
        ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY s.avg_interval))::numeric, 1) AS med_interval,
        COUNT(*) FILTER(WHERE s.last_visit < :p_to - (2 * COALESCE(a.global_avg_interval,30))::int) AS at_risk,
        COUNT(*) FILTER(WHERE s.last_visit < :p_to - 60)                          AS churned,
        ROUND(COUNT(DISTINCT s.customer_id) FILTER(WHERE s.order_count > 1) * 100.0
              / NULLIF(COUNT(DISTINCT s.customer_id), 0), 1)                      AS repeat_rate
    FROM stats s CROSS JOIN agg a
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    # previous period for trend
    prev_sql = """
    SELECT COUNT(DISTINCT customer_id) FILTER(WHERE order_count>1) * 100.0
           / NULLIF(COUNT(DISTINCT customer_id),0) AS repeat_rate
    FROM (
        SELECT customer_id, COUNT(*) AS order_count
        FROM kirana_oltp.orders
        WHERE store_id=:sid AND order_status='completed'
          AND customer_id IS NOT NULL
          AND order_date BETWEEN :pp_from AND :pp_to
        GROUP BY customer_id
    ) x
    """
    prev_rate = _scalar(engine, prev_sql, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    segments_sql = """
    WITH cust_stats AS (
        SELECT customer_id,
               COUNT(*) AS orders,
               AVG(total_amount) AS avg_basket,
               AVG((od - prev_od)) AS avg_interval
        FROM (
            SELECT customer_id, order_date::date AS od, total_amount,
                   LAG(order_date::date) OVER (PARTITION BY customer_id ORDER BY order_date) AS prev_od
            FROM kirana_oltp.orders
            WHERE store_id=:sid AND order_status='completed'
              AND order_date BETWEEN :p_from AND :p_to
        ) x
        GROUP BY customer_id
    )
    SELECT
        CASE
            WHEN orders >= 5 THEN 'loyal'
            WHEN orders >= 3 THEN 'regular'
            WHEN orders >= 2 THEN 'occasional'
            ELSE 'one_time'
        END AS label,
        COUNT(*) AS customer_count,
        ROUND(AVG(avg_basket)::numeric, 2) AS avg_basket,
        ROUND(AVG(avg_interval)::numeric, 1) AS avg_interval
    FROM cust_stats GROUP BY 1 ORDER BY customer_count DESC
    """
    segs = _rows(engine, segments_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    cur_rate = float(r.get("repeat_rate") or 0)
    return {
        "total_customers": int(r.get("total") or 0),
        "repeat_customer_count": int(r.get("repeat_cust") or 0),
        "repeat_rate_pct": cur_rate,
        "avg_visit_interval_days": float(r.get("avg_interval") or 0),
        "median_visit_interval_days": float(r.get("med_interval") or 0),
        "at_risk_count": int(r.get("at_risk") or 0),
        "churned_count": int(r.get("churned") or 0),
        "trend": _trend(cur_rate, float(prev_rate or 0)),
        "segments": [
            {"label": s["label"], "customer_count": int(s["customer_count"]),
             "avg_basket": float(s["avg_basket"] or 0),
             "avg_visit_interval_days": float(s["avg_interval"] or 0)}
            for s in segs
        ],
    }


# ── 2. Category Mix Optimization ──────────────────────────────────────────────

def calc_category_mix(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH sales AS (
        SELECT p.category_id, c.name AS cat_name,
               SUM(oi.quantity * oi.unit_price)               AS revenue,
               SUM(oi.quantity * (oi.unit_price - oi.cost_price)) AS profit,
               SUM(oi.quantity) / :days                        AS avg_units_per_day
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        JOIN kirana_oltp.product p    ON oi.product_id = p.product_id
        JOIN kirana_oltp.category c   ON p.category_id = c.category_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
        GROUP BY p.category_id, c.name
    ),
    totals AS (SELECT SUM(revenue) AS total_rev, SUM(profit) AS total_profit FROM sales)
    SELECT s.category_id, s.cat_name,
           ROUND(s.revenue::numeric, 2)                                   AS revenue,
           ROUND((s.revenue * 100.0 / NULLIF(t.total_rev, 0))::numeric, 2)          AS revenue_share_pct,
           ROUND((s.profit  * 100.0 / NULLIF(s.revenue, 0))::numeric, 2)            AS margin_pct,
           ROUND(s.avg_units_per_day::numeric, 2)                                    AS avg_units_per_day,
           ROUND(t.total_rev::numeric, 2)                                            AS total_revenue,
           ROUND((t.total_profit * 100.0 / NULLIF(t.total_rev, 0))::numeric, 2)     AS overall_margin
    FROM sales s CROSS JOIN totals t
    ORDER BY revenue DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to, "days": days})
    if not rows:
        return {"total_revenue": 0, "overall_margin_pct": 0, "category_count": 0,
                "mix_score": 0, "categories": [], "top_opportunity": "No data", "trend": _trend(None, None)}

    total_rev     = float(rows[0].get("total_revenue") or 0)
    overall_margin = float(rows[0].get("overall_margin") or 0)

    # BCG quadrant: high share + high margin = star, etc.
    median_share  = sorted(float(r.get("revenue_share_pct") or 0) for r in rows)[len(rows)//2]
    median_margin = sorted(float(r.get("margin_pct") or 0) for r in rows)[len(rows)//2]

    def _bcg(rev_share, margin):
        hs = float(rev_share) >= median_share
        hm = float(margin) >= median_margin
        return "star" if hs and hm else ("cash_cow" if hs else ("question_mark" if hm else "dog"))

    categories = []
    for r in rows:
        categories.append({
            "category_id": int(r["category_id"]),
            "category_name": r["cat_name"],
            "revenue": float(r.get("revenue") or 0),
            "revenue_share_pct": float(r.get("revenue_share_pct") or 0),
            "margin_pct": float(r.get("margin_pct") or 0),
            "avg_units_per_day": float(r.get("avg_units_per_day") or 0),
            "bcg_quadrant": _bcg(r.get("revenue_share_pct"), r.get("margin_pct")),
        })

    stars = [c for c in categories if c["bcg_quadrant"] == "star"]
    dogs  = [c for c in categories if c["bcg_quadrant"] == "dog"]
    qm    = [c for c in categories if c["bcg_quadrant"] == "question_mark"]

    # mix_score: HHI-based concentration (lower = more diverse = better)
    hhi = sum((c["revenue_share_pct"] / 100) ** 2 for c in categories)
    mix_score = round(max(0, 100 - hhi * 100), 1)

    opp = ""
    if qm:
        opp = f"Push {qm[0]['category_name']} — high margin but low share. Increase shelf visibility."
    elif dogs:
        opp = f"Review {dogs[0]['category_name']} — low share and margin. Consider reducing assortment."
    else:
        opp = "Good mix. Focus on keeping star categories well-stocked."

    # Trend: compare overall margin to previous period
    pm_sql = """
    SELECT SUM(oi.quantity*(oi.unit_price-oi.cost_price))*100/NULLIF(SUM(oi.quantity*oi.unit_price),0)
    FROM kirana_oltp.orders o JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to
    """
    pp_from, pp_to = _prev_period(days)
    prev_margin = _scalar(engine, pm_sql, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    return {
        "total_revenue": total_rev,
        "overall_margin_pct": overall_margin,
        "category_count": len(rows),
        "mix_score": mix_score,
        "categories": categories,
        "top_opportunity": opp,
        "trend": _trend(overall_margin, float(prev_margin or 0)),
    }


# ── 3. Digital Payment Adoption ───────────────────────────────────────────────

def calc_digital_payment(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT p.payment_method,
           COUNT(*)            AS txn_count,
           SUM(p.amount)       AS total_amount
    FROM kirana_oltp.payments p
    JOIN kirana_oltp.orders o ON p.order_id = o.order_id
    WHERE o.store_id = :sid
      AND o.order_date BETWEEN :p_from AND :p_to
    GROUP BY p.payment_method
    ORDER BY txn_count DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_txn = sum(int(r["txn_count"]) for r in rows)
    total_amt = sum(float(r["total_amount"] or 0) for r in rows)
    digital_txn = sum(int(r["txn_count"]) for r in rows if r["payment_method"] in ("upi", "card"))
    digital_pct = round(digital_txn * 100.0 / max(total_txn, 1), 1)
    cash_pct    = round(100 - digital_pct, 1)

    by_method = [
        {"method": r["payment_method"],
         "count": int(r["txn_count"]),
         "amount": round(float(r["total_amount"] or 0), 2),
         "share_pct": round(int(r["txn_count"]) * 100.0 / max(total_txn, 1), 1)}
        for r in rows
    ]

    weekly_sql = """
    SELECT DATE_TRUNC('week', o.order_date)::date AS week,
           COUNT(*) FILTER(WHERE p.payment_method IN ('upi','card')) AS digital,
           COUNT(*) AS total,
           ROUND(COUNT(*) FILTER(WHERE p.payment_method IN ('upi','card'))*100.0/NULLIF(COUNT(*),0), 1) AS digital_pct
    FROM kirana_oltp.orders o JOIN kirana_oltp.payments p ON o.order_id=p.order_id
    WHERE o.store_id=:sid AND o.order_date BETWEEN :p_from AND :p_to
    GROUP BY 1 ORDER BY 1
    """
    weekly = _rows(engine, weekly_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    pp_from, pp_to = _prev_period(days)
    prev_pct = _scalar(engine, """
    SELECT COUNT(*) FILTER(WHERE p.payment_method IN ('upi','card'))*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.payments p JOIN kirana_oltp.orders o ON o.order_id=p.order_id
    WHERE o.store_id=:sid AND o.order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    return {
        "digital_pct": digital_pct,
        "cash_pct": cash_pct,
        "total_transactions": total_txn,
        "total_amount": round(total_amt, 2),
        "by_method": by_method,
        "weekly_trend": [{"week": str(w["week"]), "digital": int(w["digital"]),
                          "total": int(w["total"]), "digital_pct": float(w["digital_pct"] or 0)}
                         for w in weekly],
        "trend": _trend(digital_pct, float(prev_pct or 0)),
    }


# ── 4. New Product Trial Success ──────────────────────────────────────────────

def calc_new_product_trial(engine, store_id: int, trial_days: int = 30) -> dict:
    """
    Uses first_sale_date as launch proxy (since all products share created_at).
    A product is 'new' if its first sale was within the last trial_days window.
    """
    sql = """
    WITH first_sale AS (
        SELECT oi.product_id,
               MIN(o.order_date::date) AS first_sale_date
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
        GROUP BY oi.product_id
    ),
    trial AS (
        SELECT fs.product_id,
               p.name AS product_name,
               c.name AS category_name,
               fs.first_sale_date,
               (CURRENT_DATE - fs.first_sale_date) AS days_since_launch,
               COALESCE(SUM(oi.quantity), 0)                                   AS units_30d,
               COALESCE(SUM(oi.quantity * oi.unit_price), 0)                   AS revenue_30d
        FROM first_sale fs
        JOIN kirana_oltp.product p  ON fs.product_id = p.product_id
        JOIN kirana_oltp.category c ON p.category_id = c.category_id
        LEFT JOIN kirana_oltp.order_item oi ON oi.product_id = fs.product_id
        LEFT JOIN kirana_oltp.orders o
            ON oi.order_id = o.order_id
           AND o.store_id = :sid
           AND o.order_date::date BETWEEN fs.first_sale_date
                                      AND fs.first_sale_date + :trial_days
        WHERE (CURRENT_DATE - fs.first_sale_date) <= :trial_days * 2
        GROUP BY fs.product_id, p.name, c.name, fs.first_sale_date
    )
    SELECT * FROM trial ORDER BY revenue_30d DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "trial_days": trial_days})
    if not rows:
        return {"trial_window_days": trial_days, "new_products_count": 0,
                "success_rate_pct": 0, "avg_units_sold_30d": 0, "products": [],
                "trend": _trend(None, None)}

    # Label success: top 33% units = hit, bottom 33% = slow
    all_units = sorted(int(r["units_30d"]) for r in rows)
    p33 = all_units[len(all_units)//3] if all_units else 0
    p67 = all_units[2*len(all_units)//3] if all_units else 0

    products = []
    for r in rows:
        u = int(r["units_30d"])
        label = "hit" if u >= p67 else ("average" if u >= p33 else "slow")
        products.append({
            "product_id": int(r["product_id"]),
            "product_name": r["product_name"],
            "category_name": r["category_name"],
            "days_since_launch": int(r["days_since_launch"]),
            "units_sold_30d": u,
            "revenue_30d": round(float(r["revenue_30d"] or 0), 2),
            "success_label": label,
            "predicted_success_prob": None,
        })

    hits        = sum(1 for p in products if p["success_label"] == "hit")
    success_pct = round(hits * 100.0 / max(len(products), 1), 1)
    avg_units   = round(sum(p["units_sold_30d"] for p in products) / max(len(products), 1), 1)

    return {
        "trial_window_days": trial_days,
        "new_products_count": len(products),
        "success_rate_pct": success_pct,
        "avg_units_sold_30d": avg_units,
        "products": products[:20],
        "trend": _trend(success_pct, None),
    }


# ── 5. Cross-Category Basket ──────────────────────────────────────────────────

def calc_cross_category_basket(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)

    summary_sql = """
    WITH order_cats AS (
        SELECT o.order_id,
               COUNT(DISTINCT p.category_id) AS cat_count
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        JOIN kirana_oltp.product p     ON oi.product_id = p.product_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
        GROUP BY o.order_id
    )
    SELECT
        COUNT(*)                                                AS total_orders,
        COUNT(*) FILTER(WHERE cat_count > 1)                   AS multi_cat_orders,
        ROUND(COUNT(*) FILTER(WHERE cat_count > 1)*100.0/NULLIF(COUNT(*),0), 1) AS multi_cat_pct,
        ROUND(AVG(cat_count), 2)                               AS avg_cats,
        ROUND(COUNT(*) FILTER(WHERE cat_count >= 3)*100.0/NULLIF(COUNT(*),0), 1) AS three_plus_pct
    FROM order_cats
    """
    r = _row(engine, summary_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    pairs_sql = """
    WITH order_cats AS (
        SELECT o.order_id, p.category_id
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        JOIN kirana_oltp.product p     ON oi.product_id = p.product_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
        GROUP BY o.order_id, p.category_id
    ),
    pairs AS (
        SELECT a.category_id AS cat_a_id, b.category_id AS cat_b_id,
               ca.name AS cat_a, cb.name AS cat_b,
               COUNT(*) AS co_occ
        FROM order_cats a
        JOIN order_cats b  ON a.order_id = b.order_id AND a.category_id < b.category_id
        JOIN kirana_oltp.category ca ON a.category_id = ca.category_id
        JOIN kirana_oltp.category cb ON b.category_id = cb.category_id
        GROUP BY 1,2,3,4
    ),
    totals AS (
        SELECT category_id, COUNT(DISTINCT order_id) AS cat_orders
        FROM order_cats GROUP BY category_id
    ),
    total_orders AS (SELECT COUNT(DISTINCT order_id) AS n FROM order_cats)
    SELECT p.cat_a, p.cat_b, p.co_occ,
           ROUND((p.co_occ::numeric / NULLIF((ta.cat_orders::numeric * tb.cat_orders / NULLIF(tot.n,0)),0)), 3) AS lift
    FROM pairs p
    JOIN totals ta ON p.cat_a_id = ta.category_id
    JOIN totals tb ON p.cat_b_id = tb.category_id
    CROSS JOIN total_orders tot
    ORDER BY p.co_occ DESC LIMIT 15
    """
    pairs = _rows(engine, pairs_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    pp_from, pp_to = _prev_period(days)
    prev_pct = _scalar(engine, """
    WITH oc AS (
        SELECT o.order_id, COUNT(DISTINCT p.category_id) AS cat_count
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
        JOIN kirana_oltp.product p ON oi.product_id=p.product_id
        WHERE o.store_id=:sid AND o.order_status='completed'
          AND o.order_date BETWEEN :pp_from AND :pp_to
        GROUP BY o.order_id
    )
    SELECT ROUND(COUNT(*) FILTER(WHERE cat_count>1)*100.0/NULLIF(COUNT(*),0),1) FROM oc
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    cur_pct = float(r.get("multi_cat_pct") or 0)
    return {
        "total_orders":             int(r.get("total_orders") or 0),
        "multi_category_orders":    int(r.get("multi_cat_orders") or 0),
        "multi_category_pct":       cur_pct,
        "avg_categories_per_order": float(r.get("avg_cats") or 0),
        "orders_3plus_cat_pct":     float(r.get("three_plus_pct") or 0),
        "top_pairs": [
            {"category_a": p["cat_a"], "category_b": p["cat_b"],
             "co_occurrences": int(p["co_occ"]), "lift": float(p["lift"] or 0)}
            for p in pairs
        ],
        "trend": _trend(cur_pct, float(prev_pct or 0)),
    }


# ── 6. WhatsApp Order Conversion ──────────────────────────────────────────────


    """Calculate conversion of WhatsApp sessions to actual orders.
    Logic:
      1. Find unique phone numbers in wa_sessions for this store.
      2. Join with kirana_oltp.customer to find matching customer_ids.
      3. Count how many of those customers placed orders in the period.
    """
    p_from, p_to = _period(days)

    sql = """
    WITH store_sessions AS (
        -- Unique phones that chatted with this store
        SELECT DISTINCT phone 
        FROM wa_sessions 
        WHERE store_id = :sid 
          AND (last_message_at >= :p_from OR updated_at >= :p_from)
    ),
    linked_customers AS (
        -- Map them to our customer master
        SELECT s.phone, c.customer_id
        FROM store_sessions s
        JOIN kirana_oltp.customer c ON regexp_replace(c.phone, '\D', '', 'g') = regexp_replace(s.phone, '\D', '', 'g')
    ),
    converting_customers AS (
        -- See who actually bought
        SELECT DISTINCT lc.customer_id
        FROM linked_customers lc
        JOIN kirana_oltp.orders o ON o.customer_id = lc.customer_id
        WHERE o.store_id = :sid
          AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
    )
    SELECT
        (SELECT COUNT(*) FROM store_sessions)        AS total_whatsapp_users,
        (SELECT COUNT(*) FROM converting_customers)  AS converted_users
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_users = int(r.get("total_whatsapp_users") or 0)
    converted   = int(r.get("converted_users") or 0)
    conv_pct    = round(converted * 100.0 / max(total_users, 1), 1)

    return {
        "total_whatsapp_users": total_users,
        "converted_users":      converted,
        "conversion_proxy_pct": conv_pct,
        "period_days":          days
    }


# ── 7. Morning Stock Readiness ────────────────────────────────────────────────

def calc_morning_stock_readiness(engine, store_id: int, ml_adapter=None) -> dict:
    """
    Fast-movers + stockout predictions from ML; compares today's inventory vs demand.
    readiness_score = % fast-moving SKUs with days_of_cover >= 2
    """
    sql = """
    WITH fast_movers AS (
        SELECT d.product_id, p.name, c.name AS category_name,
               AVG(d.units_sold) AS avg_daily_demand
        FROM kirana_olap.daily_store_sku_metrics d
        JOIN kirana_oltp.product p  ON d.product_id = p.product_id
        JOIN kirana_oltp.category c ON p.category_id = c.category_id
        WHERE d.store_id = :sid
          AND d.date >= CURRENT_DATE - 14
          AND d.units_sold > 0
        GROUP BY d.product_id, p.name, c.name
        HAVING AVG(d.units_sold) >= 3
    )
    SELECT fm.product_id, fm.name AS product_name, fm.category_name,
           COALESCE(i.quantity, 0) AS current_stock,
           ROUND(fm.avg_daily_demand::numeric, 2) AS avg_daily_demand,
           ROUND(COALESCE(i.quantity,0) / NULLIF(fm.avg_daily_demand,0), 1) AS days_of_cover
    FROM fast_movers fm
    LEFT JOIN kirana_oltp.inventory i
           ON i.product_id = fm.product_id AND i.store_id = :sid
    ORDER BY days_of_cover ASC
    """
    rows = _rows(engine, sql, {"sid": store_id})
    if not rows:
        return {"readiness_score": 0, "ready_count": 0, "low_count": 0,
                "critical_count": 0, "total_fast_movers": 0, "skus": [],
                "trend": _trend(None, None)}

    # Merge stockout probabilities from ML
    stockout_map: dict[int, float] = {}
    if ml_adapter:
        df = ml_adapter.get_frame()
        if not df.empty:
            sub = df[(df["store_id"] == store_id) & (df["recommendation_type"] == "stockout_risk")]
            stockout_map = dict(zip(sub["sku_id"].astype(int), sub["prob_stockout_7d"]))

    skus = []
    ready = low = critical = 0
    for r in rows:
        doc  = float(r["days_of_cover"] or 0)
        status = "critical" if doc < 2 else ("low" if doc < 4 else "ready")
        if status == "ready": ready += 1
        elif status == "low": low += 1
        else: critical += 1
        pid = int(r["product_id"])
        skus.append({
            "product_id":        pid,
            "product_name":      r["product_name"],
            "category_name":     r["category_name"],
            "current_stock":     int(r["current_stock"]),
            "avg_daily_demand":  float(r["avg_daily_demand"]),
            "days_of_cover":     doc,
            "readiness_status":  status,
            "stockout_risk_7d":  stockout_map.get(pid),
        })

    total = len(rows)
    score = round(ready * 100.0 / max(total, 1), 1)

    return {
        "readiness_score":   score,
        "ready_count":       ready,
        "low_count":         low,
        "critical_count":    critical,
        "total_fast_movers": total,
        "skus":              skus,
        "trend":             _trend(score, None),
    }


# ── 8. Procurement Cost Savings ───────────────────────────────────────────────

def calc_procurement_cost(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH purchases AS (
        SELECT pu.supplier_id, s.name AS supplier_name,
               pi.product_id, pi.quantity,
               pi.cost_price                          AS actual_unit_cost,
               ps.cost_price                          AS standard_unit_cost,
               pi.quantity * pi.cost_price            AS actual_value,
               pi.quantity * ps.cost_price            AS standard_value
        FROM kirana_oltp.purchases pu
        JOIN kirana_oltp.purchase_items pi ON pu.purchase_id = pi.purchase_id
        JOIN kirana_oltp.product_supplier ps
              ON pi.product_id = ps.product_id AND pu.supplier_id = ps.supplier_id
        JOIN kirana_oltp.supplier s ON pu.supplier_id = s.supplier_id
        WHERE pu.store_id = :sid
          AND pu.order_date BETWEEN :p_from AND :p_to
    )
    SELECT
        supplier_id, supplier_name,
        ROUND(SUM(actual_value)::numeric,   2)   AS total_actual,
        ROUND(SUM(standard_value)::numeric, 2)   AS total_standard,
        ROUND(SUM(standard_value - actual_value)::numeric, 2) AS savings,
        ROUND((SUM(standard_value - actual_value) / NULLIF(SUM(standard_value),0) * 100)::numeric, 2) AS savings_pct
    FROM purchases
    GROUP BY supplier_id, supplier_name
    ORDER BY savings DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_actual   = sum(float(r.get("total_actual")   or 0) for r in rows)
    total_standard = sum(float(r.get("total_standard") or 0) for r in rows)
    net_savings    = sum(float(r.get("savings")        or 0) for r in rows)
    savings_pct    = round(net_savings / max(total_standard, 1) * 100, 2)
    overpay_count  = sum(1 for r in rows if float(r.get("savings") or 0) < 0)
    underpay_count = sum(1 for r in rows if float(r.get("savings") or 0) > 0)

    pp_from, pp_to = _prev_period(days)
    prev_sp = _scalar(engine, """
    SELECT SUM(ps.cost_price - pi.cost_price)*100/NULLIF(SUM(ps.cost_price),0)
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
    JOIN kirana_oltp.product_supplier ps ON pi.product_id=ps.product_id AND pu.supplier_id=ps.supplier_id
    WHERE pu.store_id=:sid AND pu.order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    return {
        "total_purchased_value": round(total_actual, 2),
        "total_standard_value":  round(total_standard, 2),
        "net_savings":           round(net_savings, 2),
        "savings_pct":           savings_pct,
        "overpay_count":         overpay_count,
        "underpay_count":        underpay_count,
        "by_supplier": [
            {"supplier_id":           int(r["supplier_id"]),
             "supplier_name":         r["supplier_name"],
             "total_purchased_value": float(r.get("total_actual") or 0),
             "standard_value":        float(r.get("total_standard") or 0),
             "actual_savings":        float(r.get("savings") or 0),
             "savings_pct":           float(r.get("savings_pct") or 0)}
            for r in rows
        ],
        "trend": _trend(savings_pct, float(prev_sp or 0)),
    }


# ── 9. Inventory Holding Cost ─────────────────────────────────────────────────

def calc_inventory_holding(engine, store_id: int) -> dict:
    sql = """
    WITH inv AS (
        SELECT i.product_id, i.quantity,
               ps.cost_price,
               i.quantity * ps.cost_price                         AS stock_value,
               c.name AS category_name
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p    ON i.product_id = p.product_id
        JOIN kirana_oltp.category c   ON p.category_id = c.category_id
        LEFT JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
        WHERE i.store_id = :sid AND ps.cost_price IS NOT NULL
    ),
    demand AS (
        SELECT product_id,
               AVG(units_sold) AS avg_daily_demand
        FROM kirana_olap.daily_store_sku_metrics
        WHERE store_id = :sid AND date >= CURRENT_DATE - 30
        GROUP BY product_id
    )
    SELECT i.category_name,
           ROUND(SUM(i.stock_value)::numeric, 2)             AS avg_stock_value,
           ROUND(SUM(i.stock_value) * :hold_rate / 365 * 30, 2) AS holding_cost_30d,
           ROUND(SUM(i.stock_value) * :hold_rate / 365 * 30
                 / NULLIF(SUM(i.stock_value), 0) * 100, 2)   AS holding_cost_pct,
           SUM(GREATEST(0, i.quantity - COALESCE(d.avg_daily_demand,0) * 14))::int AS excess_units,
           ROUND(SUM(GREATEST(0, i.quantity - COALESCE(d.avg_daily_demand,0)*14)
                     * i.cost_price)::numeric, 2)              AS excess_value
    FROM inv i LEFT JOIN demand d USING (product_id)
    GROUP BY i.category_name
    ORDER BY holding_cost_30d DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "hold_rate": _HOLDING_RATE})

    total_stock  = sum(float(r.get("avg_stock_value") or 0) for r in rows)
    total_hold   = sum(float(r.get("holding_cost_30d") or 0) for r in rows)
    excess_value = sum(float(r.get("excess_value") or 0) for r in rows)

    rev_sql = "SELECT SUM(revenue) FROM kirana_olap.daily_store_sku_metrics WHERE store_id=:sid AND date>=CURRENT_DATE-30"
    monthly_rev = float(_scalar(engine, rev_sql, {"sid": store_id}) or 1)
    hold_pct_rev = round(total_hold / max(monthly_rev, 1) * 100, 2)

    return {
        "total_stock_value":            round(total_stock, 2),
        "total_holding_cost":           round(total_hold, 2),
        "holding_cost_pct_of_revenue":  hold_pct_rev,
        "excess_inventory_value":       round(excess_value, 2),
        "optimal_stock_value":          round(total_stock - excess_value, 2),
        "by_category": [
            {"category_name":    r["category_name"],
             "avg_stock_value":  float(r.get("avg_stock_value") or 0),
             "holding_cost":     float(r.get("holding_cost_30d") or 0),
             "holding_cost_pct": float(r.get("holding_cost_pct") or 0),
             "excess_units":     int(r.get("excess_units") or 0),
             "excess_value":     float(r.get("excess_value") or 0)}
            for r in rows
        ],
        "trend": _trend(100 - hold_pct_rev, None, higher_is_better=False),
    }


# ── 10. Distributor Terms Leverage ────────────────────────────────────────────

def calc_distributor_terms(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH purchases AS (
        SELECT pu.supplier_id, s.name AS supplier_name,
               COUNT(DISTINCT pu.purchase_id)             AS order_count,
               AVG(pi.cost_price)                         AS avg_actual,
               AVG(ps.cost_price)                         AS avg_standard,
               AVG(EXTRACT(EPOCH FROM (pu.arrival_date - pu.order_date))/86400) AS avg_actual_lead,
               AVG(ps.lead_time_days)                     AS avg_expected_lead
        FROM kirana_oltp.purchases pu
        JOIN kirana_oltp.purchase_items pi
              ON pu.purchase_id = pi.purchase_id
        JOIN kirana_oltp.product_supplier ps
              ON pi.product_id = ps.product_id AND pu.supplier_id = ps.supplier_id
        JOIN kirana_oltp.supplier s ON pu.supplier_id = s.supplier_id
        WHERE pu.store_id = :sid
          AND pu.order_date BETWEEN :p_from AND :p_to
          AND pu.arrival_date IS NOT NULL
        GROUP BY pu.supplier_id, s.name
    )
    SELECT *,
           ROUND((avg_actual - avg_standard) / NULLIF(avg_standard,0) * 100, 2) AS price_variance_pct,
           ROUND(100 - ABS(avg_actual_lead - avg_expected_lead)
                       / NULLIF(avg_expected_lead,0) * 100, 1)                   AS lead_time_accuracy
    FROM purchases
    ORDER BY lead_time_accuracy DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    overpay_opp = sum(
        max(0, float(r.get("price_variance_pct") or 0)) * float(r.get("avg_standard") or 0) * int(r.get("order_count") or 0)
        for r in rows
    )
    best = rows[0] if rows else {}

    def _reliability(r):
        price_var = abs(float(r.get("price_variance_pct") or 0))
        lt_acc    = float(r.get("lead_time_accuracy") or 50)
        return round(max(0, min(100, lt_acc * 0.6 + max(0, 100 - price_var * 5) * 0.4)), 1)

    return {
        "total_suppliers": len(rows),
        "best_supplier_id":   int(best.get("supplier_id") or 0),
        "best_supplier_name": str(best.get("supplier_name") or ""),
        "total_overpay_opportunity": round(overpay_opp, 2),
        "by_supplier": [
            {"supplier_id":           int(r["supplier_id"]),
             "supplier_name":         r["supplier_name"],
             "total_orders":          int(r.get("order_count") or 0),
             "avg_actual_cost":       round(float(r.get("avg_actual") or 0), 2),
             "avg_standard_cost":     round(float(r.get("avg_standard") or 0), 2),
             "price_variance_pct":    float(r.get("price_variance_pct") or 0),
             "reliability_score":     _reliability(r),
             "lead_time_accuracy_pct": float(r.get("lead_time_accuracy") or 0),
             "recommendation":
                "Negotiate better rates" if float(r.get("price_variance_pct") or 0) > 5
                else ("Reliable — prefer for critical SKUs"
                      if _reliability(r) >= 80 else "Monitor lead times")}
            for r in rows
        ],
        "trend": _trend(100 - overpay_opp / max(sum(float(r.get("avg_standard") or 0) for r in rows), 1) * 100, None),
    }


# ── 11. Perishable Freshness Waste ────────────────────────────────────────────

def calc_perishable_waste(engine, store_id: int, days: int = 14) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH perishables AS (
        SELECT p.product_id, p.name, c.name AS category_name,
               i.quantity AS current_stock,
               COALESCE(d.avg_daily, 0) AS avg_daily_sales
        FROM kirana_oltp.product p
        JOIN kirana_oltp.category c   ON p.category_id = c.category_id
        JOIN kirana_oltp.inventory i  ON p.product_id = i.product_id AND i.store_id = :sid
        LEFT JOIN (
            SELECT product_id, AVG(units_sold) AS avg_daily
            FROM kirana_olap.daily_store_sku_metrics
            WHERE store_id = :sid AND date >= CURRENT_DATE - 14
            GROUP BY product_id
        ) d ON p.product_id = d.product_id
        WHERE p.is_perishable = TRUE
    ),
    stagnant AS (
        SELECT product_id,
               SUM(CASE WHEN stock_on_hand = first_snap THEN 1 ELSE 0 END) AS unchanged_days
        FROM (
            SELECT product_id, stock_on_hand,
                   FIRST_VALUE(stock_on_hand) OVER (PARTITION BY product_id ORDER BY snapshot_date) AS first_snap
            FROM kirana_oltp.inventory_snapshots
            WHERE store_id = :sid AND snapshot_date >= CURRENT_DATE - :days
        ) sub
        GROUP BY product_id
    )
    SELECT per.product_id, per.name, per.category_name,
           per.current_stock, per.avg_daily_sales,
           ROUND(per.current_stock / NULLIF(per.avg_daily_sales, 0), 1) AS days_of_cover,
           COALESCE(stag.unchanged_days, 0) AS days_unchanged
    FROM perishables per
    LEFT JOIN (SELECT product_id, MAX(unchanged_days) AS unchanged_days FROM stagnant GROUP BY product_id) stag
           USING (product_id)
    ORDER BY days_of_cover DESC NULLS LAST
    """
    rows = _rows(engine, sql, {"sid": store_id, "days": days})

    cost_sql = """
    SELECT p.product_id, ps.cost_price
    FROM kirana_oltp.product p
    JOIN kirana_oltp.product_supplier ps ON p.product_id = ps.product_id
    WHERE p.is_perishable = TRUE
    """
    cost_map = {int(r["product_id"]): float(r["cost_price"] or 0)
                for r in _rows(engine, cost_sql, {})}

    items = []
    total_at_risk_value = 0.0
    high = medium = 0
    total_stock = 0

    for r in rows:
        doc    = float(r.get("days_of_cover") or 0)
        pid    = int(r["product_id"])
        stock  = int(r.get("current_stock") or 0)
        cost   = cost_map.get(pid, 0)
        # Waste risk: items with >3 days of cover are perishable risk (items sell fast or go bad)
        # days_unchanged > 3 is a strong stagnation signal
        unchanged = int(r.get("days_unchanged") or 0)
        if doc > 7 or unchanged > 3:
            risk = "high"
            high += 1
        elif doc > 4 or unchanged > 1:
            risk = "medium"
            medium += 1
        else:
            risk = "low"

        waste_val = round(stock * cost, 2) if risk in ("high", "medium") else 0
        total_at_risk_value += waste_val
        total_stock += stock
        items.append({
            "product_id":            pid,
            "product_name":          r["name"],
            "category_name":         r["category_name"],
            "current_stock":         stock,
            "days_stock_unchanged":  unchanged,
            "daily_avg_sales":       float(r.get("avg_daily_sales") or 0),
            "days_of_cover":         doc if doc != float("inf") else 999.0,
            "waste_risk":            risk,
            "estimated_waste_value": waste_val,
        })

    waste_rate = round((high + medium) * 100.0 / max(len(items), 1), 1)
    return {
        "total_perishable_skus": len(items),
        "high_risk_count":       high,
        "medium_risk_count":     medium,
        "total_at_risk_value":   round(total_at_risk_value, 2),
        "waste_rate_pct":        waste_rate,
        "items":                 sorted(items, key=lambda x: x["waste_risk"] == "high", reverse=True),
        "trend":                 _trend(100 - waste_rate, None, higher_is_better=False),
    }


# ── 12. Pilferage / Shrinkage Loss ────────────────────────────────────────────

def calc_shrinkage(engine, store_id: int, days: int = 30, ml_anomaly_fn=None) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH opening AS (
        SELECT DISTINCT ON (product_id) product_id, stock_on_hand AS opening_stock
        FROM kirana_oltp.inventory_snapshots
        WHERE store_id = :sid AND snapshot_date >= :p_from
        ORDER BY product_id, snapshot_date ASC
    ),
    closing AS (
        SELECT DISTINCT ON (product_id) product_id, stock_on_hand AS closing_stock
        FROM kirana_oltp.inventory_snapshots
        WHERE store_id = :sid AND snapshot_date <= :p_to
        ORDER BY product_id, snapshot_date DESC
    ),
    moves AS (
        SELECT product_id,
               SUM(CASE WHEN reason='purchase' THEN change_quantity  ELSE 0 END) AS purchased,
               SUM(CASE WHEN reason='sale'     THEN ABS(change_quantity) ELSE 0 END) AS sold
        FROM kirana_oltp.inventory_movements
        WHERE store_id = :sid
          AND created_at::date BETWEEN :p_from AND :p_to
        GROUP BY product_id
    ),
    shrink AS (
        SELECT o.product_id,
               p.name,
               c.name AS category_name,
               o.opening_stock,
               COALESCE(m.purchased, 0) AS purchased,
               COALESCE(m.sold, 0)      AS sold,
               (o.opening_stock + COALESCE(m.purchased,0) - COALESCE(m.sold,0)) AS expected_closing,
               cl.closing_stock         AS actual_closing,
               (o.opening_stock + COALESCE(m.purchased,0) - COALESCE(m.sold,0))
                 - cl.closing_stock     AS shrinkage_units
        FROM opening o
        JOIN closing  cl ON o.product_id = cl.product_id
        LEFT JOIN moves m ON o.product_id = m.product_id
        JOIN kirana_oltp.product p   ON o.product_id = p.product_id
        JOIN kirana_oltp.category c  ON p.category_id = c.category_id
    )
    SELECT s.*,
           ROUND((ps.cost_price * s.shrinkage_units)::numeric, 2) AS shrinkage_value
    FROM shrink s
    LEFT JOIN kirana_oltp.product_supplier ps ON s.product_id = ps.product_id
    WHERE s.shrinkage_units > 0
    ORDER BY shrinkage_units DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_units = sum(int(r.get("shrinkage_units") or 0) for r in rows)
    total_value = sum(float(r.get("shrinkage_value") or 0) for r in rows)

    # Total sold in period for rate calculation
    total_sold_sql = """
    SELECT SUM(ABS(change_quantity)) FROM kirana_oltp.inventory_movements
    WHERE store_id=:sid AND reason='sale'
      AND created_at::date BETWEEN :p_from AND :p_to
    """
    total_sold = float(_scalar(engine, total_sold_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}) or 1)
    shrink_rate = round(total_units / (total_units + total_sold) * 100, 2)

    # Anomaly scores from ML if available
    scores: dict[int, float] = {}
    flagged_threshold = 5
    if ml_anomaly_fn:
        try:
            scores = ml_anomaly_fn([int(r["product_id"]) for r in rows],
                                   [int(r.get("shrinkage_units") or 0) for r in rows])
        except Exception:
            pass

    items = []
    flagged_count = 0
    for r in rows:
        pid = int(r["product_id"])
        su  = int(r.get("shrinkage_units") or 0)
        score = scores.get(pid)
        flagged = su >= flagged_threshold or (score is not None and score > 0.7)
        if flagged:
            flagged_count += 1
        items.append({
            "product_id":      pid,
            "product_name":    r["name"],
            "category_name":   r["category_name"],
            "opening_stock":   int(r.get("opening_stock") or 0),
            "purchases":       int(r.get("purchased") or 0),
            "sales":           int(r.get("sold") or 0),
            "expected_closing": int(r.get("expected_closing") or 0),
            "actual_closing":  int(r.get("actual_closing") or 0),
            "shrinkage_units": su,
            "shrinkage_value": float(r.get("shrinkage_value") or 0),
            "anomaly_score":   score,
            "flagged":         flagged,
        })

    return {
        "total_shrinkage_units": total_units,
        "total_shrinkage_value": round(total_value, 2),
        "shrinkage_rate_pct":   shrink_rate,
        "flagged_skus_count":   flagged_count,
        "items":                items[:30],
        "trend":                _trend(100 - shrink_rate, None, higher_is_better=False),
    }


# ── 13. Reorder Lead-Time Accuracy ────────────────────────────────────────────

def calc_lead_time_accuracy(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH purchase_lead AS (
        SELECT pu.supplier_id, s.name AS supplier_name,
               ps.lead_time_days AS expected_days,
               EXTRACT(EPOCH FROM (pu.arrival_date - pu.order_date))/86400 AS actual_days
        FROM kirana_oltp.purchases pu
        JOIN kirana_oltp.purchase_items pi ON pu.purchase_id = pi.purchase_id
        JOIN kirana_oltp.product_supplier ps
              ON pi.product_id = ps.product_id AND pu.supplier_id = ps.supplier_id
        JOIN kirana_oltp.supplier s ON pu.supplier_id = s.supplier_id
        WHERE pu.store_id = :sid
          AND pu.order_date BETWEEN :p_from AND :p_to
          AND pu.arrival_date IS NOT NULL
    )
    SELECT supplier_id, supplier_name,
           COUNT(*)                                     AS order_count,
           ROUND(AVG(expected_days)::numeric, 2)        AS avg_expected,
           ROUND(AVG(actual_days)::numeric, 2)          AS avg_actual,
           ROUND(COUNT(*) FILTER(WHERE actual_days <= expected_days + 0.5)*100.0/NULLIF(COUNT(*),0), 1) AS on_time_pct,
           ROUND(AVG(ABS(actual_days - expected_days)/NULLIF(expected_days,0))*100, 2) AS mape
    FROM purchase_lead
    GROUP BY supplier_id, supplier_name
    ORDER BY on_time_pct DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    if not rows:
        return {"total_purchase_orders": 0, "avg_expected_days": 0, "avg_actual_days": 0,
                "on_time_rate_pct": 0, "overall_accuracy_pct": 0, "by_supplier": [],
                "trend": _trend(None, None)}

    total_orders = sum(int(r.get("order_count") or 0) for r in rows)
    overall_on_time = sum(
        float(r.get("on_time_pct") or 0) * int(r.get("order_count") or 0)
        for r in rows
    ) / max(total_orders, 1)
    avg_expected = sum(float(r.get("avg_expected") or 0) for r in rows) / max(len(rows), 1)
    avg_actual   = sum(float(r.get("avg_actual")   or 0) for r in rows) / max(len(rows), 1)
    overall_acc  = round(100 - sum(float(r.get("mape") or 0) for r in rows) / max(len(rows), 1), 1)

    pp_from, pp_to = _prev_period(days)
    prev_ot = _scalar(engine, """
    SELECT COUNT(*) FILTER(WHERE EXTRACT(EPOCH FROM (pu.arrival_date-pu.order_date))/86400
                                 <= ps.lead_time_days + 0.5)*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
    JOIN kirana_oltp.product_supplier ps ON pi.product_id=ps.product_id AND pu.supplier_id=ps.supplier_id
    WHERE pu.store_id=:sid AND pu.order_date BETWEEN :pp_from AND :pp_to AND pu.arrival_date IS NOT NULL
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    def _rel_score(r):
        ot  = float(r.get("on_time_pct") or 50)
        mpe = float(r.get("mape") or 50)
        return round(ot * 0.7 + max(0, 100 - mpe) * 0.3, 1)

    return {
        "total_purchase_orders": total_orders,
        "avg_expected_days":     round(avg_expected, 2),
        "avg_actual_days":       round(avg_actual, 2),
        "on_time_rate_pct":      round(overall_on_time, 1),
        "overall_accuracy_pct":  overall_acc,
        "by_supplier": [
            {"supplier_id":             int(r["supplier_id"]),
             "supplier_name":           r["supplier_name"],
             "order_count":             int(r.get("order_count") or 0),
             "avg_expected_days":       float(r.get("avg_expected") or 0),
             "avg_actual_days":         float(r.get("avg_actual") or 0),
             "on_time_pct":             float(r.get("on_time_pct") or 0),
             "mape":                    float(r.get("mape") or 0),
             "reliability_score":       _rel_score(r)}
            for r in rows
        ],
        "trend": _trend(overall_on_time, float(prev_ot or 0)),
    }


# ── 14. Cash Leakage / Billing Misses ────────────────────────────────────────

def calc_cash_leakage(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT o.order_id, o.order_date, o.total_amount,
           p.amount AS payment_amount,
           COALESCE(o.total_amount - p.amount, o.total_amount) AS gap,
           CASE
               WHEN p.payment_id IS NULL THEN 'unpaid'
               WHEN ABS(o.total_amount - p.amount) > 1 AND o.total_amount > p.amount THEN 'underpaid'
               WHEN ABS(o.total_amount - p.amount) > 1 AND o.total_amount < p.amount THEN 'overpaid'
               ELSE 'clean'
           END AS issue_type
    FROM kirana_oltp.orders o
    LEFT JOIN kirana_oltp.payments p ON o.order_id = p.order_id
    WHERE o.store_id = :sid
      AND o.order_status = 'completed'
      AND o.order_date BETWEEN :p_from AND :p_to
    ORDER BY gap DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_orders     = len(rows)
    problematic      = [r for r in rows if r["issue_type"] != "clean"]
    unpaid           = [r for r in rows if r["issue_type"] == "unpaid"]
    mismatch         = [r for r in rows if r["issue_type"] in ("underpaid", "overpaid")]
    total_leakage    = sum(float(r.get("gap") or 0) for r in problematic)
    leakage_rate_pct = round(len(problematic) * 100.0 / max(total_orders, 1), 2)

    pp_from, pp_to = _prev_period(days)
    prev_rate = _scalar(engine, """
    SELECT COUNT(*) FILTER(WHERE p.payment_id IS NULL OR ABS(o.total_amount - p.amount) > 1)*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.orders o LEFT JOIN kirana_oltp.payments p ON o.order_id=p.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    return {
        "total_orders":       total_orders,
        "clean_orders":       total_orders - len(problematic),
        "problematic_orders": len(problematic),
        "total_leakage_value": round(total_leakage, 2),
        "leakage_rate_pct":   leakage_rate_pct,
        "unpaid_count":       len(unpaid),
        "mismatch_count":     len(mismatch),
        "flagged_orders": [
            {"order_id":       int(r["order_id"]),
             "order_date":     r["order_date"],
             "order_total":    float(r.get("total_amount") or 0),
             "payment_amount": float(r["payment_amount"]) if r.get("payment_amount") is not None else None,
             "gap":            float(r.get("gap") or 0),
             "issue_type":     r["issue_type"]}
            for r in problematic[:50]
        ],
        "trend": _trend(100 - leakage_rate_pct, float(100 - (prev_rate or 0))),
    }


# ── 15. Daily Revenue (GMV) ───────────────────────────────────────────────────

def calc_daily_revenue(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    SELECT
        ROUND(SUM(total_amount)::numeric, 2)                            AS total_revenue,
        ROUND(AVG(total_amount)::numeric, 2)                            AS avg_daily_revenue,
        COUNT(*)                                                        AS order_count,
        ROUND(SUM(total_amount)::numeric / NULLIF(:days, 0), 2)         AS daily_avg
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_status = 'completed'
      AND order_date BETWEEN :p_from AND :p_to
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to, "days": days})

    by_day_sql = """
    SELECT order_date::date AS day,
           ROUND(SUM(total_amount)::numeric, 2) AS revenue,
           COUNT(*) AS orders
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_status = 'completed'
      AND order_date BETWEEN :p_from AND :p_to
    GROUP BY 1 ORDER BY 1 DESC LIMIT 14
    """
    daily = _rows(engine, by_day_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    prev_rev = _scalar(engine, """
    SELECT SUM(total_amount) FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_status='completed'
      AND order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    cur = float(r.get("total_revenue") or 0)
    return {
        "total_revenue":     cur,
        "avg_daily_revenue": float(r.get("daily_avg") or 0),
        "order_count":       int(r.get("order_count") or 0),
        "daily_breakdown":   [{"day": str(d["day"]), "revenue": float(d["revenue"] or 0),
                               "orders": int(d["orders"])} for d in daily],
        "trend": _trend(cur, float(prev_rev or 0)),
    }


# ── 16. Gross Profit Margin ───────────────────────────────────────────────────

def calc_gross_profit_margin(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    SELECT
        ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2)                        AS revenue,
        ROUND(SUM(oi.quantity * oi.cost_price)::numeric, 2)                        AS cogs,
        ROUND(SUM(oi.quantity * (oi.unit_price - oi.cost_price))::numeric, 2)      AS gross_profit,
        ROUND(SUM(oi.quantity * (oi.unit_price - oi.cost_price)) * 100.0
              / NULLIF(SUM(oi.quantity * oi.unit_price), 0), 2)                    AS gpm_pct
    FROM kirana_oltp.orders o
    JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
    WHERE o.store_id = :sid AND o.order_status = 'completed'
      AND o.order_date BETWEEN :p_from AND :p_to
      AND oi.cost_price > 0
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    by_cat_sql = """
    SELECT c.name AS category_name,
           ROUND(SUM(oi.quantity*(oi.unit_price-oi.cost_price))*100.0
                 /NULLIF(SUM(oi.quantity*oi.unit_price),0), 2) AS margin_pct,
           ROUND(SUM(oi.quantity*oi.unit_price)::numeric, 2)   AS revenue
    FROM kirana_oltp.orders o
    JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
    JOIN kirana_oltp.product p     ON oi.product_id=p.product_id
    JOIN kirana_oltp.category c    ON p.category_id=c.category_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :p_from AND :p_to AND oi.cost_price>0
    GROUP BY c.name ORDER BY margin_pct DESC
    """
    by_cat = _rows(engine, by_cat_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    prev_gpm = _scalar(engine, """
    SELECT SUM(oi.quantity*(oi.unit_price-oi.cost_price))*100.0
           /NULLIF(SUM(oi.quantity*oi.unit_price),0)
    FROM kirana_oltp.orders o JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to AND oi.cost_price>0
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    cur = float(r.get("gpm_pct") or 0)
    return {
        "total_revenue":   float(r.get("revenue") or 0),
        "total_cogs":      float(r.get("cogs") or 0),
        "gross_profit":    float(r.get("gross_profit") or 0),
        "gpm_pct":         cur,
        "by_category":     [{"category_name": c["category_name"],
                             "margin_pct": float(c["margin_pct"] or 0),
                             "revenue": float(c["revenue"] or 0)} for c in by_cat],
        "trend": _trend(cur, float(prev_gpm or 0)),
    }


# ── 17. Average Basket Value ──────────────────────────────────────────────────

def calc_avg_basket_value(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    SELECT
        ROUND(AVG(total_amount)::numeric, 2)                                AS avg_basket,
        ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY total_amount))::numeric, 2) AS median_basket,
        ROUND(MAX(total_amount)::numeric, 2)                                AS max_basket,
        ROUND(MIN(total_amount)::numeric, 2)                                AS min_basket,
        COUNT(*) AS order_count,
        ROUND(SUM(total_amount)::numeric, 2)                                AS total_revenue
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_status = 'completed'
      AND order_date BETWEEN :p_from AND :p_to
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    brackets_sql = """
    SELECT
        CASE
            WHEN total_amount < 100  THEN '<₹100'
            WHEN total_amount < 300  THEN '₹100–300'
            WHEN total_amount < 600  THEN '₹300–600'
            WHEN total_amount < 1000 THEN '₹600–1000'
            ELSE '>₹1000'
        END AS bracket,
        COUNT(*) AS order_count,
        ROUND(AVG(total_amount)::numeric, 2) AS avg_value
    FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_status='completed'
      AND order_date BETWEEN :p_from AND :p_to
    GROUP BY 1 ORDER BY MIN(total_amount)
    """
    brackets = _rows(engine, brackets_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    prev_avg = _scalar(engine, """
    SELECT AVG(total_amount) FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_status='completed'
      AND order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    cur = float(r.get("avg_basket") or 0)
    return {
        "avg_basket_value":    cur,
        "median_basket_value": float(r.get("median_basket") or 0),
        "max_basket_value":    float(r.get("max_basket") or 0),
        "min_basket_value":    float(r.get("min_basket") or 0),
        "order_count":         int(r.get("order_count") or 0),
        "total_revenue":       float(r.get("total_revenue") or 0),
        "brackets":            [{"bracket": b["bracket"], "order_count": int(b["order_count"]),
                                 "avg_value": float(b["avg_value"] or 0)} for b in brackets],
        "trend": _trend(cur, float(prev_avg or 0)),
    }


# ── 18. Inventory Turnover ────────────────────────────────────────────────────

def calc_inventory_turnover(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    cogs_sql = """
    SELECT ROUND(SUM(oi.quantity * oi.cost_price)::numeric, 2) AS cogs
    FROM kirana_oltp.orders o
    JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
    WHERE o.store_id = :sid AND o.order_status = 'completed'
      AND o.order_date BETWEEN :p_from AND :p_to AND oi.cost_price > 0
    """
    inv_sql = """
    SELECT ROUND(SUM(i.quantity * ps.cost_price)::numeric, 2) AS avg_inv_value
    FROM kirana_oltp.inventory i
    LEFT JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
    WHERE i.store_id = :sid AND ps.cost_price IS NOT NULL AND i.quantity > 0
    """
    by_cat_sql = """
    SELECT c.name AS category_name,
           ROUND(SUM(i.quantity * ps.cost_price)::numeric, 2) AS inv_value,
           ROUND(SUM(oi.quantity * oi.cost_price)::numeric, 2) AS cogs_value
    FROM kirana_oltp.inventory i
    JOIN kirana_oltp.product p    ON i.product_id = p.product_id
    JOIN kirana_oltp.category c   ON p.category_id = c.category_id
    LEFT JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
    LEFT JOIN kirana_oltp.order_item oi ON oi.product_id = i.product_id
    LEFT JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        AND o.store_id=:sid AND o.order_status='completed'
        AND o.order_date BETWEEN :p_from AND :p_to
    WHERE i.store_id = :sid AND ps.cost_price IS NOT NULL
    GROUP BY c.name ORDER BY inv_value DESC LIMIT 10
    """

    cr = _row(engine, cogs_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    ir = _row(engine, inv_sql, {"sid": store_id})
    by_cat = _rows(engine, by_cat_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    cogs      = float(cr.get("cogs") or 0)
    avg_inv   = float(ir.get("avg_inv_value") or 1)
    # Annualised turnover
    annualised_cogs = cogs * (365.0 / max(days, 1))
    turnover  = round(annualised_cogs / avg_inv, 2) if avg_inv else 0
    doi       = round(avg_inv / max(cogs / max(days, 1), 0.01), 1)  # days of inventory

    prev_cogs = float(_scalar(engine, """
    SELECT SUM(oi.quantity * oi.cost_price)
    FROM kirana_oltp.orders o JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to AND oi.cost_price>0
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to}) or 0)
    prev_turnover = round(prev_cogs * (365.0/max(days,1)) / avg_inv, 2) if avg_inv and prev_cogs else None

    return {
        "turnover_ratio":      turnover,
        "days_of_inventory":   doi,
        "cogs":                round(cogs, 2),
        "avg_inventory_value": round(avg_inv, 2),
        "by_category": [{"category_name": c["category_name"],
                         "inv_value": float(c["inv_value"] or 0),
                         "cogs": float(c["cogs_value"] or 0)} for c in by_cat],
        "trend": _trend(turnover, prev_turnover),
    }


# ── 19. Stockout Rate ─────────────────────────────────────────────────────────

def calc_stockout_rate(engine, store_id: int, days: int = 30) -> dict:
    # Current snapshot: % SKUs with qty=0
    oos_sql = """
    SELECT
        COUNT(*) AS total_skus,
        COUNT(*) FILTER(WHERE quantity = 0) AS oos_count,
        COUNT(*) FILTER(WHERE quantity > 0 AND quantity <= 5) AS low_stock_count,
        ROUND(COUNT(*) FILTER(WHERE quantity=0)*100.0/NULLIF(COUNT(*),0), 1) AS oos_rate
    FROM kirana_oltp.inventory WHERE store_id = :sid
    """
    r = _row(engine, oos_sql, {"sid": store_id})

    # Historical: stockout events from movements (sale with insufficient stock)
    hist_sql = """
    SELECT p.product_id, p.name AS product_name, c.name AS category_name,
           COUNT(*) FILTER(WHERE i.quantity = 0) AS days_oos
    FROM kirana_oltp.inventory i
    JOIN kirana_oltp.product p  ON i.product_id = p.product_id
    JOIN kirana_oltp.category c ON p.category_id = c.category_id
    WHERE i.store_id = :sid AND i.quantity = 0
    GROUP BY p.product_id, p.name, c.name
    ORDER BY days_oos DESC LIMIT 20
    """
    oos_items = _rows(engine, hist_sql, {"sid": store_id})

    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)
    prev_rate = _scalar(engine, """
    SELECT COUNT(*) FILTER(WHERE quantity=0)*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.inventory WHERE store_id=:sid
    """, {"sid": store_id})

    cur = float(r.get("oos_rate") or 0)
    return {
        "total_skus":       int(r.get("total_skus") or 0),
        "oos_sku_count":    int(r.get("oos_count") or 0),
        "low_stock_count":  int(r.get("low_stock_count") or 0),
        "oos_rate_pct":     cur,
        "oos_items":        [{"product_id": int(i["product_id"]),
                              "product_name": i["product_name"],
                              "category_name": i["category_name"]} for i in oos_items],
        "trend": _trend(100 - cur, float(100 - (prev_rate or 0))),
    }


# ── 20. Dead Stock ────────────────────────────────────────────────────────────

def calc_dead_stock(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)

    sql = """
    WITH sold AS (
        SELECT DISTINCT oi.product_id
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date >= :p_from
    ),
    inv AS (
        SELECT i.product_id, i.quantity,
               p.name AS product_name, c.name AS category_name,
               COALESCE(ps.cost_price, 0) AS cost_price
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p  ON i.product_id = p.product_id
        JOIN kirana_oltp.category c ON p.category_id = c.category_id
        LEFT JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
        WHERE i.store_id = :sid AND i.quantity > 0
    )
    SELECT inv.product_id, inv.product_name, inv.category_name,
           inv.quantity, inv.cost_price,
           ROUND((inv.quantity * inv.cost_price)::numeric, 2) AS dead_value
    FROM inv
    WHERE inv.product_id NOT IN (SELECT product_id FROM sold)
    ORDER BY dead_value DESC
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from})

    total_inv_sql = """
    SELECT COUNT(*) AS total_skus,
           SUM(i.quantity * COALESCE(ps.cost_price,0)) AS total_inv_value
    FROM kirana_oltp.inventory i
    LEFT JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
    WHERE i.store_id = :sid AND i.quantity > 0
    """
    ti = _row(engine, total_inv_sql, {"sid": store_id})

    dead_count = len(rows)
    dead_value = sum(float(r.get("dead_value") or 0) for r in rows)
    total_inv_value = float(ti.get("total_inv_value") or 1)
    dead_pct = round(dead_value / total_inv_value * 100, 1)

    return {
        "dead_sku_count":      dead_count,
        "dead_stock_value":    round(dead_value, 2),
        "total_inventory_value": round(total_inv_value, 2),
        "dead_stock_pct":      dead_pct,
        "analysis_days":       days,
        "items": [{"product_id": int(r["product_id"]),
                   "product_name": r["product_name"],
                   "category_name": r["category_name"],
                   "quantity": int(r["quantity"]),
                   "dead_value": float(r.get("dead_value") or 0)} for r in rows[:30]],
        "trend": _trend(100 - dead_pct, None, higher_is_better=False),
    }


# ── 21. Return Rate ───────────────────────────────────────────────────────────

def calc_return_rate(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    SELECT
        COUNT(*) AS total_orders,
        COUNT(*) FILTER(WHERE order_status IN ('cancelled', 'returned')) AS returned_orders,
        ROUND(COUNT(*) FILTER(WHERE order_status IN ('cancelled','returned'))*100.0
              / NULLIF(COUNT(*), 0), 2) AS return_rate,
        ROUND(SUM(total_amount) FILTER(WHERE order_status IN ('cancelled','returned'))::numeric, 2) AS returned_value
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_date BETWEEN :p_from AND :p_to
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    by_reason_sql = """
    SELECT order_status,
           COUNT(*) AS count,
           ROUND(SUM(total_amount)::numeric, 2) AS value
    FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_date BETWEEN :p_from AND :p_to
      AND order_status IN ('cancelled','returned','completed')
    GROUP BY order_status ORDER BY count DESC
    """
    by_status = _rows(engine, by_reason_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    prev_rate = _scalar(engine, """
    SELECT COUNT(*) FILTER(WHERE order_status IN ('cancelled','returned'))*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    cur = float(r.get("return_rate") or 0)
    return {
        "total_orders":    int(r.get("total_orders") or 0),
        "returned_orders": int(r.get("returned_orders") or 0),
        "return_rate_pct": cur,
        "returned_value":  float(r.get("returned_value") or 0),
        "by_status":       [{"status": s["order_status"], "count": int(s["count"]),
                             "value": float(s["value"] or 0)} for s in by_status],
        "trend": _trend(100 - cur, float(100 - (prev_rate or 0))),
    }


# ── 22. Cashflow Runway ───────────────────────────────────────────────────────

def calc_cashflow_runway(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    rev_sql = """
    SELECT ROUND(SUM(total_amount)::numeric, 2) AS revenue
    FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_status='completed'
      AND order_date BETWEEN :p_from AND :p_to
    """
    cost_sql = """
    SELECT ROUND(SUM(pi.quantity * pi.cost_price)::numeric, 2) AS total_cost
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id = pi.purchase_id
    WHERE pu.store_id=:sid AND pu.order_date BETWEEN :p_from AND :p_to
    """
    rr = _row(engine, rev_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    cr = _row(engine, cost_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    revenue       = float(rr.get("revenue") or 0)
    total_cost    = float(cr.get("total_cost") or 0)
    net_cashflow  = revenue - total_cost
    daily_revenue = revenue / max(days, 1)
    daily_cost    = total_cost / max(days, 1)
    daily_net     = net_cashflow / max(days, 1)

    # Runway = how many days at current net rate we can cover costs
    # Positive net = sustainable; estimate runway as net / daily_cost
    runway_days = round(net_cashflow / max(daily_cost, 1), 0) if daily_cost > 0 else 9999

    # Weekly cash flow trend
    weekly_sql = """
    WITH revenue AS (
        SELECT DATE_TRUNC('week', order_date)::date AS week,
               SUM(total_amount) AS rev
        FROM kirana_oltp.orders
        WHERE store_id=:sid AND order_status='completed'
          AND order_date BETWEEN :p_from AND :p_to
        GROUP BY 1
    ),
    costs AS (
        SELECT DATE_TRUNC('week', order_date)::date AS week,
               SUM(pi.quantity * pi.cost_price) AS cost
        FROM kirana_oltp.purchases pu
        JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
        WHERE pu.store_id=:sid AND pu.order_date BETWEEN :p_from AND :p_to
        GROUP BY 1
    )
    SELECT r.week, ROUND(r.rev::numeric,2) AS revenue,
           ROUND(COALESCE(c.cost,0)::numeric,2) AS cost,
           ROUND((r.rev - COALESCE(c.cost,0))::numeric,2) AS net
    FROM revenue r LEFT JOIN costs c USING(week) ORDER BY 1
    """
    weekly = _rows(engine, weekly_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    prev_net = _scalar(engine, """
    SELECT SUM(o.total_amount) - COALESCE(SUM(pi.quantity*pi.cost_price),0)
    FROM kirana_oltp.orders o
    LEFT JOIN kirana_oltp.purchases pu ON pu.store_id=o.store_id
        AND pu.order_date BETWEEN :pp_from AND :pp_to
    LEFT JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to})

    return {
        "period_revenue":   round(revenue, 2),
        "period_cost":      round(total_cost, 2),
        "net_cashflow":     round(net_cashflow, 2),
        "daily_net":        round(daily_net, 2),
        "runway_days":      int(min(runway_days, 9999)),
        "cashflow_status":  "positive" if net_cashflow > 0 else "negative",
        "weekly_cashflow":  [{"week": str(w["week"]), "revenue": float(w["revenue"] or 0),
                              "cost": float(w["cost"] or 0), "net": float(w["net"] or 0)}
                             for w in weekly],
        "trend": _trend(net_cashflow, float(prev_net or 0)),
    }


# ── High-Margin Item Sales % (K-TL5 / C7) ─────────────────────────────────────

def calc_high_margin_sales(engine, store_id: int, days: int = 30,
                            margin_pctile: float = 0.75) -> dict:
    """Revenue share contributed by SKUs whose margin exceeds the
    `margin_pctile`-th percentile within this store/period.

    A higher number means the store is shifting mix toward fatter-margin SKUs.
    """
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    WITH item_margin AS (
        SELECT
            oi.product_id,
            SUM(oi.quantity * oi.unit_price)                             AS revenue,
            SUM(oi.quantity * (oi.unit_price - oi.cost_price))           AS profit,
            CASE WHEN SUM(oi.quantity * oi.unit_price) > 0
                 THEN SUM(oi.quantity * (oi.unit_price - oi.cost_price))
                      / SUM(oi.quantity * oi.unit_price)
                 ELSE 0 END                                              AS margin_ratio
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON o.order_id = oi.order_id
        WHERE o.store_id = :sid
          AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
        GROUP BY oi.product_id
    ),
    threshold AS (
        SELECT PERCENTILE_CONT(:pctile) WITHIN GROUP (ORDER BY margin_ratio) AS th
        FROM item_margin
    )
    SELECT
        (SELECT COUNT(*) FROM item_margin) AS total_skus,
        (SELECT COUNT(*) FROM item_margin, threshold WHERE margin_ratio >= th) AS hm_sku_count,
        COALESCE(SUM(revenue), 0) AS total_revenue,
        COALESCE(SUM(CASE WHEN im.margin_ratio >= t.th THEN im.revenue ELSE 0 END), 0) AS hm_revenue,
        COALESCE(SUM(profit), 0) AS total_profit,
        COALESCE(SUM(CASE WHEN im.margin_ratio >= t.th THEN im.profit ELSE 0 END), 0) AS hm_profit
    FROM item_margin im, threshold t
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to, "pctile": margin_pctile})
    total_rev = float(r.get("total_revenue") or 0)
    hm_rev    = float(r.get("hm_revenue")    or 0)
    pct       = round(hm_rev / total_rev * 100, 2) if total_rev > 0 else 0.0

    # Prev period for trend
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to, "pctile": margin_pctile})
    prev_total = float(prev.get("total_revenue") or 0)
    prev_hm    = float(prev.get("hm_revenue")    or 0)
    prev_pct   = round(prev_hm / prev_total * 100, 2) if prev_total > 0 else 0.0

    return {
        "total_skus":            int(r.get("total_skus")    or 0),
        "high_margin_skus":      int(r.get("hm_sku_count")  or 0),
        "total_revenue":         round(total_rev, 2),
        "high_margin_revenue":   round(hm_rev, 2),
        "high_margin_pct":       pct,
        "high_margin_profit":    round(float(r.get("hm_profit") or 0), 2),
        "trend": _trend(pct, prev_pct),
    }


# ── Stockout Lost Sales (K-BL5) ──────────────────────────────────────────────

def calc_stockout_lost_sales(engine, store_id: int, days: int = 30) -> dict:
    """Estimate revenue lost due to stockouts using:
      lost_units * avg_selling_price (where lost_sales col exists)
      + zero-stock-day count * avg_daily_units * avg_price as fallback.

    Source: kirana_olap.daily_store_sku_metrics + kirana_oltp.inventory.
    """
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    WITH base AS (
        SELECT
            d.product_id,
            COALESCE(SUM(d.lost_sales), 0)                  AS lost_units,
            AVG(NULLIF(d.avg_selling_price, 0))             AS avg_price,
            AVG(NULLIF(d.units_sold, 0))                    AS avg_units_per_day,
            COUNT(*) FILTER (WHERE d.stock_on_hand = 0)     AS zero_stock_days
        FROM kirana_olap.daily_store_sku_metrics d
        WHERE d.store_id = :sid
          AND d.date BETWEEN :p_from AND :p_to
        GROUP BY d.product_id
    )
    SELECT
        COALESCE(SUM(lost_units * COALESCE(avg_price, 0)), 0) AS direct_lost_revenue,
        COALESCE(SUM(zero_stock_days * COALESCE(avg_units_per_day, 0) * COALESCE(avg_price, 0)), 0) AS proxy_lost_revenue,
        COALESCE(SUM(lost_units), 0) AS lost_units_total,
        COALESCE(SUM(zero_stock_days), 0) AS zero_stock_day_total,
        COUNT(*) AS skus_seen,
        COUNT(*) FILTER (WHERE zero_stock_days > 0 OR lost_units > 0) AS skus_impacted
    FROM base
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    direct = float(r.get("direct_lost_revenue") or 0)
    proxy  = float(r.get("proxy_lost_revenue")  or 0)
    estimate = direct if direct > 0 else proxy

    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})
    prev_dir = float(prev.get("direct_lost_revenue") or 0)
    prev_prx = float(prev.get("proxy_lost_revenue")  or 0)
    prev_est = prev_dir if prev_dir > 0 else prev_prx

    return {
        "estimated_lost_revenue":   round(estimate, 2),
        "direct_lost_revenue":      round(direct, 2),
        "proxy_lost_revenue":       round(proxy, 2),
        "lost_units":               int(r.get("lost_units_total") or 0),
        "zero_stock_days":          int(r.get("zero_stock_day_total") or 0),
        "skus_impacted":            int(r.get("skus_impacted") or 0),
        "skus_observed":            int(r.get("skus_seen") or 0),
        "method":                   "direct (lost_sales col)" if direct > 0
                                       else "proxy (zero-stock days × avg sales)",
        "trend": _trend(estimate, prev_est, higher_is_better=False),
    }


# ── Data Quality Score (C13) ─────────────────────────────────────────────────

def calc_data_quality_score(engine, store_id: int | None = None) -> dict:
    """Compute fill-rate of critical fields across core tables.
    Used as the C13 "Data Quality Score" KPI.
    """
    checks_sql = [
        ("orders.customer_id",      "SELECT COUNT(*) FILTER(WHERE customer_id IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.orders"),
        ("orders.user_id",          "SELECT COUNT(*) FILTER(WHERE user_id IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.orders"),
        ("product.brand",           "SELECT COUNT(*) FILTER(WHERE brand IS NOT NULL AND brand != '')*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.product"),
        ("product.barcode",         "SELECT COUNT(*) FILTER(WHERE barcode IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.product"),
        ("payments.payment_method", "SELECT COUNT(*) FILTER(WHERE payment_method IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.payments"),
        ("pricing.mrp",             "SELECT COUNT(*) FILTER(WHERE mrp IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.pricing"),
        ("supplier.contact",        "SELECT COUNT(*) FILTER(WHERE contact IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.supplier"),
    ]
    breakdown = []
    total = 0.0
    for label, q in checks_sql:
        v = float(_scalar(engine, q, {}) or 0)
        breakdown.append({"field": label, "fill_rate_pct": round(v, 2)})
        total += v
    score = round(total / max(len(checks_sql), 1), 2)
    return {
        "score":      score,
        "field_count": len(checks_sql),
        "breakdown":  breakdown,
        "trend": {"direction": "stable", "pct_change": None,
                  "current_value": score, "previous_value": None,
                  "interpretation": "Snapshot — historical baseline not tracked"},
    }


# ════════════════════════════════════════════════════════════════════════════
#  v6 KPIs — backed by the new tables/columns added in v6_schema_extensions.py
# ════════════════════════════════════════════════════════════════════════════

# ── K_TL_2: Walk-in to Purchase % ────────────────────────────────────────────

def calc_walkin_purchase(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    WITH visits AS (
        SELECT COALESCE(SUM(visitors), 0) AS total_visitors
        FROM kirana_oltp.footfall
        WHERE store_id = :sid AND ts::date BETWEEN :p_from AND :p_to
    ),
    bills AS (
        SELECT COUNT(DISTINCT order_id) AS total_bills
        FROM kirana_oltp.orders
        WHERE store_id = :sid
          AND order_status = 'completed'
          AND order_date::date BETWEEN :p_from AND :p_to
    )
    SELECT visits.total_visitors, bills.total_bills
    FROM visits, bills
    """
    cur  = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})

    visitors = int(cur.get("total_visitors") or 0)
    bills    = int(cur.get("total_bills")    or 0)
    rate     = round((bills / visitors) * 100, 2) if visitors > 0 else 0.0

    p_visitors = int(prev.get("total_visitors") or 0)
    p_bills    = int(prev.get("total_bills")    or 0)
    prev_rate  = round((p_bills / p_visitors) * 100, 2) if p_visitors > 0 else 0.0

    # Hour-of-day breakdown for ops insight
    hour_sql = """
    SELECT f.hour,
           SUM(f.visitors) AS visitors,
           COUNT(DISTINCT o.order_id) AS bills
    FROM kirana_oltp.footfall f
    LEFT JOIN kirana_oltp.orders o
        ON o.store_id = f.store_id
       AND date_trunc('hour', o.order_date) = date_trunc('hour', f.ts)
       AND o.order_status = 'completed'
    WHERE f.store_id = :sid
      AND f.ts::date BETWEEN :p_from AND :p_to
    GROUP BY f.hour
    ORDER BY f.hour
    """
    rows = _rows(engine, hour_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    by_hour = [
        {"hour": int(r["hour"]),
         "visitors": int(r["visitors"] or 0),
         "bills": int(r["bills"] or 0),
         "conversion_pct": round((int(r["bills"] or 0) / max(int(r["visitors"] or 0), 1)) * 100, 2)}
        for r in rows
    ]

    return {
        "total_visitors":   visitors,
        "total_bills":      bills,
        "conversion_pct":   rate,
        "by_hour":          by_hour,
        "trend": _trend(rate, prev_rate),
    }


# ── K_TL_6: Scheme Benefit Capture ───────────────────────────────────────────

def calc_scheme_capture(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    SELECT
        COUNT(*) FILTER (WHERE status = 'claimed') AS claimed,
        COUNT(*) FILTER (WHERE status = 'missed')  AS missed,
        COUNT(*)                                    AS total,
        COALESCE(SUM(amount_saved) FILTER (WHERE status='claimed'), 0) AS amount_saved
    FROM kirana_oltp.scheme_claim
    WHERE store_id = :sid
      AND claim_date BETWEEN :p_from AND :p_to
    """
    cur  = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})

    total    = int(cur.get("total") or 0)
    claimed  = int(cur.get("claimed") or 0)
    missed   = int(cur.get("missed") or 0)
    capture_pct = round((claimed / total) * 100, 2) if total > 0 else 0.0
    saved = float(cur.get("amount_saved") or 0)

    pt = int(prev.get("total") or 0)
    pc = int(prev.get("claimed") or 0)
    prev_pct = round((pc / pt) * 100, 2) if pt > 0 else 0.0

    # Active schemes the store has not yet claimed (potential opportunity)
    active_sql = """
    SELECT s.scheme_id, s.name, s.scheme_type, s.value, s.end_date
    FROM kirana_oltp.scheme s
    LEFT JOIN kirana_oltp.scheme_claim c
        ON c.scheme_id = s.scheme_id AND c.store_id = :sid
    WHERE :today BETWEEN s.start_date AND s.end_date
      AND c.claim_id IS NULL
    ORDER BY s.end_date
    LIMIT 10
    """
    active = _rows(engine, active_sql, {"sid": store_id, "today": date.today()})
    active_open = [
        {"scheme_id": int(r["scheme_id"]), "name": r["name"],
         "type": r["scheme_type"], "value": float(r["value"] or 0),
         "ends": str(r["end_date"])}
        for r in active
    ]

    return {
        "claimed":             claimed,
        "missed":               missed,
        "total":                total,
        "capture_pct":          capture_pct,
        "amount_saved":         round(saved, 2),
        "open_opportunities":  active_open,
        "trend": _trend(capture_pct, prev_pct),
    }


# ── K_TL_9: Home Delivery Revenue % ──────────────────────────────────────────

def calc_home_delivery(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    SELECT
        COALESCE(SUM(total_amount), 0)                                          AS total_revenue,
        COALESCE(SUM(total_amount) FILTER (WHERE order_channel = 'delivery'), 0) AS delivery_revenue,
        COALESCE(SUM(total_amount) FILTER (WHERE order_channel = 'whatsapp'), 0) AS whatsapp_revenue,
        COALESCE(SUM(total_amount) FILTER (WHERE order_channel = 'walk_in'),  0) AS walkin_revenue,
        COUNT(*)                                                                AS total_orders,
        COUNT(*) FILTER (WHERE order_channel = 'delivery')                      AS delivery_orders
    FROM kirana_oltp.orders
    WHERE store_id = :sid
      AND order_status = 'completed'
      AND order_date::date BETWEEN :p_from AND :p_to
    """
    cur  = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})

    total = float(cur.get("total_revenue") or 0)
    deliv = float(cur.get("delivery_revenue") or 0)
    pct   = round(deliv / total * 100, 2) if total > 0 else 0.0
    p_total = float(prev.get("total_revenue") or 0)
    p_deliv = float(prev.get("delivery_revenue") or 0)
    p_pct   = round(p_deliv / p_total * 100, 2) if p_total > 0 else 0.0

    return {
        "total_revenue":      round(total, 2),
        "delivery_revenue":   round(deliv, 2),
        "whatsapp_revenue":   round(float(cur.get("whatsapp_revenue") or 0), 2),
        "walkin_revenue":     round(float(cur.get("walkin_revenue") or 0), 2),
        "delivery_pct":       pct,
        "delivery_orders":    int(cur.get("delivery_orders") or 0),
        "total_orders":       int(cur.get("total_orders") or 0),
        "trend": _trend(pct, p_pct),
    }


# ── K_TL_12: Festive / Seasonal Uplift ───────────────────────────────────────

def calc_festive_uplift(engine, store_id: int, days: int = 90) -> dict:
    """Compare revenue on festival days vs. baseline non-festival weekdays."""
    p_from, p_to = _period(days)

    sql = """
    WITH dr AS (
        SELECT o.order_date::date AS d, SUM(o.total_amount) AS revenue
        FROM kirana_oltp.orders o
        WHERE o.store_id = :sid
          AND o.order_status = 'completed'
          AND o.order_date::date BETWEEN :p_from AND :p_to
        GROUP BY o.order_date::date
    ),
    enriched AS (
        SELECT dr.d, dr.revenue,
               c.festival, COALESCE(c.weight, 1.0) AS weight
        FROM dr
        LEFT JOIN kirana_oltp.calendar c ON c.cal_date = dr.d
    )
    SELECT
        AVG(revenue)                                                       AS overall_avg,
        AVG(revenue) FILTER (WHERE festival IS NOT NULL)                   AS festival_avg,
        AVG(revenue) FILTER (WHERE festival IS NULL)                       AS baseline_avg,
        COUNT(*)     FILTER (WHERE festival IS NOT NULL)                   AS festival_days,
        COUNT(*)     FILTER (WHERE festival IS NULL)                       AS baseline_days
    FROM enriched
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    fav  = float(r.get("festival_avg") or 0)
    base = float(r.get("baseline_avg") or 0)
    uplift = round((fav - base) / base * 100, 2) if base > 0 else 0.0

    # Top contributing festivals
    top_sql = """
    SELECT c.festival, SUM(o.total_amount) AS revenue, COUNT(DISTINCT o.order_id) AS orders
    FROM kirana_oltp.orders o
    JOIN kirana_oltp.calendar c ON c.cal_date = o.order_date::date
    WHERE o.store_id = :sid
      AND o.order_status = 'completed'
      AND o.order_date::date BETWEEN :p_from AND :p_to
      AND c.festival IS NOT NULL
    GROUP BY c.festival
    ORDER BY revenue DESC
    LIMIT 5
    """
    top = [{"festival": r["festival"], "revenue": float(r["revenue"]), "orders": int(r["orders"])}
           for r in _rows(engine, top_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})]

    return {
        "uplift_pct":    uplift,
        "festival_avg":  round(fav, 2),
        "baseline_avg":  round(base, 2),
        "festival_days": int(r.get("festival_days") or 0),
        "baseline_days": int(r.get("baseline_days") or 0),
        "top_festivals": top,
        "trend": _trend(uplift, None),
    }


# ── K_TL_14: WhatsApp Order Conversion ────────────────────────────────────────


    """Calculate conversion of WhatsApp sessions to actual orders.
    Logic:
      1. Find unique phone numbers in wa_sessions for this store.
      2. Join with kirana_oltp.customer to find matching customer_ids.
      3. Count how many of those customers placed orders in the period.
    """
    p_from, p_to = _period(days)

    sql = """
    WITH store_sessions AS (
        -- Unique phones that chatted with this store
        SELECT DISTINCT phone 
        FROM wa_sessions 
        WHERE store_id = :sid 
          AND (last_message_at >= :p_from OR updated_at >= :p_from)
    ),
    linked_customers AS (
        -- Map them to our customer master
        SELECT s.phone, c.customer_id
        FROM store_sessions s
        JOIN kirana_oltp.customer c ON regexp_replace(c.phone, '\D', '', 'g') = regexp_replace(s.phone, '\D', '', 'g')
    ),
    converting_customers AS (
        -- See who actually bought
        SELECT DISTINCT lc.customer_id
        FROM linked_customers lc
        JOIN kirana_oltp.orders o ON o.customer_id = lc.customer_id
        WHERE o.store_id = :sid
          AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
    )
    SELECT
        (SELECT COUNT(*) FROM store_sessions)        AS total_whatsapp_users,
        (SELECT COUNT(*) FROM converting_customers)  AS converted_users
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_users = int(r.get("total_whatsapp_users") or 0)
    converted   = int(r.get("converted_users") or 0)
    conv_pct    = round(converted * 100.0 / max(total_users, 1), 1)

    return {
        "total_whatsapp_users": total_users,
        "converted_users":      converted,
        "conversion_proxy_pct": conv_pct,
        "period_days":          days
    }




def calc_whatsapp_conversion(engine, store_id: int, days: int = 30) -> dict:
    """Calculate conversion of WhatsApp sessions and engagement metrics."""
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sessions_sql = """
    SELECT
        COUNT(*)                                                    AS total_sessions,
        COUNT(*) FILTER(WHERE state != 'new')                       AS active_sessions,
        COUNT(*) FILTER(WHERE state IN ('idle','sales_menu','analytics_menu','main_menu')) AS engaged,
        COUNT(*) FILTER(WHERE language='en')                        AS lang_en,
        COUNT(*) FILTER(WHERE language='te')                        AS lang_te,
        COUNT(*) FILTER(WHERE language='hi')                        AS lang_hi,
        COUNT(*) FILTER(WHERE state='main_menu')                    AS at_main_menu,
        COUNT(*) FILTER(WHERE state='sales_menu')                   AS at_sales,
        COUNT(*) FILTER(WHERE state='analytics_menu')               AS at_analytics,
        COUNT(*) FILTER(WHERE state='idle')                         AS completed_flow
    FROM wa_sessions
    WHERE store_id = :sid AND (last_message_at >= :p_from OR updated_at >= :p_from)
    """
    sr = _row(engine, sessions_sql, {"sid": store_id, "p_from": p_from})
    
    prev_sr = _row(engine, sessions_sql.replace(":p_from", ":pp_from"), {"sid": store_id, "pp_from": pp_from})

    msgs_sql = """
    SELECT
        COUNT(*) FILTER(WHERE m.direction='inbound')  AS received,
        COUNT(*) FILTER(WHERE m.direction='outbound') AS sent
    FROM wa_message_log m
    JOIN wa_sessions s ON m.phone = s.phone
    WHERE s.store_id = :sid AND m.created_at::date BETWEEN :p_from AND :p_to
    """
    mr = _row(engine, msgs_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_sess  = int(sr.get("total_sessions") or 0)
    engaged     = int(sr.get("engaged") or 0)
    conv_proxy  = round(engaged * 100.0 / max(total_sess, 1), 1)
    
    prev_total = int(prev_sr.get("total_sessions") or 0)
    prev_engaged = int(prev_sr.get("engaged") or 0)
    prev_conv = round(prev_engaged * 100.0 / max(prev_total, 1), 1)

    return {
        "total_sessions":          total_sess,
        "active_sessions":         int(sr.get("active_sessions") or 0),
        "language_breakdown": {
            "en": int(sr.get("lang_en") or 0),
            "te": int(sr.get("lang_te") or 0),
            "hi": int(sr.get("lang_hi") or 0),
        },
        "state_breakdown": {
            "main_menu":      int(sr.get("at_main_menu") or 0),
            "sales_menu":     int(sr.get("at_sales") or 0),
            "analytics_menu": int(sr.get("at_analytics") or 0),
            "completed":      int(sr.get("completed_flow") or 0),
        },
        "total_messages_sent":     int(mr.get("sent") or 0),
        "total_messages_received": int(mr.get("received") or 0),
        "avg_messages_per_session": round((int(mr.get("sent") or 0) + int(mr.get("received") or 0)) / max(total_sess, 1), 1),
        "conversion_proxy_pct":    conv_proxy,
        "trend": _trend(conv_proxy, prev_conv)
    }

    """Calculate conversion of WhatsApp sessions and engagement metrics."""
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sessions_sql = """
    SELECT
        COUNT(*)                                                    AS total_sessions,
        COUNT(*) FILTER(WHERE state != 'new')                       AS active_sessions,
        COUNT(*) FILTER(WHERE state IN ('idle','sales_menu','analytics_menu','main_menu')) AS engaged,
        COUNT(*) FILTER(WHERE language='en')                        AS lang_en,
        COUNT(*) FILTER(WHERE language='te')                        AS lang_te,
        COUNT(*) FILTER(WHERE language='hi')                        AS lang_hi,
        COUNT(*) FILTER(WHERE state='main_menu')                    AS at_main_menu,
        COUNT(*) FILTER(WHERE state='sales_menu')                   AS at_sales,
        COUNT(*) FILTER(WHERE state='analytics_menu')               AS at_analytics,
        COUNT(*) FILTER(WHERE state='idle')                         AS completed_flow
    FROM wa_sessions
    WHERE store_id = :sid AND (last_message_at >= :p_from OR updated_at >= :p_from)
    """
    sr = _row(engine, sessions_sql, {"sid": store_id, "p_from": p_from})
    
    prev_sr = _row(engine, sessions_sql.replace(":p_from", ":pp_from"), {"sid": store_id, "pp_from": pp_from})

    msgs_sql = """
    SELECT
        COUNT(*) FILTER(WHERE direction='inbound')  AS received,
        COUNT(*) FILTER(WHERE direction='outbound') AS sent
    FROM wa_message_log 
    WHERE store_id = :sid AND created_at::date BETWEEN :p_from AND :p_to
    """
    mr = _row(engine, msgs_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_sess  = int(sr.get("total_sessions") or 0)
    engaged     = int(sr.get("engaged") or 0)
    conv_proxy  = round(engaged * 100.0 / max(total_sess, 1), 1)
    
    prev_total = int(prev_sr.get("total_sessions") or 0)
    prev_engaged = int(prev_sr.get("engaged") or 0)
    prev_conv = round(prev_engaged * 100.0 / max(prev_total, 1), 1)

    return {
        "total_sessions":          total_sess,
        "active_sessions":         int(sr.get("active_sessions") or 0),
        "language_breakdown": {
            "en": int(sr.get("lang_en") or 0),
            "te": int(sr.get("lang_te") or 0),
            "hi": int(sr.get("lang_hi") or 0),
        },
        "state_breakdown": {
            "main_menu":      int(sr.get("at_main_menu") or 0),
            "sales_menu":     int(sr.get("at_sales") or 0),
            "analytics_menu": int(sr.get("at_analytics") or 0),
            "completed":      int(sr.get("completed_flow") or 0),
        },
        "total_messages_sent":     int(mr.get("sent") or 0),
        "total_messages_received": int(mr.get("received") or 0),
        "avg_messages_per_session": round((int(mr.get("sent") or 0) + int(mr.get("received") or 0)) / max(total_sess, 1), 1),
        "conversion_proxy_pct":    conv_proxy,
        "trend": _trend(conv_proxy, prev_conv)
    }


# ── K_TL_15: Household Wallet Share ───────────────────────────────────────────

def calc_household_wallet_share(engine, store_id: int, days: int = 30) -> dict:
    """
    Estimate family wallet share by comparing this store's spend vs.
    industry average for household grocery spend (~₹8,000-12,000/mo).
    """
    industry_avg_monthly = 10000.0
    p_from, p_to = _period(days)

    sql = """
    SELECT
        customer_id,
        SUM(total_amount) AS total_spend
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_status = 'completed'
      AND order_date BETWEEN :p_from AND :p_to
      AND customer_id IS NOT NULL
    GROUP BY customer_id
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    if not rows:
        return {"avg_share_pct": 0, "customer_count": 0, "wallet_estimate": industry_avg_monthly}

    shares = [round(min(1.0, float(r["total_spend"]) / industry_avg_monthly) * 100, 2)
              for r in rows]
    avg_share = round(sum(shares) / len(shares), 1)

    return {
        "avg_share_pct":    avg_share,
        "customer_count":   len(rows),
        "wallet_estimate":  industry_avg_monthly,
        "share_distribution": {
            "high (>70%)":   len([s for s in shares if s > 70]),
            "medium (30-70%)": len([s for s in shares if 30 <= s <= 70]),
            "low (<30%)":    len([s for s in shares if s < 30]),
        },
        "trend": _trend(avg_share, None),
    }


# ── K_BL_1: Udhar (Credit) Recovery ───────────────────────────────────────────

def calc_udhar_recovery(engine, store_id: int, days: int = 30) -> dict:
    sql = """
    SELECT
        COALESCE(SUM(amount), 0) AS total_outstanding,
        COALESCE(SUM(amount_paid), 0) AS total_paid,
        COALESCE(SUM(amount - amount_paid) FILTER (WHERE status = 'overdue'), 0) AS overdue_amount,
        COUNT(*) FILTER (WHERE status = 'open')      AS open_count,
        COUNT(*) FILTER (WHERE status = 'overdue')   AS overdue_count,
        COUNT(*) FILTER (WHERE status = 'settled')   AS settled_count,
        COUNT(*) FILTER (WHERE status = 'written_off') AS write_off_count
    FROM kirana_oltp.khata
    WHERE store_id = :sid
    """
    cur = _row(engine, sql, {"sid": store_id})
    total = float(cur.get("total_outstanding") or 0)
    paid  = float(cur.get("total_paid") or 0)
    recovery_pct = round((paid / total) * 100, 2) if total > 0 else 0.0

    top_sql = """
    SELECT k.khata_id, k.customer_id, c.name AS customer_name,
           k.amount, k.amount_paid, k.due_date, k.status,
           (CURRENT_DATE - k.due_date) AS days_overdue
    FROM kirana_oltp.khata k
    LEFT JOIN kirana_oltp.customer c ON c.customer_id = k.customer_id
    WHERE k.store_id = :sid AND k.status IN ('open', 'overdue')
    ORDER BY (k.amount - k.amount_paid) DESC
    LIMIT 10
    """
    top = [{"khata_id": int(r["khata_id"]),
            "customer_id": int(r["customer_id"]),
            "customer_name": r["customer_name"],
            "outstanding": float(r["amount"] or 0) - float(r["amount_paid"] or 0),
            "due_date": str(r["due_date"]),
            "status": r["status"],
            "days_overdue": int(r["days_overdue"] or 0)}
           for r in _rows(engine, top_sql, {"sid": store_id})]

    # Trend: recovery this month vs last month
    cur_paid = _scalar(engine, """
        SELECT COALESCE(SUM(amount_paid), 0) FROM kirana_oltp.khata
        WHERE store_id = :sid AND issue_date >= CURRENT_DATE - INTERVAL '30 days'
    """, {"sid": store_id}) or 0
    prev_paid = _scalar(engine, """
        SELECT COALESCE(SUM(amount_paid), 0) FROM kirana_oltp.khata
        WHERE store_id = :sid AND issue_date BETWEEN CURRENT_DATE - INTERVAL '60 days' AND CURRENT_DATE - INTERVAL '30 days'
    """, {"sid": store_id}) or 0

    return {
        "recovery_pct":      recovery_pct,
        "total_outstanding": round(total, 2),
        "total_recovered":   round(paid, 2),
        "overdue_amount":    round(float(cur.get("overdue_amount") or 0), 2),
        "counts": {
            "open":    int(cur.get("open_count") or 0),
            "overdue": int(cur.get("overdue_count") or 0),
            "settled": int(cur.get("settled_count") or 0),
            "write_off": int(cur.get("write_off_count") or 0),
        },
        "top_defaulters":    top,
        "trend": _trend(float(cur_paid), float(prev_paid)),
    }


# ── K_BL_2: Expiry & Wastage Loss ─────────────────────────────────────────────

def calc_expiry_wastage(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    sql = """
    WITH waste AS (
        SELECT p.category_id, c.name AS category_name,
               SUM(m.change_quantity * ps.cost_price) AS waste_value
        FROM kirana_oltp.inventory_movements m
        JOIN kirana_oltp.product p ON m.product_id = p.product_id
        JOIN kirana_oltp.category c ON p.category_id = c.category_id
        JOIN kirana_oltp.product_supplier ps ON p.product_id = ps.product_id
        WHERE m.store_id = :sid AND m.reason = 'expiry'
          AND m.created_at::date BETWEEN :p_from AND :p_to
        GROUP BY p.category_id, c.name
    ),
    revenue AS (
        SELECT SUM(total_amount) AS total_rev
        FROM kirana_oltp.orders
        WHERE store_id = :sid AND order_status = 'completed'
          AND order_date::date BETWEEN :p_from AND :p_to
    )
    SELECT w.*, r.total_rev
    FROM waste w CROSS JOIN revenue r
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_waste = sum(abs(float(r["waste_value"])) for r in rows)
    total_rev   = float(rows[0]["total_rev"] if rows else 1) or 1
    rate = round(total_waste / total_rev * 100, 2)

    # Trend
    prev_waste = abs(float(_scalar(engine, """
        SELECT COALESCE(SUM(m.change_quantity * ps.cost_price), 0)
        FROM kirana_oltp.inventory_movements m
        JOIN kirana_oltp.product_supplier ps ON m.product_id = ps.product_id
        WHERE m.store_id = :sid AND m.reason = 'expiry'
          AND m.created_at::date BETWEEN :pp_from AND :pp_to
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to}) or 0))

    return {
        "total_waste_value": round(total_waste, 2),
        "waste_rate_pct":    rate,
        "by_category":       [{"category_name": r["category_name"],
                               "waste_value": round(abs(float(r["waste_value"])), 2)} for r in rows],
        "trend": _trend(total_waste, prev_waste, higher_is_better=False),
    }


# ── K_BL_10: Electricity / Rent % of Rev (Overhead Ratio) ────────────────────

def calc_overhead_ratio(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)

    sql = """
    WITH expenses AS (
        SELECT 'electricity' as expense_type, SUM(electricity) AS total_amount FROM kirana_oltp.opex WHERE store_id = :sid AND month_start BETWEEN :p_from AND :p_to
        UNION ALL
        SELECT 'rent' as expense_type, SUM(rent) AS total_amount FROM kirana_oltp.opex WHERE store_id = :sid AND month_start BETWEEN :p_from AND :p_to
        UNION ALL
        SELECT 'staff' as expense_type, SUM(staff) AS total_amount FROM kirana_oltp.opex WHERE store_id = :sid AND month_start BETWEEN :p_from AND :p_to
        UNION ALL
        SELECT 'other' as expense_type, SUM(other) AS total_amount FROM kirana_oltp.opex WHERE store_id = :sid AND month_start BETWEEN :p_from AND :p_to
    ),
    revenue AS (
        SELECT COALESCE(SUM(total_amount), 0) AS total_rev
        FROM kirana_oltp.orders
        WHERE store_id = :sid AND order_status = 'completed'
          AND order_date::date BETWEEN :p_from AND :p_to
    )
    SELECT e.*, r.total_rev
    FROM expenses e CROSS JOIN revenue r
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_opex = sum(float(r["total_amount"] or 0) for r in rows)
    total_rev  = float((rows[0]["total_rev"] if rows else 0) or 0)
    ratio = round(total_opex / max(total_rev, 1) * 100, 2)

    return {
        "total_overhead": round(total_opex, 2),
        "total_revenue":  round(total_rev, 2),
        "ratio_pct":      ratio,
        "breakdown":      [{"type": r["expense_type"], "amount": float(r["total_amount"] or 0)} for r in rows],
        "trend": _trend(ratio, 0.0, higher_is_better=False),
    }


# ── K_BL_11: Supplier Fill Rate ───────────────────────────────────────────────

def calc_supplier_fill_rate(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)

    sql = """
    SELECT pu.supplier_id, s.name AS supplier_name,
           SUM(pi.requested_qty) AS ordered_qty,
           SUM(pi.quantity) AS received_qty
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id = pi.purchase_id
    JOIN kirana_oltp.supplier s ON pu.supplier_id = s.supplier_id
    WHERE pu.store_id = :sid AND pu.order_date BETWEEN :p_from AND :p_to
      AND pu.arrival_date IS NOT NULL
    GROUP BY pu.supplier_id, s.name
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    if not rows:
        return {"fill_pct": 100, "by_supplier": []}

    total_ordered = sum(int(r["ordered_qty"]) for r in rows)
    total_received = sum(int(r["received_qty"]) for r in rows)
    overall_fill = round(total_received * 100.0 / max(total_ordered, 1), 2)

    return {
        "fill_pct": overall_fill,
        "total_ordered": total_ordered,
        "total_received": total_received,
        "by_supplier": [{"name": r["supplier_name"],
                         "fill_pct": round(int(r["received_qty"])*100.0/max(int(r["ordered_qty"]),1), 2)}
                        for r in rows],
        "trend": _trend(overall_fill, None),
    }


# ── K_BL_13: Return-to-Vendor Recovery ───────────────────────────────────────

def calc_rtv_recovery(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)

    sql = """
    SELECT
        COUNT(*) AS total_returns,
        COUNT(*) FILTER (WHERE amount_recovered > 0) AS recovered_count,
        COALESCE(SUM(qty_returned * unit_cost), 0) AS estimated_loss,
        COALESCE(SUM(amount_recovered), 0) AS recovered_amount
    FROM kirana_oltp.return_to_vendor
    WHERE store_id = :sid AND return_date BETWEEN :p_from AND :p_to
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    est = float(r.get("estimated_loss") or 0)
    rec = float(r.get("recovered_amount") or 0)
    pct = round(rec / est * 100, 2) if est > 0 else 0.0

    return {
        "recovery_pct":     pct,
        "estimated_loss":   round(est, 2),
        "recovered_amount": round(rec, 2),
        "total_returns":    int(r.get("total_returns") or 0),
        "trend": _trend(pct, None),
    }


# ── K_BL_16: Near-Expiry Markdown Recovery ───────────────────────────────────

def calc_markdown_recovery(engine, store_id: int, days: int = 30) -> dict:
    """
    Measure revenue recovered from items sold under 'markdown' or 'clearance'
    compared to their original cost value.
    """
    p_from, p_to = _period(days)

    sql = """
    SELECT
        COUNT(DISTINCT oi.product_id) AS sku_count,
        SUM(oi.quantity * oi.unit_price) AS markdown_revenue,
        SUM(oi.quantity * oi.cost_price) AS cost_value
    FROM kirana_oltp.order_item oi
    JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
    WHERE o.store_id = :sid AND o.order_status = 'completed'
      AND o.order_date BETWEEN :p_from AND :p_to
      AND (oi.unit_price < oi.cost_price )
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    rev  = float(r.get("markdown_revenue") or 0)
    cost = float(r.get("cost_value") or 0)
    # Recovery % = how much of the cost was recovered vs. letting it expire (0 recovery)
    pct = round(rev / cost * 100, 2) if cost > 0 else 0.0

    return {
        "recovery_pct":      pct,
        "markdown_revenue":  round(rev, 2),
        "cost_value":        round(cost, 2),
        "sku_count":         int(r.get("sku_count") or 0),
        "trend": _trend(pct, None),
    }


# ── Common (All Verticals) ────────────────────────────────────────────────────

def calc_customer_ltv(engine, store_id: int | None = None) -> dict:
    sql = """
    SELECT AVG(total_spend) AS avg_ltv,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_spend) AS median_ltv,
           MAX(total_spend) AS top_ltv
    FROM (
        SELECT customer_id, SUM(total_amount) AS total_spend
        FROM kirana_oltp.orders
        WHERE order_status = 'completed'
          """ + ("AND store_id = :sid" if store_id else "") + """
        GROUP BY customer_id
    ) x
    """
    params = {"sid": store_id} if store_id else {}
    r = _row(engine, sql, params)
    val = float(r.get("avg_ltv") or 0)
    return {
        "avg_ltv":    round(val, 2),
        "median_ltv": round(float(r.get("median_ltv") or 0), 2),
        "top_ltv":    round(float(r.get("top_ltv") or 0), 2),
        "trend": _trend(val, None),
    }

def calc_nrr(engine, store_id: int | None = None, days: int = 365) -> dict:
    """Net Revenue Retention: (Revenue from existing customers) / (Revenue from same customers in prev period)"""
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)
    
    # Identify customers active in prev period
    sql_prev = "SELECT DISTINCT customer_id FROM kirana_oltp.orders WHERE order_date BETWEEN :pp_from AND :pp_to AND customer_id IS NOT NULL"
    with engine.connect() as conn:
        prev_ids = [r[0] for r in conn.execute(text(sql_prev), {"pp_from": pp_from, "pp_to": pp_to}).all()]
    
    if not prev_ids:
        return {"nrr_pct": 0, "status": "No baseline customers", "trend": _trend(0, None)}

    # Current revenue from those specific customers
    sql_cur = "SELECT SUM(total_amount) FROM kirana_oltp.orders WHERE customer_id IN :ids AND order_date BETWEEN :p_from AND :p_to"
    cur_rev = float(_scalar(engine, sql_cur, {"ids": tuple(prev_ids), "p_from": p_from, "p_to": p_to}) or 0)

    # Baseline revenue from those specific customers in prev period
    sql_base = "SELECT SUM(total_amount) FROM kirana_oltp.orders WHERE customer_id IN :ids AND order_date BETWEEN :pp_from AND :pp_to"
    base_rev = float(_scalar(engine, sql_base, {"ids": tuple(prev_ids), "pp_from": pp_from, "pp_to": pp_to}) or 1)
    
    nrr = round(cur_rev / base_rev * 100, 2)
    return {"nrr_pct": nrr, "baseline_revenue": round(base_rev, 2), "retained_revenue": round(cur_rev, 2), "trend": _trend(nrr, None)}

def calc_arpu(engine, store_id: int | None = None, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT ROUND(SUM(total_amount) / NULLIF(COUNT(DISTINCT customer_id), 0), 2) AS arpu
    FROM kirana_oltp.orders
    WHERE order_status = 'completed' AND customer_id IS NOT NULL
      AND order_date BETWEEN :p_from AND :p_to
    """ + (" AND store_id = :sid" if store_id else "")
    params = {"p_from": p_from, "p_to": p_to}
    if store_id: params["sid"] = store_id
    val = float(_scalar(engine, sql, params) or 0)
    return {"arpu": val, "trend": _trend(val, None)}

def calc_brand_conversion(engine, store_id: int | None = None, days: int = 90) -> dict:
    # Placeholder: Brand deals/investments aren't fully modeled in tables yet
    return {"conversion_pct": 0.0, "status": "Data source pending (Brand Deals table)", "trend": _trend(0.0, None)}

def calc_cac_payback(engine, store_id: int = None, days: int = 90) -> dict:
    p_from, p_to = _period(days)

    sql = """
    SELECT
        COALESCE(SUM(amount), 0)                  AS total_spend,
        COALESCE(SUM(attributed_customers), 0)    AS new_customers
    FROM kirana_oltp.marketing_spend
    WHERE spend_date BETWEEN :p_from AND :p_to
    """ + (" AND store_id = :sid" if store_id else "")
    params = {"p_from": p_from, "p_to": p_to}
    if store_id:
        params["sid"] = store_id
    r = _row(engine, sql, params)

    spend = float(r.get("total_spend") or 0)
    new_cust = int(r.get("new_customers") or 0)
    cac = round(spend / new_cust, 2) if new_cust > 0 else 0.0

    # Use avg basket value × ~5 visits/yr as monthly contribution proxy
    abv_sql = """
    SELECT COALESCE(AVG(total_amount), 0) AS abv
    FROM kirana_oltp.orders
    WHERE order_status = 'completed'
      AND order_date::date BETWEEN :p_from AND :p_to
    """ + (" AND store_id = :sid" if store_id else "")
    abv = float(_scalar(engine, abv_sql, params) or 0)
    margin = 0.20  # rough industry gross margin
    monthly_contribution = abv * margin * 4  # ~4 visits per month per active customer
    payback_months = round(cac / monthly_contribution, 1) if monthly_contribution > 0 else 0.0

    return {
        "marketing_spend":      round(spend, 2),
        "new_customers":        new_cust,
        "cac":                  cac,
        "avg_basket":           round(abv, 2),
        "estimated_monthly_contribution": round(monthly_contribution, 2),
        "payback_months":       payback_months,
        "trend": _trend(payback_months, None, higher_is_better=False),
    }

def calc_working_capital_cycle(engine, store_id: int = None) -> dict:
    # Inventory Days + AR Days - AP Days
    # AR Days = (Khata / Revenue) * 365
    rev_sql = "SELECT COALESCE(SUM(total_amount),1) FROM kirana_oltp.orders WHERE order_date >= CURRENT_DATE - 365"
    ar_sql = "SELECT COALESCE(SUM(amount - amount_paid),0) FROM kirana_oltp.khata"
    inv_sql = "SELECT COALESCE(SUM(quantity * 50),0) FROM kirana_oltp.inventory" # rough value
    
    rev = float(_scalar(engine, rev_sql, {}) or 1)
    ar = float(_scalar(engine, ar_sql, {}) or 0)
    inv = float(_scalar(engine, inv_sql, {}) or 0)
    
    ar_days = (ar / rev) * 365
    inv_days = (inv / rev) * 365
    ap_days = 15 # estimate
    
    cycle = round(inv_days + ar_days - ap_days, 1)
    return {"working_capital_days": cycle, "inventory_days": round(inv_days,1), "ar_days": round(ar_days,1), "trend": _trend(cycle, None, higher_is_better=False)}

def calc_ops_cost_per_outlet(engine, store_id: int = None) -> dict:
    sql = "SELECT COALESCE(SUM(electricity + rent + staff + other), 0) AS total FROM kirana_oltp.opex"
    count_sql = "SELECT COUNT(*) FROM kirana_oltp.store WHERE is_deleted=FALSE"
    total = float(_scalar(engine, sql, {}) or 0)
    count = int(_scalar(engine, count_sql, {}) or 1)
    avg = round(total / count, 2)
    return {"avg_cost_per_outlet": avg, "total_ops_cost": total, "outlet_count": count, "trend": _trend(avg, None, higher_is_better=False)}

def calc_ai_roi(engine, store_id: int = None) -> dict:
    # (Savings from Expiry + Stockout Recovery) / AI Cost (₹599)
    waste_saved = 1500.0 # hypothetical
    stockout_rec = 2500.0 # hypothetical
    cost = 599.0
    roi = round((waste_saved + stockout_rec) / cost, 2)
    return {"roi_multiplier": roi, "total_savings": waste_saved + stockout_rec, "monthly_subscription": cost, "trend": _trend(roi, None)}

def calc_customer_credit_risk(engine, store_id: int = None) -> dict:
    sql = """
    SELECT ROUND(SUM(amount - amount_paid) * 100.0 / NULLIF(SUM(amount), 0), 2) AS risk_pct
    FROM kirana_oltp.khata
    WHERE status != 'settled'
    """
    val = float(_scalar(engine, sql, {}) or 0)
    return {"risk_pct": val, "trend": _trend(val, None, higher_is_better=False)}

def calc_process_automation(engine, store_id: int = None) -> dict:
    # Ratio of auto-generated orders / total orders
    return {"automation_pct": 53.92, "status": "Partial simulation", "trend": _trend(53.92, None)}

def calc_private_label(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT
        COALESCE(SUM(total_amount), 0) AS total_revenue,
        COALESCE(SUM(total_amount) FILTER (WHERE p.brand = 'Store Brand'), 0) AS private_label_revenue
    FROM kirana_oltp.orders o
    JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
    JOIN kirana_oltp.product p ON oi.product_id = p.product_id
    WHERE o.store_id = :sid AND o.order_status = 'completed'
      AND o.order_date BETWEEN :p_from AND :p_to
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    total = float(r.get("total_revenue") or 0)
    pl_rev = float(r.get("private_label_revenue") or 0)
    pct = round(pl_rev * 100.0 / max(total, 1), 2)
    return {"total_revenue": total, "private_label_revenue": pl_rev, "private_label_pct": pct, "trend": _trend(pct, None)}


def calc_shelf_productivity(engine, store_id: int) -> dict:
    sql = """
    SELECT 
        COALESCE(SUM(total_amount), 0) AS total_revenue,
        (SELECT COALESCE(SUM(sq_ft), 100) FROM kirana_oltp.shelf_planogram WHERE store_id = :sid) AS total_sqft
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_status = 'completed'
      AND order_date >= CURRENT_DATE - INTERVAL '30 days'
    """
    r = _row(engine, sql, {"sid": store_id})
    rev = float(r.get("total_revenue") or 0)
    sqft = float(r.get("total_sqft") or 100)
    val = round(rev/sqft, 2)
    return {"total_revenue": rev, "shelf_sqft": sqft, "rev_per_sqft": val, "trend": _trend(val, None)}

