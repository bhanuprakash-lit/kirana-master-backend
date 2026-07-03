from .core import _period, _prev_period, _row, _rows, _scalar, _trend


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

    total_orders = len(rows)
    problematic = [r for r in rows if r["issue_type"] != "clean"]
    unpaid = [r for r in rows if r["issue_type"] == "unpaid"]
    mismatch = [r for r in rows if r["issue_type"] in ("underpaid", "overpaid")]
    total_leakage = sum(float(r.get("gap") or 0) for r in problematic)
    leakage_rate_pct = round(len(problematic) * 100.0 / max(total_orders, 1), 2)

    pp_from, pp_to = _prev_period(days)
    prev_rate = _scalar(
        engine,
        """
    SELECT COUNT(*) FILTER(WHERE p.payment_id IS NULL OR ABS(o.total_amount - p.amount) > 1)*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.orders o LEFT JOIN kirana_oltp.payments p ON o.order_id=p.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    return {
        "total_orders": total_orders,
        "clean_orders": total_orders - len(problematic),
        "problematic_orders": len(problematic),
        "total_leakage_value": round(total_leakage, 2),
        "leakage_rate_pct": leakage_rate_pct,
        "unpaid_count": len(unpaid),
        "mismatch_count": len(mismatch),
        "flagged_orders": [
            {
                "order_id": int(r["order_id"]),
                "order_date": r["order_date"],
                "order_total": float(r.get("total_amount") or 0),
                "payment_amount": float(r["payment_amount"])
                if r.get("payment_amount") is not None
                else None,
                "gap": float(r.get("gap") or 0),
                "issue_type": r["issue_type"],
            }
            for r in problematic[:50]
        ],
        "trend": _trend(100 - leakage_rate_pct, float(100 - (prev_rate or 0))),
    }


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
    r = _row(
        engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to, "days": days}
    )

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

    prev_rev = _scalar(
        engine,
        """
    SELECT SUM(total_amount) FROM kirana_oltp.orders
    WHERE store_id=:sid AND order_status='completed'
      AND order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    cur = float(r.get("total_revenue") or 0)
    return {
        "total_revenue": cur,
        "avg_daily_revenue": float(r.get("daily_avg") or 0),
        "order_count": int(r.get("order_count") or 0),
        "daily_breakdown": [
            {
                "day": str(d["day"]),
                "revenue": float(d["revenue"] or 0),
                "orders": int(d["orders"]),
            }
            for d in daily
        ],
        "trend": _trend(cur, float(prev_rev or 0)),
    }


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
    by_cat = _rows(
        engine, by_cat_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

    prev_gpm = _scalar(
        engine,
        """
    SELECT SUM(oi.quantity*(oi.unit_price-oi.cost_price))*100.0
           /NULLIF(SUM(oi.quantity*oi.unit_price),0)
    FROM kirana_oltp.orders o JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to AND oi.cost_price>0
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    # Total billed revenue WITHOUT the cost filter — lets us report how much of
    # the period's sales actually have cost data behind the profit figure.
    billed_revenue = float(
        _scalar(
            engine,
            """
    SELECT SUM(oi.quantity * oi.unit_price)
    FROM kirana_oltp.orders o JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
    WHERE o.store_id = :sid AND o.order_status = 'completed'
      AND o.order_date BETWEEN :p_from AND :p_to
    """,
            {"sid": store_id, "p_from": p_from, "p_to": p_to},
        )
        or 0
    )

    covered_revenue = float(r.get("revenue") or 0)
    cost_coverage_pct = (
        round(covered_revenue / billed_revenue * 100, 1) if billed_revenue > 0 else 0.0
    )

    cur = float(r.get("gpm_pct") or 0)
    return {
        "total_revenue": covered_revenue,
        "billed_revenue": round(billed_revenue, 2),
        "total_cogs": float(r.get("cogs") or 0),
        "gross_profit": float(r.get("gross_profit") or 0),
        "gpm_pct": cur,
        # Share of billed revenue that has cost data — how trustworthy the
        # profit number is. 100% = every sold item had a known cost.
        "cost_coverage_pct": cost_coverage_pct,
        "by_category": [
            {
                "category_name": c["category_name"],
                "margin_pct": float(c["margin_pct"] or 0),
                "revenue": float(c["revenue"] or 0),
            }
            for c in by_cat
        ],
        "trend": _trend(cur, float(prev_gpm or 0)),
    }


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

    revenue = float(rr.get("revenue") or 0)
    total_cost = float(cr.get("total_cost") or 0)
    net_cashflow = revenue - total_cost
    daily_revenue = revenue / max(days, 1)
    daily_cost = total_cost / max(days, 1)
    daily_net = net_cashflow / max(days, 1)

    # Runway = how many days at current net rate we can cover costs
    # Positive net = sustainable; estimate runway as net / daily_cost
    runway_days = (
        round(net_cashflow / max(daily_cost, 1), 0) if daily_cost > 0 else 9999
    )

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
    weekly = _rows(
        engine, weekly_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

    prev_net = _scalar(
        engine,
        """
    SELECT SUM(o.total_amount) - COALESCE(SUM(pi.quantity*pi.cost_price),0)
    FROM kirana_oltp.orders o
    LEFT JOIN kirana_oltp.purchases pu ON pu.store_id=o.store_id
        AND pu.order_date BETWEEN :pp_from AND :pp_to
    LEFT JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    return {
        "period_revenue": round(revenue, 2),
        "period_cost": round(total_cost, 2),
        "net_cashflow": round(net_cashflow, 2),
        "daily_net": round(daily_net, 2),
        "runway_days": int(min(runway_days, 9999)),
        "cashflow_status": "positive" if net_cashflow > 0 else "negative",
        "weekly_cashflow": [
            {
                "week": str(w["week"]),
                "revenue": float(w["revenue"] or 0),
                "cost": float(w["cost"] or 0),
                "net": float(w["net"] or 0),
            }
            for w in weekly
        ],
        "trend": _trend(net_cashflow, float(prev_net or 0)),
    }


def calc_high_margin_sales(
    engine, store_id: int, days: int = 30, margin_pctile: float = 0.75
) -> dict:
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
    r = _row(
        engine,
        sql,
        {"sid": store_id, "p_from": p_from, "p_to": p_to, "pctile": margin_pctile},
    )
    total_rev = float(r.get("total_revenue") or 0)
    hm_rev = float(r.get("hm_revenue") or 0)
    pct = round(hm_rev / total_rev * 100, 2) if total_rev > 0 else 0.0

    # Prev period for trend
    prev = _row(
        engine,
        sql,
        {"sid": store_id, "p_from": pp_from, "p_to": pp_to, "pctile": margin_pctile},
    )
    prev_total = float(prev.get("total_revenue") or 0)
    prev_hm = float(prev.get("hm_revenue") or 0)
    prev_pct = round(prev_hm / prev_total * 100, 2) if prev_total > 0 else 0.0

    return {
        "total_skus": int(r.get("total_skus") or 0),
        "high_margin_skus": int(r.get("hm_sku_count") or 0),
        "total_revenue": round(total_rev, 2),
        "high_margin_revenue": round(hm_rev, 2),
        "high_margin_pct": pct,
        "high_margin_profit": round(float(r.get("hm_profit") or 0), 2),
        "trend": _trend(pct, prev_pct),
    }


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
    paid = float(cur.get("total_paid") or 0)
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
    top = [
        {
            "khata_id": int(r["khata_id"]),
            "customer_id": int(r["customer_id"]),
            "customer_name": r["customer_name"],
            "outstanding": float(r["amount"] or 0) - float(r["amount_paid"] or 0),
            "due_date": str(r["due_date"]),
            "status": r["status"],
            "days_overdue": int(r["days_overdue"] or 0),
        }
        for r in _rows(engine, top_sql, {"sid": store_id})
    ]

    # Trend: recovery this month vs last month
    cur_paid = (
        _scalar(
            engine,
            """
        SELECT COALESCE(SUM(amount_paid), 0) FROM kirana_oltp.khata
        WHERE store_id = :sid AND issue_date >= CURRENT_DATE - INTERVAL '30 days'
    """,
            {"sid": store_id},
        )
        or 0
    )
    prev_paid = (
        _scalar(
            engine,
            """
        SELECT COALESCE(SUM(amount_paid), 0) FROM kirana_oltp.khata
        WHERE store_id = :sid AND issue_date BETWEEN CURRENT_DATE - INTERVAL '60 days' AND CURRENT_DATE - INTERVAL '30 days'
    """,
            {"sid": store_id},
        )
        or 0
    )

    return {
        "recovery_pct": recovery_pct,
        "total_outstanding": round(total, 2),
        "total_recovered": round(paid, 2),
        "overdue_amount": round(float(cur.get("overdue_amount") or 0), 2),
        "counts": {
            "open": int(cur.get("open_count") or 0),
            "overdue": int(cur.get("overdue_count") or 0),
            "settled": int(cur.get("settled_count") or 0),
            "write_off": int(cur.get("write_off_count") or 0),
        },
        "top_defaulters": top,
        "trend": _trend(float(cur_paid), float(prev_paid)),
    }


def calc_working_capital_cycle(engine, store_id: int = None) -> dict:
    params: dict = {}
    store_clause = ""
    inv_clause = ""
    pu_clause = ""
    if store_id:
        params["sid"] = store_id
        store_clause = " AND store_id = :sid"
        inv_clause = " AND i.store_id = :sid"
        pu_clause = " AND pu.store_id = :sid"

    rev = float(_scalar(engine, f"""
    SELECT COALESCE(SUM(total_amount), 1) FROM kirana_oltp.orders
    WHERE order_date >= CURRENT_DATE - 365 AND order_status = 'completed'{store_clause}
    """, params) or 1)

    ar = float(_scalar(engine, f"""
    SELECT COALESCE(SUM(amount - amount_paid), 0)
    FROM kirana_oltp.khata WHERE status != 'settled'{store_clause}
    """, params) or 0)

    inv = float(_scalar(engine, f"""
    SELECT COALESCE(SUM(i.quantity * COALESCE(ps.cost_price, 0)), 0)
    FROM kirana_oltp.inventory i
    LEFT JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
    WHERE i.quantity > 0{inv_clause}
    """, params) or 0)

    # AP days: avg supplier lead time as proxy for payables cycle
    ap_days = float(_scalar(engine, f"""
    SELECT COALESCE(
        AVG(EXTRACT(EPOCH FROM (pu.arrival_date - pu.order_date)) / 86400), 15
    )
    FROM kirana_oltp.purchases pu
    WHERE pu.arrival_date IS NOT NULL AND pu.order_date >= CURRENT_DATE - 90{pu_clause}
    """, params) or 15)

    ar_days = (ar / rev) * 365
    inv_days = (inv / rev) * 365
    cycle = round(inv_days + ar_days - ap_days, 1)

    return {
        "working_capital_days": cycle,
        "inventory_days": round(inv_days, 1),
        "ar_days": round(ar_days, 1),
        "ap_days": round(ap_days, 1),
        "trend": _trend(cycle, None, higher_is_better=False),
    }


def calc_ai_roi(engine, store_id: int = None) -> dict:
    params: dict = {}
    store_clause = ""
    inv_clause = ""
    oos_clause = ""
    if store_id:
        params["sid"] = store_id
        store_clause = " AND m.store_id = :sid"
        inv_clause = " AND i.store_id = :sid"
        oos_clause = " AND store_id = :sid"

    # Actual monthly expiry waste value
    monthly_waste = float(_scalar(engine, f"""
    SELECT COALESCE(SUM(ABS(m.change_quantity) * COALESCE(ps.cost_price, 0)), 0)
    FROM kirana_oltp.inventory_movements m
    LEFT JOIN kirana_oltp.product_supplier ps ON m.product_id = ps.product_id
    WHERE m.reason = 'expiry' AND m.created_at >= CURRENT_DATE - 30{store_clause}
    """, params) or 0)

    # Stockout impact: current OOS products × their 14-day avg daily revenue
    oos_daily_impact = float(_scalar(engine, f"""
    WITH oos AS (
        SELECT product_id FROM kirana_oltp.inventory
        WHERE quantity = 0{oos_clause}
    ),
    hist AS (
        SELECT oi.product_id,
               SUM(oi.quantity * oi.unit_price)::float / 14 AS daily_revenue
        FROM kirana_oltp.order_item oi
        JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
        WHERE o.order_status = 'completed'
          AND o.order_date >= CURRENT_DATE - 14
          {'AND o.store_id = :sid' if store_id else ''}
        GROUP BY oi.product_id
    )
    SELECT COALESCE(SUM(h.daily_revenue), 0) FROM oos JOIN hist h USING (product_id)
    """, params) or 0)

    cost = 599.0
    # Conservative prevention estimates: AI catches 50% of expiry, 30% of stockout days
    waste_saved = round(monthly_waste * 0.5, 2)
    stockout_rec = round(oos_daily_impact * 30 * 0.3, 2)
    total_savings = round(waste_saved + stockout_rec, 2)
    roi = round(total_savings / cost, 2) if cost > 0 else 0.0

    return {
        "roi_multiplier": roi,
        "total_savings": total_savings,
        "waste_savings": waste_saved,
        "stockout_savings": stockout_rec,
        "monthly_expiry_waste": round(monthly_waste, 2),
        "current_oos_daily_impact": round(oos_daily_impact, 2),
        "monthly_subscription": cost,
        "trend": _trend(roi, None),
    }
