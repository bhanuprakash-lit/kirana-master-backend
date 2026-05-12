def calc_whatsapp_conversion(engine, store_id: int, days: int = 30) -> dict:
    p_from, p_to = _period(days)

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
    WHERE last_message_at >= :p_from OR updated_at >= :p_from
    """
    try:
        sr = _row(engine, sessions_sql, {"p_from": p_from})
    except Exception:
        sr = {}

    msgs_sql = """
    SELECT
        COUNT(*) FILTER(WHERE direction='inbound')  AS received,
        COUNT(*) FILTER(WHERE direction='outbound') AS sent
    FROM wa_message_log WHERE created_at::date >= :p_from
    """
    try:
        mr = _row(engine, msgs_sql, {"p_from": p_from})
    except Exception:
        mr = {}

    total_sess  = int(sr.get("total_sessions") or 0)
    engaged     = int(sr.get("engaged") or 0)
    conv_proxy  = round(engaged * 100.0 / max(total_sess, 1), 1)

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
        "conversion_proxy_pct":    conv_proxy,
    }


# ── 7. Morning Stock Readiness ────────────────────────────────────────────────
