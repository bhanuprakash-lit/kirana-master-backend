_HOLDING_RATE = 0.20

from .core import _period, _prev_period, _row, _rows, _scalar, _trend, ml_profile_for
from datetime import date, timedelta


_OLAP_FAST_MOVERS_SQL = """
WITH fast_movers AS (
    SELECT d.product_id, p.name, c.name AS category_name,
           AVG(d.units_sold) AS avg_daily_demand
    FROM kirana_olap.daily_store_sku_metrics d
    JOIN kirana_oltp.product p  ON d.product_id = p.product_id
    JOIN kirana_oltp.category c ON p.category_id = c.category_id
    WHERE d.store_id = :sid AND d.date >= CURRENT_DATE - 14 AND d.units_sold > 0
    GROUP BY d.product_id, p.name, c.name
    HAVING AVG(d.units_sold) >= 3
)
SELECT fm.product_id, fm.name AS product_name, fm.category_name,
       COALESCE(i.quantity, 0) AS current_stock,
       ROUND(fm.avg_daily_demand::numeric, 2) AS avg_daily_demand,
       ROUND((COALESCE(i.quantity,0) / NULLIF(fm.avg_daily_demand,0))::numeric, 1) AS days_of_cover
FROM fast_movers fm
LEFT JOIN kirana_oltp.inventory i ON i.product_id = fm.product_id AND i.store_id = :sid
ORDER BY days_of_cover ASC
"""

_OLTP_FAST_MOVERS_SQL = """
WITH fast_movers AS (
    SELECT oi.product_id, p.name, c.name AS category_name,
           SUM(oi.quantity)::float / 14 AS avg_daily_demand
    FROM kirana_oltp.order_item oi
    JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
    JOIN kirana_oltp.product p  ON oi.product_id = p.product_id
    JOIN kirana_oltp.category c ON p.category_id = c.category_id
    WHERE o.store_id = :sid AND o.order_status = 'completed'
      AND o.order_date >= CURRENT_DATE - 14
    GROUP BY oi.product_id, p.name, c.name
    HAVING SUM(oi.quantity)::float / 14 >= 3
)
SELECT fm.product_id, fm.name AS product_name, fm.category_name,
       COALESCE(i.quantity, 0) AS current_stock,
       ROUND(fm.avg_daily_demand::numeric, 2) AS avg_daily_demand,
       ROUND((COALESCE(i.quantity,0) / NULLIF(fm.avg_daily_demand,0))::numeric, 1) AS days_of_cover
FROM fast_movers fm
LEFT JOIN kirana_oltp.inventory i ON i.product_id = fm.product_id AND i.store_id = :sid
ORDER BY days_of_cover ASC
"""


def calc_morning_stock_readiness(engine, store_id: int, ml_adapter=None) -> dict:
    """
    Fast-movers + stockout predictions from ML; compares today's inventory vs demand.
    readiness_score = % fast-moving SKUs with days_of_cover >= 2
    """
    try:
        rows = _rows(engine, _OLAP_FAST_MOVERS_SQL, {"sid": store_id})
    except Exception:
        rows = []
    if not rows:
        rows = _rows(engine, _OLTP_FAST_MOVERS_SQL, {"sid": store_id})

    if not rows:
        return {
            "readiness_score": 0,
            "ready_count": 0,
            "low_count": 0,
            "critical_count": 0,
            "total_fast_movers": 0,
            "skus": [],
            "trend": _trend(None, None),
        }

    # Merge stockout probabilities from ML
    stockout_map: dict[int, float] = {}
    if ml_adapter:
        df = ml_adapter.get_frame()
        if not df.empty:
            sub = df[
                (df["store_id"] == store_id)
                & (df["recommendation_type"] == "stockout_risk")
            ]
            stockout_map = dict(zip(sub["sku_id"].astype(int), sub["prob_stockout_7d"]))

    skus = []
    ready = low = critical = 0
    for r in rows:
        doc = float(r["days_of_cover"] or 0)
        status = "critical" if doc < 2 else ("low" if doc < 4 else "ready")
        if status == "ready":
            ready += 1
        elif status == "low":
            low += 1
        else:
            critical += 1
        pid = int(r["product_id"])
        skus.append(
            {
                "product_id": pid,
                "product_name": r["product_name"],
                "category_name": r["category_name"],
                "current_stock": int(r["current_stock"]),
                "avg_daily_demand": float(r["avg_daily_demand"]),
                "days_of_cover": doc,
                "readiness_status": status,
                "stockout_risk_7d": stockout_map.get(pid),
            }
        )

    total = len(rows)
    score = round(ready * 100.0 / max(total, 1), 1)

    return {
        "readiness_score": score,
        "ready_count": ready,
        "low_count": low,
        "critical_count": critical,
        "total_fast_movers": total,
        "skus": skus,
        "trend": _trend(score, None),
    }


