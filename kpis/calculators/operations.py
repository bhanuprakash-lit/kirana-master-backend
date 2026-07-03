from .core import _row, _scalar, _trend


def calc_data_quality_score(engine, store_id: int | None = None) -> dict:
    """Compute fill-rate of critical fields across core tables.
    Used as the C13 "Data Quality Score" KPI.
    """
    checks_sql = [
        (
            "orders.customer_id",
            "SELECT COUNT(*) FILTER(WHERE customer_id IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.orders",
        ),
        (
            "orders.user_id",
            "SELECT COUNT(*) FILTER(WHERE user_id IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.orders",
        ),
        (
            "product.brand",
            "SELECT COUNT(*) FILTER(WHERE brand IS NOT NULL AND brand != '')*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.product",
        ),
        (
            "product.barcode",
            "SELECT COUNT(*) FILTER(WHERE barcode IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.product",
        ),
        (
            "payments.payment_method",
            "SELECT COUNT(*) FILTER(WHERE payment_method IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.payments",
        ),
        (
            "pricing.mrp",
            "SELECT COUNT(*) FILTER(WHERE mrp IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.pricing",
        ),
        (
            "supplier.contact",
            "SELECT COUNT(*) FILTER(WHERE contact IS NOT NULL)*100.0/NULLIF(COUNT(*),0) FROM kirana_oltp.supplier",
        ),
    ]
    breakdown = []
    total = 0.0
    for label, q in checks_sql:
        v = float(_scalar(engine, q, {}) or 0)
        breakdown.append({"field": label, "fill_rate_pct": round(v, 2)})
        total += v
    score = round(total / max(len(checks_sql), 1), 2)
    return {
        "score": score,
        "field_count": len(checks_sql),
        "breakdown": breakdown,
        "trend": {
            "direction": "stable",
            "pct_change": None,
            "current_value": score,
            "previous_value": None,
            "interpretation": "Snapshot — historical baseline not tracked",
        },
    }


def calc_ops_cost_per_outlet(engine, store_id: int = None) -> dict:
    sql = "SELECT COALESCE(SUM(electricity + rent + staff + other), 0) AS total FROM kirana_oltp.opex"
    count_sql = "SELECT COUNT(*) FROM kirana_oltp.store WHERE is_deleted=FALSE"
    total = float(_scalar(engine, sql, {}) or 0)
    count = int(_scalar(engine, count_sql, {}) or 1)
    avg = round(total / count, 2)
    return {
        "avg_cost_per_outlet": avg,
        "total_ops_cost": total,
        "outlet_count": count,
        "trend": _trend(avg, None, higher_is_better=False),
    }


def calc_process_automation(engine, store_id: int = None) -> dict:
    params: dict = {}
    store_clause = ""
    if store_id:
        params["sid"] = store_id
        store_clause = " AND store_id = :sid"
    sql = f"""
    SELECT
        COUNT(*) AS total_orders,
        COUNT(*) FILTER (WHERE order_channel IN ('whatsapp', 'app', 'delivery', 'api')) AS auto_orders,
        COUNT(*) FILTER (WHERE order_channel IN ('walk_in', 'pos'))                     AS manual_orders,
        COUNT(*) FILTER (WHERE order_channel IS NULL)                                    AS unknown_orders
    FROM kirana_oltp.orders
    WHERE order_status = 'completed'
      AND order_date >= CURRENT_DATE - 30{store_clause}
    """
    r = _row(engine, sql, params)
    total = int(r.get("total_orders") or 0)
    auto = int(r.get("auto_orders") or 0)
    pct = round(auto / total * 100, 2) if total > 0 else 0.0
    return {
        "automation_pct": pct,
        "total_orders": total,
        "auto_orders": auto,
        "manual_orders": int(r.get("manual_orders") or 0),
        "unknown_channel_orders": int(r.get("unknown_orders") or 0),
        "trend": _trend(pct, None),
    }


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
    val = round(rev / sqft, 2)
    return {
        "total_revenue": rev,
        "shelf_sqft": sqft,
        "rev_per_sqft": val,
        "trend": _trend(val, None),
    }
