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


# ── M4 Services KPIs — thin wrappers over the services repository ─────────────
def calc_service_revenue(engine, store_id: int, days: int = 30) -> dict:
    from kirana.repositories.main import KiranaRepository
    out = KiranaRepository(engine).service_revenue(store_id, days)
    out["trend"] = _trend(None, None)
    return out


def calc_appointment_utilisation(engine, store_id: int, days: int = 30) -> dict:
    from kirana.repositories.main import KiranaRepository
    out = KiranaRepository(engine).appointment_utilisation(store_id, days)
    out["trend"] = _trend(None, None)
    return out


# ── M2 cross-vertical KPI — multi-store rollup ───────────────────────────────
def calc_zone_comparison(engine, store_id: int, days: int = 30) -> dict:
    from kirana.repositories.main import KiranaRepository
    out = KiranaRepository(engine).store_rollup(store_id, days)
    out["primary"] = out.get("store_count", 1)
    out["trend"] = _trend(None, None)
    return out


# ── M5 staff performance + M7 warranty-claim KPIs ────────────────────────────
def calc_staff_performance(engine, store_id: int, days: int = 30) -> dict:
    from kirana.repositories.main import KiranaRepository
    out = KiranaRepository(engine).staff_performance(store_id, days)
    out["trend"] = _trend(None, None)
    return out


def calc_warranty_claim_rate(engine, store_id: int, days: int = 90) -> dict:
    from kirana.repositories.main import KiranaRepository
    out = KiranaRepository(engine).warranty_claim_rate(store_id, days)
    out["trend"] = _trend(None, None)
    return out


# ── F4 V_AP_5 — Outfit / Bundle uptake (recommender-lite via co-purchase) ─────
def _multi_item_attach(engine, store_id: int, p_from, p_to) -> tuple[int, int]:
    r = _row(engine, """
        WITH per_order AS (
            SELECT o.order_id, COUNT(DISTINCT oi.product_id) AS distinct_products
            FROM kirana_oltp.orders o
            JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
            WHERE o.store_id = :sid AND o.order_status = 'completed'
              AND o.order_date BETWEEN :p_from AND :p_to
            GROUP BY o.order_id
        )
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE distinct_products >= 2) AS multi
        FROM per_order
    """, {"sid": store_id, "p_from": p_from, "p_to": p_to})
    return int(r.get("total") or 0), int(r.get("multi") or 0)


def calc_outfit_uptake(engine, store_id: int, days: int = 30) -> dict:
    """Bundle/outfit attach: share of bills with 2+ distinct items, plus the
    products most often bought together (a lightweight recommender seed)."""
    p_from, p_to = _period(days)
    total, multi = _multi_item_attach(engine, store_id, p_from, p_to)
    pct = round(multi / total * 100, 2) if total else 0.0

    pairs = _rows(engine, """
        SELECT pa.name AS product_a, pb.name AS product_b, COUNT(*) AS together
        FROM kirana_oltp.orders o
        JOIN kirana_oltp.order_item ia ON ia.order_id = o.order_id
        JOIN kirana_oltp.order_item ib ON ib.order_id = o.order_id
                                       AND ib.product_id > ia.product_id
        JOIN kirana_oltp.product pa ON pa.product_id = ia.product_id
        JOIN kirana_oltp.product pb ON pb.product_id = ib.product_id
        WHERE o.store_id = :sid AND o.order_status = 'completed'
          AND o.order_date BETWEEN :p_from AND :p_to
        GROUP BY pa.name, pb.name
        ORDER BY together DESC
        LIMIT 5
    """, {"sid": store_id, "p_from": p_from, "p_to": p_to})

    pp_from, pp_to = _prev_period(days)
    prev_total, prev_multi = _multi_item_attach(engine, store_id, pp_from, pp_to)
    prev_pct = round(prev_multi / prev_total * 100, 2) if prev_total else None

    return {
        "attach_pct": pct,
        "multi_item_orders": multi,
        "total_orders": total,
        "top_pairs": [
            {"a": r["product_a"], "b": r["product_b"], "count": int(r["together"])}
            for r in pairs
        ],
        "trend": _trend(pct, prev_pct),
    }


