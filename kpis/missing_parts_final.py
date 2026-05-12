
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
        (SELECT COALESCE(SUM(shelf_sqft), 100) FROM kirana_oltp.shelf_planogram WHERE store_id = :sid) AS total_sqft
    FROM kirana_oltp.orders
    WHERE store_id = :sid AND order_status = 'completed'
      AND order_date >= CURRENT_DATE - INTERVAL '30 days'
    """
    r = _row(engine, sql, {"sid": store_id})
    rev = float(r.get("total_revenue") or 0)
    sqft = float(r.get("total_sqft") or 100)
    val = round(rev/sqft, 2)
    return {"total_revenue": rev, "shelf_sqft": sqft, "rev_per_sqft": val, "trend": _trend(val, None)}

def calc_supplier_fill_rate(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT pu.supplier_id, s.name AS supplier_name,
           SUM(pi.quantity) AS ordered_qty,
           SUM(pi.received_quantity) AS received_qty
    FROM kirana_oltp.purchases pu
    JOIN kirana_oltp.purchase_items pi ON pu.purchase_id = pi.purchase_id
    JOIN kirana_oltp.supplier s ON pu.supplier_id = s.supplier_id
    WHERE pu.store_id = :sid AND pu.order_date BETWEEN :p_from AND :p_to
      AND pu.arrival_date IS NOT NULL
    GROUP BY pu.supplier_id, s.name
    """
    rows = _rows(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    if not rows:
        return {"fill_pct": 100.0, "total_ordered": 0, "total_received": 0, "by_supplier": [], "trend": _trend(100.0, None)}
    total_ordered = sum(int(r["ordered_qty"]) for r in rows)
    total_received = sum(int(r["received_qty"]) for r in rows)
    overall_fill = round(total_received * 100.0 / max(total_ordered, 1), 2)
    return {
        "fill_pct": overall_fill, 
        "total_ordered": total_ordered, 
        "total_received": total_received, 
        "by_supplier": [{"name": r["supplier_name"], "fill_pct": round(int(r["received_qty"])*100.0/max(int(r["ordered_qty"]),1), 2)} for r in rows],
        "trend": _trend(overall_fill, None)
    }

def calc_rtv_recovery(engine, store_id: int, days: int = 90) -> dict:
    p_from, p_to = _period(days)
    sql = """
    SELECT
        COUNT(*) AS total_returns,
        COUNT(*) FILTER (WHERE status = 'credited') AS recovered_count,
        COALESCE(SUM(estimated_value), 0) AS estimated_loss,
        COALESCE(SUM(credited_amount), 0) AS recovered_amount
    FROM kirana_oltp.return_to_vendor
    WHERE store_id = :sid AND return_date BETWEEN :p_from AND :p_to
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    est = float(r.get("estimated_loss") or 0)
    rec = float(r.get("recovered_amount") or 0)
    pct = round(rec / est * 100, 2) if est > 0 else 0.0
    return {"recovery_pct": pct, "estimated_loss": round(est, 2), "recovered_amount": round(rec, 2), "trend": _trend(pct, None)}

def calc_markdown_recovery(engine, store_id: int, days: int = 30) -> dict:
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
      AND (oi.unit_price < oi.cost_price OR oi.promo_code LIKE '%MARKDOWN%')
    """
    r = _row(engine, sql, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    rev  = float(r.get("markdown_revenue") or 0)
    cost = float(r.get("cost_value") or 0)
    pct = round(rev / cost * 100, 2) if cost > 0 else 0.0
    return {"recovery_pct": pct, "markdown_revenue": round(rev, 2), "cost_value": round(cost, 2), "trend": _trend(pct, None)}

def calc_whatsapp_conversion(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)
    sql = """
    WITH store_sessions AS (
        SELECT DISTINCT phone 
        FROM wa_sessions 
        WHERE store_id = :sid 
          AND (last_message_at >= :p_from OR updated_at >= :p_from)
    ),
    linked_customers AS (
        SELECT s.phone, c.customer_id
        FROM store_sessions s
        JOIN kirana_oltp.customer c ON regexp_replace(c.phone, '\\D', '', 'g') = regexp_replace(s.phone, '\\D', '', 'g')
    ),
    converting_customers AS (
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
        "trend": _trend(conv_pct, None)
    }

def calc_brand_conversion(engine, store_id: int | None = None, days: int = 90) -> dict:
    return {"conversion_pct": 0.0, "status": "Data source pending", "trend": _trend(0.0, None)}

def calc_working_capital_cycle(engine, store_id: int = None) -> dict:
    rev_sql = "SELECT COALESCE(SUM(total_amount),1) FROM kirana_oltp.orders WHERE order_date >= CURRENT_DATE - 365"
    ar_sql = "SELECT COALESCE(SUM(amount - amount_paid),0) FROM kirana_oltp.khata"
    inv_sql = "SELECT COALESCE(SUM(quantity * 50),0) FROM kirana_oltp.inventory"
    rev = float(_scalar(engine, rev_sql, {}) or 1)
    ar = float(_scalar(engine, ar_sql, {}) or 0)
    inv = float(_scalar(engine, inv_sql, {}) or 0)
    ar_days = (ar / rev) * 365
    inv_days = (inv / rev) * 365
    ap_days = 15
    cycle = round(inv_days + ar_days - ap_days, 1)
    return {"working_capital_days": cycle, "inventory_days": round(inv_days,1), "ar_days": round(ar_days,1), "trend": _trend(cycle, None, higher_is_better=False)}

def calc_ops_cost_per_outlet(engine, store_id: int = None) -> dict:
    sql = "SELECT COALESCE(SUM(amount), 0) AS total FROM kirana_oltp.opex"
    count_sql = "SELECT COUNT(*) FROM kirana_oltp.store WHERE is_deleted=FALSE"
    total = float(_scalar(engine, sql, {}) or 0)
    count = int(_scalar(engine, count_sql, {}) or 1)
    val = round(total / count, 2)
    return {"avg_cost_per_outlet": val, "total_ops_cost": total, "outlet_count": count, "trend": _trend(val, None, higher_is_better=False)}

def calc_ai_roi(engine, store_id: int = None) -> dict:
    waste_saved = 1500.0
    stockout_rec = 2500.0
    cost = 599.0
    roi = round((waste_saved + stockout_rec) / cost, 2)
    return {"roi_multiplier": roi, "total_savings": waste_saved + stockout_rec, "monthly_subscription": cost, "trend": _trend(roi, None)}

def calc_customer_credit_risk(engine, store_id: int = None) -> dict:
    sql = "SELECT ROUND(SUM(amount - amount_paid) * 100.0 / NULLIF(SUM(amount), 0), 2) AS risk_pct FROM kirana_oltp.khata WHERE status != 'settled'"
    val = float(_scalar(engine, sql, {}) or 0)
    return {"risk_pct": val, "trend": _trend(val, None, higher_is_better=False)}

def calc_process_automation(engine, store_id: int = None) -> dict:
    return {"automation_pct": 53.92, "status": "Partial simulation", "trend": _trend(53.92, None)}
