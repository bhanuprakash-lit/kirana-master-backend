from sqlalchemy import text
from datetime import date, timedelta


# F4 — per-vertical ML profiles. Same engine, tuned per vertical: a grocery item
# unsold for 21 days is dead stock, but seasonal apparel needs a longer window.
# vertical_config.ml_profile selects the profile.
ML_PROFILES = {
    "grocery":     {"dead_stock_days": 21, "slow_mover_days": 14},
    "apparel":     {"dead_stock_days": 60, "slow_mover_days": 45},
    "electronics": {"dead_stock_days": 45, "slow_mover_days": 30},
    "services":    {"dead_stock_days": 30, "slow_mover_days": 21},
    # PAI-3 — bakery is own-make and turns over in days, not weeks: reusing
    # grocery's 21-day window would call yesterday's unsold bread "healthy".
    "bakery":      {"dead_stock_days": 5,  "slow_mover_days": 3},
}


def ml_profile_for(engine, store_id: int) -> dict:
    """Resolve the ML threshold profile for a store via its vertical's ml_profile."""
    prof = _scalar(
        engine,
        """
        SELECT vc.ml_profile
        FROM kirana_oltp.store s
        LEFT JOIN kirana_oltp.vertical_config vc
               ON vc.vertical_code = COALESCE(s.vertical_code, 'grocery')
        WHERE s.store_id = :sid
        """,
        {"sid": store_id},
    ) or "grocery"
    return ML_PROFILES.get(prof, ML_PROFILES["grocery"])


def _period(days: int) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=days), today


def _prev_period(days: int) -> tuple[date, date]:
    end = date.today() - timedelta(days=days)
    start = end - timedelta(days=days)
    return start, end


def _row(engine, sql: str, params: dict) -> dict:
    with engine.connect() as conn:
        r = conn.execute(text(sql), params).mappings().first()
    return dict(r) if r else {}


def _rows(engine, sql: str, params: dict) -> list[dict]:
    with engine.connect() as conn:
        rs = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rs]


def _scalar(engine, sql: str, params: dict):
    with engine.connect() as conn:
        r = conn.execute(text(sql), params).scalar()
    return r


def _store_name(engine, store_id: int) -> str:
    r = _scalar(
        engine,
        "SELECT name FROM kirana_oltp.store WHERE store_id = :sid",
        {"sid": store_id},
    )
    return r or f"Store {store_id}"


def _trend(
    current: float | None, previous: float | None, higher_is_better: bool = True
) -> dict:
    if current is None or previous is None or previous == 0:
        return {
            "direction": "stable",
            "pct_change": None,
            "current_value": current,
            "previous_value": previous,
            "interpretation": "Insufficient data for trend",
        }
    pct = round((current - previous) / abs(previous) * 100, 2)
    if abs(pct) < 1:
        direction = "stable"
    elif (pct > 0 and higher_is_better) or (pct < 0 and not higher_is_better):
        direction = "up"
    else:
        direction = "down"
    interp = {
        "up": "Improving — moving towards target.",
        "down": "Declining — action needed.",
        "stable": "No significant change.",
    }[direction]
    return {
        "direction": direction,
        "pct_change": pct,
        "current_value": current,
        "previous_value": previous,
        "interpretation": interp,
    }