# ── F4 V_EL_1 — Accessory attach-rate (category-keyword device↔accessory map) ──
# Categories whose name matches these are treated as accessories; everything else
# sold in the same electronics order counts as the "device".
_ACCESSORY_RX = (
    r"case|cover|charger|cable|screen|guard|protector|tempered|earphone|headphone|"
    r"earbud|adapter|memory|sd\s?card|power\s?bank|mount|stand|pouch|strap|"
    r"warranty|insurance"
)


def _attach_counts(engine, store_id: int, p_from, p_to) -> tuple[int, int]:
    r = _row(engine, """
        WITH flags AS (
            SELECT o.order_id,
                   BOOL_OR(c.name ~* :rx)       AS has_accessory,
                   BOOL_OR(NOT (c.name ~* :rx)) AS has_device
            FROM kirana_oltp.orders o
            JOIN kirana_oltp.order_item oi ON oi.order_id = o.order_id
            JOIN kirana_oltp.product p ON p.product_id = oi.product_id
            JOIN kirana_oltp.category c ON c.category_id = p.category_id
            WHERE o.store_id = :sid AND o.order_status = 'completed'
              AND o.order_date BETWEEN :p_from AND :p_to
            GROUP BY o.order_id
        )
        SELECT COUNT(*) FILTER (WHERE has_device) AS device_orders,
               COUNT(*) FILTER (WHERE has_device AND has_accessory) AS attached
        FROM flags
    """, {"sid": store_id, "rx": _ACCESSORY_RX, "p_from": p_from, "p_to": p_to})
    return int(r.get("device_orders") or 0), int(r.get("attached") or 0)


def calc_attach_rate(engine, store_id: int, days: int = 30) -> dict:
    """Device orders that also carried an accessory ÷ device orders."""
    p_from, p_to = _period(days)
    device_orders, attached = _attach_counts(engine, store_id, p_from, p_to)
    pct = round(attached / device_orders * 100, 2) if device_orders else 0.0

    pp_from, pp_to = _prev_period(days)
    prev_dev, prev_att = _attach_counts(engine, store_id, pp_from, pp_to)
    prev_pct = round(prev_att / prev_dev * 100, 2) if prev_dev else None

    return {
        "attach_rate_pct": pct,
        "device_orders": device_orders,
        "orders_with_accessory": attached,
        "trend": _trend(pct, prev_pct),
    }


# ── F4 V_OP_1 — Prescription renewal due (structured Rx dates) ─────────────────
def calc_rx_renewal(engine, store_id: int, days: int = 30) -> dict:
    """Optical customers whose prescription validity has lapsed or lapses within
    the lookahead window — drives recall/renewal outreach."""
    rows = _rows(engine, """
        SELECT customer_id, name, phone, prescription_date,
               COALESCE(prescription_valid_months, 12) AS valid_months,
               (prescription_date
                + (COALESCE(prescription_valid_months, 12) || ' months')::interval)::date AS due_date
        FROM kirana_oltp.customer
        WHERE store_id = :sid AND is_deleted = FALSE
          AND prescription_date IS NOT NULL
          AND (prescription_date
               + (COALESCE(prescription_valid_months, 12) || ' months')::interval)::date
              <= CURRENT_DATE + (:days || ' days')::interval
        ORDER BY due_date
        LIMIT 200
    """, {"sid": store_id, "days": days})
    customers = [
        {
            "customer_id": int(r["customer_id"]),
            "name": r["name"],
            "phone": r["phone"],
            "prescription_date": str(r["prescription_date"]),
            "due_date": str(r["due_date"]),
        }
        for r in rows
    ]
    return {
        "due_count": len(customers),
        "customers": customers,
        "trend": _trend(None, None),
    }
