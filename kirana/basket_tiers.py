"""
Basket tier classification + auto-discount.

A basket's tier is derived from its *gross total* (sum of item qty × current
selling price). Each tier carries a default discount that is applied
automatically — the owner never types a price. Tiers (ranges + discounts) are
store-customizable; until a store customizes them, these defaults apply:

    Bronze   : gross ≤ 100   → 2%
    Silver   : 101 – 300     → 4%
    Gold     : 301 – 500     → 6%
    Platinum : > 500         → 8%

Config is stored per store as an ordered, ascending list of tiers where the top
tier has `max = None` (infinity). compute_tier() returns the first tier whose
`max` is None or ≥ gross_total, so ranges are contiguous and gap-free by
construction.
"""
from __future__ import annotations

TIER_NAMES = ("bronze", "silver", "gold", "platinum")

DEFAULT_TIER_CONFIG: dict = {
    "tiers": [
        {"name": "bronze",   "max": 100,  "discount_pct": 2},
        {"name": "silver",   "max": 300,  "discount_pct": 4},
        {"name": "gold",     "max": 500,  "discount_pct": 6},
        {"name": "platinum", "max": None, "discount_pct": 8},
    ]
}


def normalize_tier_config(config: dict | None) -> dict:
    """Validate + coerce a tier config, falling back to defaults on anything
    malformed. Always returns 4 ascending tiers with the top one open-ended."""
    if not config or not isinstance(config.get("tiers"), list):
        return DEFAULT_TIER_CONFIG
    raw = config["tiers"]
    if len(raw) != 4:
        return DEFAULT_TIER_CONFIG
    tiers = []
    for i, name in enumerate(TIER_NAMES):
        src = raw[i] if i < len(raw) else {}
        is_top = i == len(TIER_NAMES) - 1
        mx = src.get("max")
        try:
            mx = None if is_top else float(mx)
        except (TypeError, ValueError):
            return DEFAULT_TIER_CONFIG
        try:
            disc = float(src.get("discount_pct", 0))
        except (TypeError, ValueError):
            disc = 0.0
        disc = max(0.0, min(100.0, disc))
        tiers.append({"name": name, "max": mx, "discount_pct": disc})
    # boundaries must be strictly ascending
    bounds = [t["max"] for t in tiers[:-1]]
    if any(b is None for b in bounds) or any(
        bounds[i] >= bounds[i + 1] for i in range(len(bounds) - 1)
    ):
        return DEFAULT_TIER_CONFIG
    return {"tiers": tiers}


def compute_tier(gross_total: float, config: dict | None = None) -> tuple[str, float]:
    """Return (tier_name, discount_pct) for a gross total under the given config."""
    cfg = normalize_tier_config(config)
    for tier in cfg["tiers"]:
        mx = tier["max"]
        if mx is None or gross_total <= mx:
            return tier["name"], float(tier["discount_pct"])
    # unreachable (top tier has max=None) — defensive fallback
    last = cfg["tiers"][-1]
    return last["name"], float(last["discount_pct"])


def price_for(gross_total: float, config: dict | None = None) -> dict:
    """Compute the tier, discount %, and final discounted price for a basket."""
    tier, disc = compute_tier(gross_total, config)
    final = round(gross_total * (1 - disc / 100.0), 2)
    return {
        "tier": tier,
        "gross_total": round(gross_total, 2),
        "discount_pct": disc,
        "price": final,
    }
