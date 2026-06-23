from datetime import date

from .core import _period, _prev_period, _row, _rows, _scalar, _trend


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
    rows = _rows(
        engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to, "days": days}
    )
    if not rows:
        return {
            "total_revenue": 0,
            "overall_margin_pct": 0,
            "category_count": 0,
            "mix_score": 0,
            "categories": [],
            "top_opportunity": "No data",
            "trend": _trend(None, None),
        }

    total_rev = float(rows[0].get("total_revenue") or 0)
    overall_margin = float(rows[0].get("overall_margin") or 0)

    # BCG quadrant: high share + high margin = star, etc.
    _shares = sorted(float(r.get("revenue_share_pct") or 0) for r in rows)
    _margins = sorted(float(r.get("margin_pct") or 0) for r in rows)
    median_share = _shares[len(_shares) // 2] if _shares else 0.0
    median_margin = _margins[len(_margins) // 2] if _margins else 0.0

    def _bcg(rev_share, margin):
        hs = float(rev_share) >= median_share
        hm = float(margin) >= median_margin
        return (
            "star"
            if hs and hm
            else ("cash_cow" if hs else ("question_mark" if hm else "dog"))
        )

    categories = []
    for r in rows:
        categories.append(
            {
                "category_id": int(r["category_id"]),
                "category_name": r["cat_name"],
                "revenue": float(r.get("revenue") or 0),
                "revenue_share_pct": float(r.get("revenue_share_pct") or 0),
                "margin_pct": float(r.get("margin_pct") or 0),
                "avg_units_per_day": float(r.get("avg_units_per_day") or 0),
                "bcg_quadrant": _bcg(r.get("revenue_share_pct"), r.get("margin_pct")),
            }
        )

    stars = [c for c in categories if c["bcg_quadrant"] == "star"]
    dogs = [c for c in categories if c["bcg_quadrant"] == "dog"]
    qm = [c for c in categories if c["bcg_quadrant"] == "question_mark"]

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
    prev_margin = _scalar(
        engine, pm_sql, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to}
    )

    return {
        "total_revenue": total_rev,
        "overall_margin_pct": overall_margin,
        "category_count": len(rows),
        "mix_score": mix_score,
        "categories": categories,
        "top_opportunity": opp,
        "trend": _trend(overall_margin, float(prev_margin or 0)),
    }


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
    digital_txn = sum(
        int(r["txn_count"]) for r in rows if r["payment_method"] in ("upi", "card")
    )
    digital_pct = round(digital_txn * 100.0 / max(total_txn, 1), 1)
    cash_pct = round(100 - digital_pct, 1)

    by_method = [
        {
            "method": r["payment_method"],
            "count": int(r["txn_count"]),
            "amount": round(float(r["total_amount"] or 0), 2),
            "share_pct": round(int(r["txn_count"]) * 100.0 / max(total_txn, 1), 1),
        }
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
    weekly = _rows(
        engine, weekly_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

    pp_from, pp_to = _prev_period(days)
    prev_pct = _scalar(
        engine,
        """
    SELECT COUNT(*) FILTER(WHERE p.payment_method IN ('upi','card'))*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.payments p JOIN kirana_oltp.orders o ON o.order_id=p.order_id
    WHERE o.store_id=:sid AND o.order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    return {
        "digital_pct": digital_pct,
        "cash_pct": cash_pct,
        "total_transactions": total_txn,
        "total_amount": round(total_amt, 2),
        "by_method": by_method,
        "weekly_trend": [
            {
                "week": str(w["week"]),
                "digital": int(w["digital"]),
                "total": int(w["total"]),
                "digital_pct": float(w["digital_pct"] or 0),
            }
            for w in weekly
        ],
        "trend": _trend(digital_pct, float(prev_pct or 0)),
    }


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
        return {
            "trial_window_days": trial_days,
            "new_products_count": 0,
            "success_rate_pct": 0,
            "avg_units_sold_30d": 0,
            "products": [],
            "trend": _trend(None, None),
        }

    # Label success: top 33% units = hit, bottom 33% = slow
    all_units = sorted(int(r["units_30d"]) for r in rows)
    n = len(all_units)
    p33 = all_units[max(0, n // 3 - 1)] if n >= 3 else (all_units[0] if n else 0)
    p67 = all_units[max(0, 2 * n // 3 - 1)] if n >= 3 else (all_units[-1] if n else 0)

    products = []
    for r in rows:
        u = int(r["units_30d"])
        label = "hit" if u >= p67 else ("average" if u >= p33 else "slow")
        products.append(
            {
                "product_id": int(r["product_id"]),
                "product_name": r["product_name"],
                "category_name": r["category_name"],
                "days_since_launch": int(r["days_since_launch"]),
                "units_sold_30d": u,
                "revenue_30d": round(float(r["revenue_30d"] or 0), 2),
                "success_label": label,
                "predicted_success_prob": None,
            }
        )

    hits = sum(1 for p in products if p["success_label"] == "hit")
    success_pct = round(hits * 100.0 / max(len(products), 1), 1)
    avg_units = round(
        sum(p["units_sold_30d"] for p in products) / max(len(products), 1), 1
    )

    return {
        "trial_window_days": trial_days,
        "new_products_count": len(products),
        "success_rate_pct": success_pct,
        "avg_units_sold_30d": avg_units,
        "products": products[:20],
        "trend": _trend(success_pct, None),
    }


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
    prev_pct = _scalar(
        engine,
        """
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
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    cur_pct = float(r.get("multi_cat_pct") or 0)
    return {
        "total_orders": int(r.get("total_orders") or 0),
        "multi_category_orders": int(r.get("multi_cat_orders") or 0),
        "multi_category_pct": cur_pct,
        "avg_categories_per_order": float(r.get("avg_cats") or 0),
        "orders_3plus_cat_pct": float(r.get("three_plus_pct") or 0),
        "top_pairs": [
            {
                "category_a": p["cat_a"],
                "category_b": p["cat_b"],
                "co_occurrences": int(p["co_occ"]),
                "lift": float(p["lift"] or 0),
            }
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
    converted = int(r.get("converted_users") or 0)
    conv_pct = round(converted * 100.0 / max(total_users, 1), 1)

    return {
        "total_whatsapp_users": total_users,
        "converted_users": converted,
        "conversion_proxy_pct": conv_pct,
        "period_days": days,
    }


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

    total_actual = sum(float(r.get("total_actual") or 0) for r in rows)
    total_standard = sum(float(r.get("total_standard") or 0) for r in rows)
    net_savings = sum(float(r.get("savings") or 0) for r in rows)
    savings_pct = round(net_savings / max(total_standard, 1) * 100, 2)
    overpay_count = sum(1 for r in rows if float(r.get("savings") or 0) < 0)
    underpay_count = sum(1 for r in rows if float(r.get("savings") or 0) > 0)

    pp_from, pp_to = _prev_period(days)
    prev_sp = _scalar(
        engine,
        """
    SELECT SUM(ps.cost_price - pi.cost_price)*100/NULLIF(SUM(ps.cost_price),0)
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
    JOIN kirana_oltp.product_supplier ps ON pi.product_id=ps.product_id AND pu.supplier_id=ps.supplier_id
    WHERE pu.store_id=:sid AND pu.order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    return {
        "total_purchased_value": round(total_actual, 2),
        "total_standard_value": round(total_standard, 2),
        "net_savings": round(net_savings, 2),
        "savings_pct": savings_pct,
        "overpay_count": overpay_count,
        "underpay_count": underpay_count,
        "by_supplier": [
            {
                "supplier_id": int(r["supplier_id"]),
                "supplier_name": r["supplier_name"],
                "total_purchased_value": float(r.get("total_actual") or 0),
                "standard_value": float(r.get("total_standard") or 0),
                "actual_savings": float(r.get("savings") or 0),
                "savings_pct": float(r.get("savings_pct") or 0),
            }
            for r in rows
        ],
        "trend": _trend(savings_pct, float(prev_sp or 0)),
    }


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
        max(0, float(r.get("price_variance_pct") or 0))
        * float(r.get("avg_standard") or 0)
        * int(r.get("order_count") or 0)
        for r in rows
    )
    best = rows[0] if rows else {}

    def _reliability(r):
        price_var = abs(float(r.get("price_variance_pct") or 0))
        lt_acc = float(r.get("lead_time_accuracy") or 50)
        return round(
            max(0, min(100, lt_acc * 0.6 + max(0, 100 - price_var * 5) * 0.4)), 1
        )

    return {
        "total_suppliers": len(rows),
        "best_supplier_id": int(best.get("supplier_id") or 0),
        "best_supplier_name": str(best.get("supplier_name") or ""),
        "total_overpay_opportunity": round(overpay_opp, 2),
        "by_supplier": [
            {
                "supplier_id": int(r["supplier_id"]),
                "supplier_name": r["supplier_name"],
                "total_orders": int(r.get("order_count") or 0),
                "avg_actual_cost": round(float(r.get("avg_actual") or 0), 2),
                "avg_standard_cost": round(float(r.get("avg_standard") or 0), 2),
                "price_variance_pct": float(r.get("price_variance_pct") or 0),
                "reliability_score": _reliability(r),
                "lead_time_accuracy_pct": float(r.get("lead_time_accuracy") or 0),
                "recommendation": "Negotiate better rates"
                if float(r.get("price_variance_pct") or 0) > 5
                else (
                    "Reliable — prefer for critical SKUs"
                    if _reliability(r) >= 80
                    else "Monitor lead times"
                ),
            }
            for r in rows
        ],
        "trend": _trend(
            100
            - overpay_opp
            / max(sum(float(r.get("avg_standard") or 0) for r in rows), 1)
            * 100,
            None,
        ),
    }


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
    brackets = _rows(
        engine, brackets_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

    prev_avg = _scalar(
        engine,
        """
    SELECT AVG(total_amount) FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_status='completed'
      AND order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    cur = float(r.get("avg_basket") or 0)
    return {
        "avg_basket_value": cur,
        "median_basket_value": float(r.get("median_basket") or 0),
        "max_basket_value": float(r.get("max_basket") or 0),
        "min_basket_value": float(r.get("min_basket") or 0),
        "order_count": int(r.get("order_count") or 0),
        "total_revenue": float(r.get("total_revenue") or 0),
        "brackets": [
            {
                "bracket": b["bracket"],
                "order_count": int(b["order_count"]),
                "avg_value": float(b["avg_value"] or 0),
            }
            for b in brackets
        ],
        "trend": _trend(cur, float(prev_avg or 0)),
    }


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
    by_status = _rows(
        engine, by_reason_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

    prev_rate = _scalar(
        engine,
        """
    SELECT COUNT(*) FILTER(WHERE order_status IN ('cancelled','returned'))*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    cur = float(r.get("return_rate") or 0)
    return {
        "total_orders": int(r.get("total_orders") or 0),
        "returned_orders": int(r.get("returned_orders") or 0),
        "return_rate_pct": cur,
        "returned_value": float(r.get("returned_value") or 0),
        "by_status": [
            {
                "status": s["order_status"],
                "count": int(s["count"]),
                "value": float(s["value"] or 0),
            }
            for s in by_status
        ],
        "trend": _trend(100 - cur, float(100 - (prev_rate or 0))),
    }


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
    cur = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})

    visitors = int(cur.get("total_visitors") or 0)
    bills = int(cur.get("total_bills") or 0)
    rate = round((bills / visitors) * 100, 2) if visitors > 0 else 0.0

    p_visitors = int(prev.get("total_visitors") or 0)
    p_bills = int(prev.get("total_bills") or 0)
    prev_rate = round((p_bills / p_visitors) * 100, 2) if p_visitors > 0 else 0.0

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
        {
            "hour": int(r["hour"]),
            "visitors": int(r["visitors"] or 0),
            "bills": int(r["bills"] or 0),
            "conversion_pct": round(
                (int(r["bills"] or 0) / max(int(r["visitors"] or 0), 1)) * 100, 2
            ),
        }
        for r in rows
    ]

    return {
        "total_visitors": visitors,
        "total_bills": bills,
        "conversion_pct": rate,
        "by_hour": by_hour,
        "trend": _trend(rate, prev_rate),
    }


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
    cur = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})

    total = int(cur.get("total") or 0)
    claimed = int(cur.get("claimed") or 0)
    missed = int(cur.get("missed") or 0)
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
        {
            "scheme_id": int(r["scheme_id"]),
            "name": r["name"],
            "type": r["scheme_type"],
            "value": float(r["value"] or 0),
            "ends": str(r["end_date"]),
        }
        for r in active
    ]

    return {
        "claimed": claimed,
        "missed": missed,
        "total": total,
        "capture_pct": capture_pct,
        "amount_saved": round(saved, 2),
        "open_opportunities": active_open,
        "trend": _trend(capture_pct, prev_pct),
    }


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
    cur = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})

    total = float(cur.get("total_revenue") or 0)
    deliv = float(cur.get("delivery_revenue") or 0)
    pct = round(deliv / total * 100, 2) if total > 0 else 0.0
    p_total = float(prev.get("total_revenue") or 0)
    p_deliv = float(prev.get("delivery_revenue") or 0)
    p_pct = round(p_deliv / p_total * 100, 2) if p_total > 0 else 0.0

    return {
        "total_revenue": round(total, 2),
        "delivery_revenue": round(deliv, 2),
        "whatsapp_revenue": round(float(cur.get("whatsapp_revenue") or 0), 2),
        "walkin_revenue": round(float(cur.get("walkin_revenue") or 0), 2),
        "delivery_pct": pct,
        "delivery_orders": int(cur.get("delivery_orders") or 0),
        "total_orders": int(cur.get("total_orders") or 0),
        "trend": _trend(pct, p_pct),
    }


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
    fav = float(r.get("festival_avg") or 0)
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
    top = [
        {
            "festival": r["festival"],
            "revenue": float(r["revenue"]),
            "orders": int(r["orders"]),
        }
        for r in _rows(
            engine, top_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
        )
    ]

    return {
        "uplift_pct": uplift,
        "festival_avg": round(fav, 2),
        "baseline_avg": round(base, 2),
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
    converted = int(r.get("converted_users") or 0)
    conv_pct = round(converted * 100.0 / max(total_users, 1), 1)

    return {
        "total_whatsapp_users": total_users,
        "converted_users": converted,
        "conversion_proxy_pct": conv_pct,
        "period_days": days,
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

    prev_sr = _row(
        engine,
        sessions_sql.replace(":p_from", ":pp_from"),
        {"sid": store_id, "pp_from": pp_from},
    )

    msgs_sql = """
    SELECT
        COUNT(*) FILTER(WHERE m.direction='inbound')  AS received,
        COUNT(*) FILTER(WHERE m.direction='outbound') AS sent
    FROM wa_message_log m
    JOIN wa_sessions s ON m.phone = s.phone
    WHERE s.store_id = :sid AND m.created_at::date BETWEEN :p_from AND :p_to
    """
    mr = _row(engine, msgs_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_sess = int(sr.get("total_sessions") or 0)
    engaged = int(sr.get("engaged") or 0)
    conv_proxy = round(engaged * 100.0 / max(total_sess, 1), 1)

    prev_total = int(prev_sr.get("total_sessions") or 0)
    prev_engaged = int(prev_sr.get("engaged") or 0)
    prev_conv = round(prev_engaged * 100.0 / max(prev_total, 1), 1)

    return {
        "total_sessions": total_sess,
        "active_sessions": int(sr.get("active_sessions") or 0),
        "language_breakdown": {
            "en": int(sr.get("lang_en") or 0),
            "te": int(sr.get("lang_te") or 0),
            "hi": int(sr.get("lang_hi") or 0),
        },
        "state_breakdown": {
            "main_menu": int(sr.get("at_main_menu") or 0),
            "sales_menu": int(sr.get("at_sales") or 0),
            "analytics_menu": int(sr.get("at_analytics") or 0),
            "completed": int(sr.get("completed_flow") or 0),
        },
        "total_messages_sent": int(mr.get("sent") or 0),
        "total_messages_received": int(mr.get("received") or 0),
        "avg_messages_per_session": round(
            (int(mr.get("sent") or 0) + int(mr.get("received") or 0))
            / max(total_sess, 1),
            1,
        ),
        "conversion_proxy_pct": conv_proxy,
        "trend": _trend(conv_proxy, prev_conv),
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

    prev_sr = _row(
        engine,
        sessions_sql.replace(":p_from", ":pp_from"),
        {"sid": store_id, "pp_from": pp_from},
    )

    msgs_sql = """
    SELECT
        COUNT(*) FILTER(WHERE direction='inbound')  AS received,
        COUNT(*) FILTER(WHERE direction='outbound') AS sent
    FROM wa_message_log 
    WHERE store_id = :sid AND created_at::date BETWEEN :p_from AND :p_to
    """
    mr = _row(engine, msgs_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    total_sess = int(sr.get("total_sessions") or 0)
    engaged = int(sr.get("engaged") or 0)
    conv_proxy = round(engaged * 100.0 / max(total_sess, 1), 1)

    prev_total = int(prev_sr.get("total_sessions") or 0)
    prev_engaged = int(prev_sr.get("engaged") or 0)
    prev_conv = round(prev_engaged * 100.0 / max(prev_total, 1), 1)

    return {
        "total_sessions": total_sess,
        "active_sessions": int(sr.get("active_sessions") or 0),
        "language_breakdown": {
            "en": int(sr.get("lang_en") or 0),
            "te": int(sr.get("lang_te") or 0),
            "hi": int(sr.get("lang_hi") or 0),
        },
        "state_breakdown": {
            "main_menu": int(sr.get("at_main_menu") or 0),
            "sales_menu": int(sr.get("at_sales") or 0),
            "analytics_menu": int(sr.get("at_analytics") or 0),
            "completed": int(sr.get("completed_flow") or 0),
        },
        "total_messages_sent": int(mr.get("sent") or 0),
        "total_messages_received": int(mr.get("received") or 0),
        "avg_messages_per_session": round(
            (int(mr.get("sent") or 0) + int(mr.get("received") or 0))
            / max(total_sess, 1),
            1,
        ),
        "conversion_proxy_pct": conv_proxy,
        "trend": _trend(conv_proxy, prev_conv),
    }


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
    total_rev = float((rows[0]["total_rev"] if rows else 0) or 0)
    ratio = round(total_opex / max(total_rev, 1) * 100, 2)

    return {
        "total_overhead": round(total_opex, 2),
        "total_revenue": round(total_rev, 2),
        "ratio_pct": ratio,
        "breakdown": [
            {"type": r["expense_type"], "amount": float(r["total_amount"] or 0)}
            for r in rows
        ],
        "trend": _trend(ratio, 0.0, higher_is_better=False),
    }


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
    payback_months = (
        round(cac / monthly_contribution, 1) if monthly_contribution > 0 else 0.0
    )

    return {
        "marketing_spend": round(spend, 2),
        "new_customers": new_cust,
        "cac": cac,
        "avg_basket": round(abv, 2),
        "estimated_monthly_contribution": round(monthly_contribution, 2),
        "payback_months": payback_months,
        "trend": _trend(payback_months, None, higher_is_better=False),
    }


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
    return {
        "total_revenue": total,
        "private_label_revenue": pl_rev,
        "private_label_pct": pct,
        "trend": _trend(pct, None),
    }
