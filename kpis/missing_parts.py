
def calc_private_label(engine, store_id: int, days: int = 30) -> dict:
    """Share of revenue from store-branded/private-label items."""
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
    return {"total_revenue": total, "private_label_revenue": pl_rev, "private_label_pct": pct}

def calc_shelf_productivity(engine, store_id: int) -> dict:
    """Revenue per square foot of shelf space."""
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
    return {"total_revenue": rev, "shelf_sqft": sqft, "rev_per_sqft": round(rev/sqft, 2)}
