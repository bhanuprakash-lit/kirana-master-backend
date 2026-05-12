
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
        return {"fill_pct": 100, "by_supplier": []}
    total_ordered = sum(int(r["ordered_qty"]) for r in rows)
    total_received = sum(int(r["received_qty"]) for r in rows)
    overall_fill = round(total_received * 100.0 / max(total_ordered, 1), 2)
    return {"fill_pct": overall_fill, "total_ordered": total_ordered, "total_received": total_received}

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
    return {"recovery_pct": pct, "estimated_loss": round(est, 2), "recovered_amount": round(rec, 2)}

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
    return {"recovery_pct": pct, "markdown_revenue": round(rev, 2), "cost_value": round(cost, 2)}
