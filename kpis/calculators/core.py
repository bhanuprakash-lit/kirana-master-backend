from sqlalchemy import text
from datetime import date, timedelta


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
