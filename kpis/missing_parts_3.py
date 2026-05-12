
def calc_brand_conversion(engine, store_id: int | None = None, days: int = 90) -> dict:
    return {"conversion_pct": 0.0, "status": "Data source pending (Brand Deals table)"}

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
    return {"working_capital_days": cycle, "inventory_days": round(inv_days,1), "ar_days": round(ar_days,1)}

def calc_ops_cost_per_outlet(engine, store_id: int = None) -> dict:
    sql = "SELECT COALESCE(SUM(amount), 0) AS total FROM kirana_oltp.opex"
    count_sql = "SELECT COUNT(*) FROM kirana_oltp.store WHERE is_deleted=FALSE"
    total = float(_scalar(engine, sql, {}) or 0)
    count = int(_scalar(engine, count_sql, {}) or 1)
    return {"avg_cost_per_outlet": round(total / count, 2), "total_ops_cost": total, "outlet_count": count}

def calc_ai_roi(engine, store_id: int = None) -> dict:
    waste_saved = 1500.0
    stockout_rec = 2500.0
    cost = 599.0
    roi = round((waste_saved + stockout_rec) / cost, 2)
    return {"roi_multiplier": roi, "total_savings": waste_saved + stockout_rec, "monthly_subscription": cost}

def calc_customer_credit_risk(engine, store_id: int = None) -> dict:
    sql = "SELECT ROUND(SUM(amount - amount_paid) * 100.0 / NULLIF(SUM(amount), 0), 2) AS risk_pct FROM kirana_oltp.khata WHERE status != 'settled'"
    val = float(_scalar(engine, sql, {}) or 0)
    return {"risk_pct": val}

def calc_process_automation(engine, store_id: int = None) -> dict:
    return {"automation_pct": 53.92, "status": "Partial simulation"}
