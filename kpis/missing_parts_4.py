
def calc_whatsapp_conversion(engine, store_id: int, days: int = 30) -> dict:
    """Calculate conversion of WhatsApp sessions to actual orders."""
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
        "period_days":          days
    }
