"""F4 — vertical KPI pack calculators (apparel / footwear / electronics).

These became computable once F2 (variant-level sales via order_item.variant_id +
product_variant.stock) and F3 (order_item.cost_price) landed. All SQL is
store-scoped via orders.store_id / inventory.store_id and guards against
div-by-zero, so a thin-data store returns zeros rather than erroring.
"""
from .core import _period, _prev_period, _row, _scalar, _rows, _trend


def calc_sell_through(engine, store_id: int, days: int = 30) -> dict:
    """Units sold ÷ (units sold + units still in stock), over real variants."""
    p_from, p_to = _period(days)
    sold = float(_scalar(engine, """
        SELECT COALESCE(SUM(oi.quantity), 0)
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
          AND oi.variant_id IS NOT NULL
    """, {"sid": store_id, "p_from": p_from, "p_to": p_to}) or 0)
    remaining = float(_scalar(engine, """
        SELECT COALESCE(SUM(pv.stock), 0)
        FROM kirana_oltp.product_variant pv
        JOIN kirana_oltp.inventory i ON i.product_id = pv.product_id AND i.store_id = :sid
        WHERE pv.is_implicit = FALSE AND pv.is_active = TRUE
    """, {"sid": store_id}) or 0)
    denom = sold + remaining
    pct = round(sold / denom * 100, 2) if denom > 0 else 0.0

    pp_from, pp_to = _prev_period(days)
    prev_sold = float(_scalar(engine, """
        SELECT COALESCE(SUM(oi.quantity), 0)
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :pp_from AND :pp_to
          AND oi.variant_id IS NOT NULL
    """, {"sid": store_id, "pp_from": pp_from, "pp_to": pp_to}) or 0)
    prev_denom = prev_sold + remaining
    prev_pct = round(prev_sold / prev_denom * 100, 2) if prev_denom > 0 else None

    return {
        "sell_through_pct": pct,
        "units_sold": sold,
        "units_remaining": remaining,
        "trend": _trend(pct, prev_pct),
    }


def calc_size_curve(engine, store_id: int, days: int = 30) -> dict:
    """Units sold split by the 'size' variant attribute."""
    p_from, p_to = _period(days)
    rows = _rows(engine, """
        SELECT COALESCE(NULLIF(pv.attributes->>'size', ''), '—') AS size,
               COALESCE(SUM(oi.quantity), 0) AS units
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        JOIN kirana_oltp.product_variant pv ON oi.variant_id = pv.variant_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
          AND (pv.attributes->>'size') IS NOT NULL
        GROUP BY pv.attributes->>'size'
        ORDER BY units DESC
    """, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    total = sum(float(r["units"] or 0) for r in rows)
    by_size = [
        {
            "size": r["size"],
            "units": float(r["units"] or 0),
            "pct": round(float(r["units"] or 0) / total * 100, 1) if total else 0.0,
        }
        for r in rows
    ]
    return {
        "sizes_tracked": len(by_size),
        "top_size": by_size[0]["size"] if by_size else None,
        "units_total": total,
        "by_size": by_size,
        "trend": _trend(None, None),
    }


def _markdown(engine, store_id: int, p_from, p_to) -> tuple[float, float]:
    r = _row(engine, """
        SELECT
          COALESCE(SUM(oi.unit_price * oi.quantity), 0) AS revenue,
          COALESCE(SUM(GREATEST(COALESCE(pr.mrp, oi.unit_price) - oi.unit_price, 0) * oi.quantity), 0) AS markdown_value
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        LEFT JOIN LATERAL (
            SELECT mrp FROM kirana_oltp.pricing
            WHERE product_id = oi.product_id AND store_id = :sid AND valid_from <= NOW()
            ORDER BY valid_from DESC LIMIT 1
        ) pr ON TRUE
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
    """, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    return float(r.get("revenue") or 0), float(r.get("markdown_value") or 0)


def calc_markdown(engine, store_id: int, days: int = 30) -> dict:
    """Discount given off MRP as a % of would-be gross (lower is better)."""
    p_from, p_to = _period(days)
    revenue, markdown_value = _markdown(engine, store_id, p_from, p_to)
    gross = revenue + markdown_value
    pct = round(markdown_value / gross * 100, 2) if gross > 0 else 0.0

    pp_from, pp_to = _prev_period(days)
    prev_rev, prev_md = _markdown(engine, store_id, pp_from, pp_to)
    prev_gross = prev_rev + prev_md
    prev_pct = round(prev_md / prev_gross * 100, 2) if prev_gross > 0 else None

    return {
        "markdown_pct": pct,
        "markdown_value": round(markdown_value, 2),
        "revenue": round(revenue, 2),
        "trend": _trend(pct, prev_pct, higher_is_better=False),
    }


def _gmroi(engine, store_id: int, p_from, p_to) -> tuple[float, float]:
    margin = float(_scalar(engine, """
        SELECT COALESCE(SUM((oi.unit_price - oi.cost_price) * oi.quantity), 0)
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to AND oi.cost_price > 0
    """, {"sid": store_id, "p_from": p_from, "p_to": p_to}) or 0)
    avg_inv = float(_scalar(engine, """
        SELECT COALESCE(SUM(i.quantity * ps.cost_price), 0)
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product_supplier ps ON i.product_id = ps.product_id
        WHERE i.store_id = :sid AND ps.cost_price IS NOT NULL AND i.quantity > 0
    """, {"sid": store_id}) or 0)
    return margin, avg_inv


def calc_gmroi(engine, store_id: int, days: int = 30) -> dict:
    """Gross-Margin Return On Inventory = gross margin ÷ avg inventory cost."""
    p_from, p_to = _period(days)
    margin, avg_inv = _gmroi(engine, store_id, p_from, p_to)
    gmroi = round(margin / avg_inv, 2) if avg_inv > 0 else 0.0

    pp_from, pp_to = _prev_period(days)
    prev_margin, prev_inv = _gmroi(engine, store_id, pp_from, pp_to)
    prev_gmroi = round(prev_margin / prev_inv, 2) if prev_inv > 0 else None

    return {
        "gmroi": gmroi,
        "gross_margin": round(margin, 2),
        "avg_inventory_cost": round(avg_inv, 2),
        "trend": _trend(gmroi, prev_gmroi),
    }