def calc_inventory_holding(engine, store_id: int) -> dict:
    try:
        has_olap = bool(_scalar(engine,
            "SELECT 1 FROM kirana_olap.daily_store_sku_metrics"
            " WHERE store_id = :sid AND date >= CURRENT_DATE - 30 LIMIT 1",
            {"sid": store_id}))
    except Exception:
        has_olap = False

    demand_cte = (
        "SELECT product_id, AVG(units_sold) AS avg_daily_demand"
        " FROM kirana_olap.daily_store_sku_metrics"
        " WHERE store_id = :sid AND date >= CURRENT_DATE - 30 GROUP BY product_id"
    ) if has_olap else (
        "SELECT oi.product_id AS product_id, SUM(oi.quantity)::float / 30 AS avg_daily_demand"
        " FROM kirana_oltp.order_item oi"
        " JOIN kirana_oltp.orders o ON oi.order_id = o.order_id"
        " WHERE o.store_id = :sid AND o.order_status = 'completed'"
        " AND o.order_date >= CURRENT_DATE - 30 GROUP BY oi.product_id"
    )

    sql = f"""
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
    demand AS ({demand_cte})
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

    total_stock = sum(float(r.get("avg_stock_value") or 0) for r in rows)
    total_hold = sum(float(r.get("holding_cost_30d") or 0) for r in rows)
    excess_value = sum(float(r.get("excess_value") or 0) for r in rows)

    if has_olap:
        monthly_rev = float(_scalar(engine,
            "SELECT COALESCE(SUM(revenue), 1) FROM kirana_olap.daily_store_sku_metrics"
            " WHERE store_id=:sid AND date>=CURRENT_DATE-30",
            {"sid": store_id}) or 1)
    else:
        monthly_rev = float(_scalar(engine,
            "SELECT COALESCE(SUM(total_amount), 1) FROM kirana_oltp.orders"
            " WHERE store_id=:sid AND order_status='completed' AND order_date>=CURRENT_DATE-30",
            {"sid": store_id}) or 1)
    hold_pct_rev = round(total_hold / max(monthly_rev, 1) * 100, 2)

    return {
        "total_stock_value": round(total_stock, 2),
        "total_holding_cost": round(total_hold, 2),
        "holding_cost_pct_of_revenue": hold_pct_rev,
        "excess_inventory_value": round(excess_value, 2),
        "optimal_stock_value": round(total_stock - excess_value, 2),
        "by_category": [
            {
                "category_name": r["category_name"],
                "avg_stock_value": float(r.get("avg_stock_value") or 0),
                "holding_cost": float(r.get("holding_cost_30d") or 0),
                "holding_cost_pct": float(r.get("holding_cost_pct") or 0),
                "excess_units": int(r.get("excess_units") or 0),
                "excess_value": float(r.get("excess_value") or 0),
            }
            for r in rows
        ],
        "trend": _trend(100 - hold_pct_rev, None, higher_is_better=False),
    }


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
    cost_map = {
        int(r["product_id"]): float(r["cost_price"] or 0)
        for r in _rows(engine, cost_sql, {})
    }

    items = []
    total_at_risk_value = 0.0
    high = medium = 0
    total_stock = 0

    for r in rows:
        doc = float(r.get("days_of_cover") or 0)
        pid = int(r["product_id"])
        stock = int(r.get("current_stock") or 0)
        cost = cost_map.get(pid, 0)
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
        items.append(
            {
                "product_id": pid,
                "product_name": r["name"],
                "category_name": r["category_name"],
                "current_stock": stock,
                "days_stock_unchanged": unchanged,
                "daily_avg_sales": float(r.get("avg_daily_sales") or 0),
                "days_of_cover": doc if doc != float("inf") else 999.0,
                "waste_risk": risk,
                "estimated_waste_value": waste_val,
            }
        )

    waste_rate = round((high + medium) * 100.0 / max(len(items), 1), 1)
    return {
        "total_perishable_skus": len(items),
        "high_risk_count": high,
        "medium_risk_count": medium,
        "total_at_risk_value": round(total_at_risk_value, 2),
        "waste_rate_pct": waste_rate,
        "items": sorted(items, key=lambda x: x["waste_risk"] == "high", reverse=True),
        "trend": _trend(100 - waste_rate, None, higher_is_better=False),
    }


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
    total_sold = float(
        _scalar(
            engine, total_sold_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
        )
        or 1
    )
    shrink_rate = round(total_units / (total_units + total_sold) * 100, 2)

    # Anomaly scores from ML if available
    scores: dict[int, float] = {}
    flagged_threshold = 5
    if ml_anomaly_fn:
        try:
            scores = ml_anomaly_fn(
                [int(r["product_id"]) for r in rows],
                [int(r.get("shrinkage_units") or 0) for r in rows],
                opening_stocks=[int(r.get("opening_stock") or 0) for r in rows],
                purchased_list=[int(r.get("purchased") or 0) for r in rows],
                sold_list=[int(r.get("sold") or 0) for r in rows],
            )
        except Exception:
            pass

    items = []
    flagged_count = 0
    for r in rows:
        pid = int(r["product_id"])
        su = int(r.get("shrinkage_units") or 0)
        score = scores.get(pid)
        flagged = su >= flagged_threshold or (score is not None and score > 0.7)
        if flagged:
            flagged_count += 1
        items.append(
            {
                "product_id": pid,
                "product_name": r["name"],
                "category_name": r["category_name"],
                "opening_stock": int(r.get("opening_stock") or 0),
                "purchases": int(r.get("purchased") or 0),
                "sales": int(r.get("sold") or 0),
                "expected_closing": int(r.get("expected_closing") or 0),
                "actual_closing": int(r.get("actual_closing") or 0),
                "shrinkage_units": su,
                "shrinkage_value": float(r.get("shrinkage_value") or 0),
                "anomaly_score": score,
                "flagged": flagged,
            }
        )

    return {
        "total_shrinkage_units": total_units,
        "total_shrinkage_value": round(total_value, 2),
        "shrinkage_rate_pct": shrink_rate,
        "flagged_skus_count": flagged_count,
        "items": items[:30],
        "trend": _trend(100 - shrink_rate, None, higher_is_better=False),
    }


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
        return {
            "total_purchase_orders": 0,
            "avg_expected_days": 0,
            "avg_actual_days": 0,
            "on_time_rate_pct": 0,
            "overall_accuracy_pct": 0,
            "by_supplier": [],
            "trend": _trend(None, None),
        }

    total_orders = sum(int(r.get("order_count") or 0) for r in rows)
    overall_on_time = sum(
        float(r.get("on_time_pct") or 0) * int(r.get("order_count") or 0) for r in rows
    ) / max(total_orders, 1)
    avg_expected = sum(float(r.get("avg_expected") or 0) for r in rows) / max(
        len(rows), 1
    )
    avg_actual = sum(float(r.get("avg_actual") or 0) for r in rows) / max(len(rows), 1)
    overall_acc = round(
        100 - sum(float(r.get("mape") or 0) for r in rows) / max(len(rows), 1), 1
    )

    pp_from, pp_to = _prev_period(days)
    prev_ot = _scalar(
        engine,
        """
    SELECT COUNT(*) FILTER(WHERE EXTRACT(EPOCH FROM (pu.arrival_date-pu.order_date))/86400
                                 <= ps.lead_time_days + 0.5)*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id=pi.purchase_id
    JOIN kirana_oltp.product_supplier ps ON pi.product_id=ps.product_id AND pu.supplier_id=ps.supplier_id
    WHERE pu.store_id=:sid AND pu.order_date BETWEEN :pp_from AND :pp_to AND pu.arrival_date IS NOT NULL
    """,
        {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
    )

    def _rel_score(r):
        ot = float(r.get("on_time_pct") or 50)
        mpe = float(r.get("mape") or 50)
        return round(ot * 0.7 + max(0, 100 - mpe) * 0.3, 1)

    return {
        "total_purchase_orders": total_orders,
        "avg_expected_days": round(avg_expected, 2),
        "avg_actual_days": round(avg_actual, 2),
        "on_time_rate_pct": round(overall_on_time, 1),
        "overall_accuracy_pct": overall_acc,
        "by_supplier": [
            {
                "supplier_id": int(r["supplier_id"]),
                "supplier_name": r["supplier_name"],
                "order_count": int(r.get("order_count") or 0),
                "avg_expected_days": float(r.get("avg_expected") or 0),
                "avg_actual_days": float(r.get("avg_actual") or 0),
                "on_time_pct": float(r.get("on_time_pct") or 0),
                "mape": float(r.get("mape") or 0),
                "reliability_score": _rel_score(r),
            }
            for r in rows
        ],
        "trend": _trend(overall_on_time, float(prev_ot or 0)),
    }


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
    by_cat = _rows(
        engine, by_cat_sql, {"sid": store_id, "p_from": p_from, "p_to": p_to}
    )

    cogs = float(cr.get("cogs") or 0)
    avg_inv = float(ir.get("avg_inv_value") or 1)
    # Annualised turnover
    annualised_cogs = cogs * (365.0 / max(days, 1))
    turnover = round(annualised_cogs / avg_inv, 2) if avg_inv else 0
    doi = round(avg_inv / max(cogs / max(days, 1), 0.01), 1)  # days of inventory

    prev_cogs = float(
        _scalar(
            engine,
            """
    SELECT SUM(oi.quantity * oi.cost_price)
    FROM kirana_oltp.orders o JOIN kirana_oltp.order_item oi ON o.order_id=oi.order_id
    WHERE o.store_id=:sid AND o.order_status='completed'
      AND o.order_date BETWEEN :pp_from AND :pp_to AND oi.cost_price>0
    """,
            {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
        )
        or 0
    )
    prev_turnover = (
        round(prev_cogs * (365.0 / max(days, 1)) / avg_inv, 2)
        if avg_inv and prev_cogs
        else None
    )

    return {
        "turnover_ratio": turnover,
        "days_of_inventory": doi,
        "cogs": round(cogs, 2),
        "avg_inventory_value": round(avg_inv, 2),
        "by_category": [
            {
                "category_name": c["category_name"],
                "inv_value": float(c["inv_value"] or 0),
                "cogs": float(c["cogs_value"] or 0),
            }
            for c in by_cat
        ],
        "trend": _trend(turnover, prev_turnover),
    }


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
    prev_rate = _scalar(
        engine,
        """
    SELECT COUNT(*) FILTER(WHERE quantity=0)*100.0/NULLIF(COUNT(*),0)
    FROM kirana_oltp.inventory WHERE store_id=:sid
    """,
        {"sid": store_id},
    )

    cur = float(r.get("oos_rate") or 0)
    return {
        "total_skus": int(r.get("total_skus") or 0),
        "oos_sku_count": int(r.get("oos_count") or 0),
        "low_stock_count": int(r.get("low_stock_count") or 0),
        "oos_rate_pct": cur,
        "oos_items": [
            {
                "product_id": int(i["product_id"]),
                "product_name": i["product_name"],
                "category_name": i["category_name"],
            }
            for i in oos_items
        ],
        "trend": _trend(100 - cur, float(100 - (prev_rate or 0))),
    }


def calc_dead_stock(engine, store_id: int, days: int = 30) -> dict:
    # F4 — the "not sold in N days = dead" window comes from the store's vertical
    # ML profile (grocery 21d, apparel 60d seasonal, electronics 45d, …).
    window = ml_profile_for(engine, store_id)["dead_stock_days"]
    p_from = date.today() - timedelta(days=window)

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
        "dead_sku_count": dead_count,
        "dead_stock_value": round(dead_value, 2),
        "total_inventory_value": round(total_inv_value, 2),
        "dead_stock_pct": dead_pct,
        "analysis_days": window,
        "items": [
            {
                "product_id": int(r["product_id"]),
                "product_name": r["product_name"],
                "category_name": r["category_name"],
                "quantity": int(r["quantity"]),
                "dead_value": float(r.get("dead_value") or 0),
            }
            for r in rows[:30]
        ],
        "trend": _trend(float(dead_count), None, higher_is_better=False),
    }


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
    proxy = float(r.get("proxy_lost_revenue") or 0)
    estimate = direct if direct > 0 else proxy
    method = "direct (lost_sales col)" if direct > 0 else "olap-proxy (zero-stock days × avg sales)"
    skus_impacted = int(r.get("skus_impacted") or 0)

    if estimate == 0:
        # OLAP has no data for this store — fall back to OLTP
        oltp_r = _row(engine, """
        WITH oos AS (
            SELECT product_id FROM kirana_oltp.inventory WHERE store_id = :sid AND quantity = 0
        ),
        hist AS (
            SELECT oi.product_id,
                   AVG(oi.unit_price)                          AS avg_price,
                   SUM(oi.quantity)::float / GREATEST(:days,1) AS avg_units_per_day
            FROM kirana_oltp.order_item oi
            JOIN kirana_oltp.orders o ON oi.order_id = o.order_id
            WHERE o.store_id = :sid AND o.order_status = 'completed'
              AND o.order_date BETWEEN :p_from AND :p_to
            GROUP BY oi.product_id
        )
        SELECT COUNT(DISTINCT oos.product_id)                            AS skus_impacted,
               COALESCE(SUM(h.avg_units_per_day * h.avg_price * :days), 0) AS oltp_proxy
        FROM oos LEFT JOIN hist h USING (product_id)
        """, {"sid": store_id, "p_from": p_from, "p_to": p_to, "days": days})
        estimate = float(oltp_r.get("oltp_proxy") or 0)
        skus_impacted = int(oltp_r.get("skus_impacted") or 0)
        method = "oltp-proxy (oos-products × avg daily revenue)"

    prev = _row(engine, sql, {"sid": store_id, "p_from": pp_from, "p_to": pp_to})
    prev_dir = float(prev.get("direct_lost_revenue") or 0)
    prev_prx = float(prev.get("proxy_lost_revenue") or 0)
    prev_est = prev_dir if prev_dir > 0 else prev_prx

    return {
        "estimated_lost_revenue": round(estimate, 2),
        "direct_lost_revenue": round(direct, 2),
        "proxy_lost_revenue": round(proxy, 2),
        "lost_units": int(r.get("lost_units_total") or 0),
        "zero_stock_days": int(r.get("zero_stock_day_total") or 0),
        "skus_impacted": skus_impacted,
        "skus_observed": int(r.get("skus_seen") or 0),
        "method": method,
        "trend": _trend(estimate, prev_est, higher_is_better=False),
    }


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
    total_rev = float(rows[0]["total_rev"] if rows else 1) or 1
    rate = round(total_waste / total_rev * 100, 2)

    # Trend
    prev_waste = abs(
        float(
            _scalar(
                engine,
                """
        SELECT COALESCE(SUM(m.change_quantity * ps.cost_price), 0)
        FROM kirana_oltp.inventory_movements m
        JOIN kirana_oltp.product_supplier ps ON m.product_id = ps.product_id
        WHERE m.store_id = :sid AND m.reason = 'expiry'
          AND m.created_at::date BETWEEN :pp_from AND :pp_to
    """,
                {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to},
            )
            or 0
        )
    )

    return {
        "total_waste_value": round(total_waste, 2),
        "waste_rate_pct": rate,
        "by_category": [
            {
                "category_name": r["category_name"],
                "waste_value": round(abs(float(r["waste_value"])), 2),
            }
            for r in rows
        ],
        "trend": _trend(total_waste, prev_waste, higher_is_better=False),
    }


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
        "by_supplier": [
            {
                "name": r["supplier_name"],
                "fill_pct": round(
                    int(r["received_qty"]) * 100.0 / max(int(r["ordered_qty"]), 1), 2
                ),
            }
            for r in rows
        ],
        "trend": _trend(overall_fill, None),
    }


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
        "recovery_pct": pct,
        "estimated_loss": round(est, 2),
        "recovered_amount": round(rec, 2),
        "total_returns": int(r.get("total_returns") or 0),
        "trend": _trend(pct, None),
    }


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

    rev = float(r.get("markdown_revenue") or 0)
    cost = float(r.get("cost_value") or 0)
    # Recovery % = how much of the cost was recovered vs. letting it expire (0 recovery)
    pct = round(rev / cost * 100, 2) if cost > 0 else 0.0

    return {
        "recovery_pct": pct,
        "markdown_revenue": round(rev, 2),
        "cost_value": round(cost, 2),
        "sku_count": int(r.get("sku_count") or 0),
        "trend": _trend(pct, None),
    }
