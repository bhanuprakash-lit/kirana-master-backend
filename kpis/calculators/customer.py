from sqlalchemy import text

from .core import _period, _prev_period, _row, _rows, _scalar, _trend


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
    prev_rate = _scalar(
        engine, prev_sql, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to}
    )

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
    segs = _rows(
        engine, segments_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

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
            {
                "label": s["label"],
                "customer_count": int(s["customer_count"]),
                "avg_basket": float(s["avg_basket"] or 0),
                "avg_visit_interval_days": float(s["avg_interval"] or 0),
            }
            for s in segs
        ],
    }


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
        return {
            "avg_share_pct": 0,
            "customer_count": 0,
            "wallet_estimate": industry_avg_monthly,
        }

    shares = [
        round(min(1.0, float(r["total_spend"]) / industry_avg_monthly) * 100, 2)
        for r in rows
    ]
    avg_share = round(sum(shares) / len(shares), 1)

    return {
        "avg_share_pct": avg_share,
        "customer_count": len(rows),
        "wallet_estimate": industry_avg_monthly,
        "share_distribution": {
            "high (>70%)": len([s for s in shares if s > 70]),
            "medium (30-70%)": len([s for s in shares if 30 <= s <= 70]),
            "low (<30%)": len([s for s in shares if s < 30]),
        },
        "trend": _trend(avg_share, None),
    }


def calc_customer_ltv(engine, store_id: int | None = None) -> dict:
    sql = (
        """
    SELECT AVG(total_spend) AS avg_ltv,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_spend) AS median_ltv,
           MAX(total_spend) AS top_ltv
    FROM (
        SELECT customer_id, SUM(total_amount) AS total_spend
        FROM kirana_oltp.orders
        WHERE order_status = 'completed'
          """
        + ("AND store_id = :sid" if store_id else "")
        + """
        GROUP BY customer_id
    ) x
    """
    )
    params = {"sid": store_id} if store_id else {}
    r = _row(engine, sql, params)
    val = float(r.get("avg_ltv") or 0)
    return {
        "avg_ltv": round(val, 2),
        "median_ltv": round(float(r.get("median_ltv") or 0), 2),
        "top_ltv": round(float(r.get("top_ltv") or 0), 2),
        "trend": _trend(val, None),
    }


def calc_nrr(engine, store_id: int | None = None, days: int = 365) -> dict:
    """Net Revenue Retention: (Revenue from existing customers) / (Revenue from same customers in prev period)"""
    p_from, p_to = _period(days)
    pp_from, pp_to = _prev_period(days)

    # Identify customers active in prev period
    sql_prev = "SELECT DISTINCT customer_id FROM kirana_oltp.orders WHERE order_date BETWEEN :pp_from AND :pp_to AND customer_id IS NOT NULL"
    with engine.connect() as conn:
        prev_ids = [
            r[0]
            for r in conn.execute(
                text(sql_prev), {"pp_from": pp_from, "pp_to": pp_to}
            ).all()
        ]

    if not prev_ids:
        return {
            "nrr_pct": 0,
            "status": "No baseline customers",
            "trend": _trend(0, None),
        }

    # Current revenue from those specific customers
    sql_cur = "SELECT SUM(total_amount) FROM kirana_oltp.orders WHERE customer_id IN :ids AND order_date BETWEEN :p_from AND :p_to"
    cur_rev = float(
        _scalar(
            engine, sql_cur, {"ids": tuple(prev_ids), "p_from": p_from, "p_to": p_to}
        )
        or 0
    )

    # Baseline revenue from those specific customers in prev period
    sql_base = "SELECT SUM(total_amount) FROM kirana_oltp.orders WHERE customer_id IN :ids AND order_date BETWEEN :pp_from AND :pp_to"
    base_rev = float(
        _scalar(
            engine,
            sql_base,
            {"ids": tuple(prev_ids), "pp_from": pp_from, "pp_to": pp_to},
        )
        or 1
    )

    nrr = round(cur_rev / base_rev * 100, 2)
    return {
        "nrr_pct": nrr,
        "baseline_revenue": round(base_rev, 2),
        "retained_revenue": round(cur_rev, 2),
        "trend": _trend(nrr, None),
    }


def calc_arpu(engine, store_id: int | None = None, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT ROUND(SUM(total_amount) / NULLIF(COUNT(DISTINCT customer_id), 0), 2) AS arpu
    FROM kirana_oltp.orders
    WHERE order_status = 'completed' AND customer_id IS NOT NULL
      AND order_date BETWEEN :p_from AND :p_to
    """ + (" AND store_id = :sid" if store_id else "")
    params = {"p_from": p_from, "p_to": p_to}
    if store_id:
        params["sid"] = store_id
    val = float(_scalar(engine, sql, params) or 0)
    return {"arpu": val, "trend": _trend(val, None)}


def calc_brand_conversion(engine, store_id: int | None = None, days: int = 90) -> dict:
    # Placeholder: Brand deals/investments aren't fully modeled in tables yet
    return {
        "conversion_pct": 0.0,
        "status": "Data source pending (Brand Deals table)",
        "trend": _trend(0.0, None),
    }


def calc_customer_credit_risk(engine, store_id: int = None) -> dict:
    sql = """
    SELECT ROUND(SUM(amount - amount_paid) * 100.0 / NULLIF(SUM(amount), 0), 2) AS risk_pct
    FROM kirana_oltp.khata
    WHERE status != 'settled'
    """
    val = float(_scalar(engine, sql, {}) or 0)
    return {"risk_pct": val, "trend": _trend(val, None, higher_is_better=False)}
