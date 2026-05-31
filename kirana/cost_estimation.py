"""
Category-aware cost estimation — single source of truth for "what does this
product cost us" when no real supplier cost is on file.

Kirana stores don't usually record cost, but the markup over cost is small and
fairly stable per category. We estimate:

    estimated_cost = round(selling_price * (1 - gross_margin), 2)

Margins are matched by keyword against the product's category name (case-
insensitive substring), so this works regardless of the exact category labels
a store uses. Tune the table below as real data comes in.

Used by:
  - db_generation/backfill_cost_prices.py (one-time historical estimate)
  - anywhere a cost fallback is needed before real cost capture exists.
Real captured costs (product_supplier.cost_price) always take precedence over
these estimates.
"""
from __future__ import annotations

# Keyword → gross margin (fraction of selling price). Checked in order; first
# substring match in the category name wins. Roughly reflects Indian kirana
# margins: staples thin, personal-care/household fat.
_CATEGORY_MARGINS: list[tuple[str, float]] = [
    ("rice", 0.08), ("atta", 0.08), ("flour", 0.08), ("oil", 0.08),
    ("sugar", 0.08), ("dal", 0.08), ("pulse", 0.08), ("staple", 0.08),
    ("grain", 0.08),
    ("milk", 0.08), ("dairy", 0.10), ("bread", 0.10), ("egg", 0.10),
    ("breakfast", 0.12),
    ("fruit", 0.15), ("vegetable", 0.15), ("veggie", 0.15),
    ("tea", 0.12), ("coffee", 0.14), ("juice", 0.14), ("beverage", 0.12),
    ("water", 0.10), ("drink", 0.13),
    ("biscuit", 0.15), ("chocolate", 0.18), ("namkeen", 0.15),
    ("snack", 0.15), ("chips", 0.16),
    ("noodle", 0.15), ("instant", 0.15), ("ready", 0.15),
    ("sauce", 0.18), ("spread", 0.18), ("jam", 0.18), ("ketchup", 0.18),
    ("masala", 0.18), ("spice", 0.18),
    ("soap", 0.20), ("shampoo", 0.22), ("cosmetic", 0.25), ("personal", 0.20),
    ("care", 0.20), ("hygiene", 0.20),
    ("detergent", 0.18), ("clean", 0.18), ("household", 0.18), ("home", 0.16),
]

DEFAULT_MARGIN = 0.12


def margin_for_category(category_name: str | None) -> float:
    """Gross margin (fraction of selling price) for a category name."""
    if not category_name:
        return DEFAULT_MARGIN
    name = category_name.lower()
    for keyword, margin in _CATEGORY_MARGINS:
        if keyword in name:
            return margin
    return DEFAULT_MARGIN


def estimate_cost(price: float | None, category_name: str | None) -> float | None:
    """Estimate unit cost from selling price + category. None if no usable price."""
    if price is None or price <= 0:
        return None
    return round(float(price) * (1 - margin_for_category(category_name)), 2)
