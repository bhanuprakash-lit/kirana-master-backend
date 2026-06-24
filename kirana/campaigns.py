"""
Daily Basket Campaigns — templates + AI recommendation scoring.

Templates are hardcoded; the recommender matches them against live
inventory and scores by time-of-day, season, stock availability, and margin.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Campaign templates ────────────────────────────────────────────────────────

@dataclass
class CampaignItemTemplate:
    display_name: str          # shown in UI
    search_terms: list[str]    # matched against product.name via ILIKE
    quantity: float = 1.0


@dataclass
class CampaignTemplate:
    campaign_id: str
    name: str
    emoji: str
    description: str
    campaign_type: str         # morning | monthly | school | weekend | festival | general
    items: list[CampaignItemTemplate] = field(default_factory=list)
    # All current templates are grocery staples; gate by store.vertical_code
    # so apparel/optical/etc. stores never see grocery campaign suggestions.
    vertical_codes: list[str] = field(default_factory=lambda: ["grocery"])

    # Time windows that boost this campaign's score
    active_hours: tuple[int, int] | None = None   # (start_hour, end_hour) inclusive
    active_days: list[int] | None = None           # 0=Mon … 6=Sun; None = every day
    active_months: list[int] | None = None         # 1–12; None = every month
    active_month_days: tuple[int, int] | None = None  # (start_day, end_day) of month


TEMPLATES: list[CampaignTemplate] = [
    CampaignTemplate(
        campaign_id="morning_essentials",
        name="Morning Essentials",
        emoji="🌅",
        description="Daily breakfast must-haves — sell more before 10 AM",
        campaign_type="morning",
        active_hours=(5, 10),
        items=[
            CampaignItemTemplate("Milk", ["milk", "toned milk", "full cream milk", "amul milk", "nandini milk"]),
            CampaignItemTemplate("Bread", ["bread", "white bread", "brown bread", "britannia bread"]),
            CampaignItemTemplate("Eggs", ["eggs", "egg", "farm eggs"]),
            CampaignItemTemplate("Tea Powder", ["tea", "tea powder", "chai powder", "red label", "tata tea", "brooke bond"]),
            CampaignItemTemplate("Butter", ["butter", "amul butter"]),
        ],
    ),
    CampaignTemplate(
        campaign_id="monthly_grocery",
        name="Monthly Grocery Basket",
        emoji="🛒",
        description="Staples customers stock up on at month start",
        campaign_type="monthly",
        active_month_days=(1, 7),
        items=[
            CampaignItemTemplate("Rice", ["rice", "basmati", "sona masoori", "raw rice", "ponni rice"]),
            CampaignItemTemplate("Dal", ["dal", "toor dal", "moong dal", "chana dal", "urad dal", "masoor dal"]),
            CampaignItemTemplate("Cooking Oil", ["oil", "cooking oil", "sunflower oil", "groundnut oil", "palm oil", "refined oil"]),
            CampaignItemTemplate("Atta", ["atta", "wheat flour", "aashirvaad atta", "pilsbury atta"]),
            CampaignItemTemplate("Sugar", ["sugar", "sugar cane"]),
            CampaignItemTemplate("Salt", ["salt", "iodized salt", "tata salt", "rock salt"]),
        ],
    ),
    CampaignTemplate(
        campaign_id="school_kids_pack",
        name="School Kids Pack",
        emoji="🎒",
        description="School tiffin & snack essentials — weekday mornings",
        campaign_type="school",
        active_hours=(6, 9),
        active_days=[0, 1, 2, 3, 4],  # Mon–Fri
        items=[
            CampaignItemTemplate("Biscuits", ["biscuits", "glucose biscuits", "parle-g", "marie", "good day", "hide & seek"]),
            CampaignItemTemplate("Juice / Drink", ["juice", "fruit juice", "frooti", "maaza", "slice", "real juice", "b natural"]),
            CampaignItemTemplate("Chips / Snacks", ["chips", "lays", "kurkure", "bingo", "snacks", "namkeen"]),
            CampaignItemTemplate("Chocolate", ["chocolate", "dairy milk", "kitkat", "munch", "perk", "5 star"]),
            CampaignItemTemplate("Pen / Pencil", ["pen", "pencil", "natraj", "classmate", "reynolds"]),
        ],
    ),
    CampaignTemplate(
        campaign_id="weekend_family",
        name="Weekend Family Pack",
        emoji="🎉",
        description="Weekend treats the whole family loves",
        campaign_type="weekend",
        active_days=[5, 6],  # Sat, Sun
        items=[
            CampaignItemTemplate("Soft Drinks", ["soft drink", "cold drink", "pepsi", "coca cola", "thums up", "sprite", "mirinda", "limca", "7up"]),
            CampaignItemTemplate("Chips / Namkeen", ["chips", "namkeen", "mixture", "bhujia", "lays", "kurkure"]),
            CampaignItemTemplate("Noodles", ["noodles", "maggi", "yippee", "top ramen", "wai wai"]),
            CampaignItemTemplate("Ice Cream", ["ice cream", "kwality walls", "amul ice cream", "cornetto"]),
            CampaignItemTemplate("Ready-to-cook", ["ready to cook", "ready-to-cook", "instant mix", "idli mix", "dosa mix", "biryani mix"]),
        ],
    ),
    CampaignTemplate(
        campaign_id="festival_basket",
        name="Festival Basket",
        emoji="🪔",
        description="Festive season must-haves — high-margin opportunity",
        campaign_type="festival",
        active_months=[10, 11],   # Oct–Nov (Diwali season)
        items=[
            CampaignItemTemplate("Dry Fruits", ["dry fruits", "almonds", "cashews", "raisins", "pista", "dates", "walnuts"]),
            CampaignItemTemplate("Sweets / Mithai", ["sweets", "mithai", "ladoo", "barfi", "halwa", "gulab jamun"]),
            CampaignItemTemplate("Puja Items", ["agarbatti", "incense", "dhoop", "camphor", "diya", "pooja"]),
            CampaignItemTemplate("Ghee", ["ghee", "clarified butter", "amul ghee", "patanjali ghee"]),
            CampaignItemTemplate("Premium Rice", ["basmati", "daawat", "india gate", "kohinoor"]),
        ],
    ),
]

_TEMPLATE_MAP = {t.campaign_id: t for t in TEMPLATES}

# ── Area-type campaign templates ──────────────────────────────────────────────

AREA_TEMPLATES: dict[str, CampaignTemplate] = {
    "apartment": CampaignTemplate(
        campaign_id="area_apartment",
        name="Monthly Apartment Basket",
        emoji="🏢",
        description="Staples for apartment households — great for subscriptions",
        campaign_type="apartment",
        items=[
            CampaignItemTemplate("Rice / Atta", ["rice", "atta", "wheat flour", "basmati"]),
            CampaignItemTemplate("Cooking Oil", ["oil", "cooking oil", "sunflower oil", "refined oil"]),
            CampaignItemTemplate("Milk", ["milk", "toned milk", "amul milk"]),
            CampaignItemTemplate("Dal / Pulses", ["dal", "toor dal", "moong dal", "chana dal"]),
            CampaignItemTemplate("Detergent", ["detergent", "surf excel", "ariel", "washing powder", "vim"]),
            CampaignItemTemplate("Sugar / Salt", ["sugar", "salt", "tata salt"]),
        ],
    ),
    "hostel": CampaignTemplate(
        campaign_id="area_hostel",
        name="Hostel Quick Combo",
        emoji="🏠",
        description="Instant & snack-heavy items students need",
        campaign_type="hostel",
        items=[
            CampaignItemTemplate("Noodles", ["maggi", "noodles", "yippee", "top ramen"]),
            CampaignItemTemplate("Biscuits", ["biscuits", "parle-g", "good day", "bourbon"]),
            CampaignItemTemplate("Chips / Snacks", ["chips", "lays", "kurkure", "bingo"]),
            CampaignItemTemplate("Cold Drinks", ["pepsi", "coke", "thums up", "sprite", "soft drink"]),
            CampaignItemTemplate("Tea / Coffee", ["tea", "coffee", "nescafe", "bru", "horlicks"]),
            CampaignItemTemplate("Bread + Egg", ["bread", "eggs", "egg"]),
        ],
    ),
    "school": CampaignTemplate(
        campaign_id="area_school",
        name="School Zone Pack",
        emoji="🏫",
        description="Tiffin snacks, stationery & drinks for school-time rush",
        campaign_type="school",
        active_hours=(6, 9),
        active_days=[0, 1, 2, 3, 4],
        items=[
            CampaignItemTemplate("Juice Box", ["juice", "frooti", "maaza", "real juice", "paper boat"]),
            CampaignItemTemplate("Biscuits", ["biscuits", "marie", "parle-g", "glucose"]),
            CampaignItemTemplate("Stationery", ["pen", "pencil", "notebook", "eraser", "sharpener"]),
            CampaignItemTemplate("Chocolate", ["chocolate", "dairy milk", "kitkat", "munch"]),
            CampaignItemTemplate("Bread + Butter", ["bread", "butter", "jam"]),
        ],
    ),
    "office": CampaignTemplate(
        campaign_id="area_office",
        name="Office Canteen Bundle",
        emoji="🏢",
        description="Tea, snacks and quick lunch items for office crowd",
        campaign_type="office",
        active_hours=(8, 11),
        active_days=[0, 1, 2, 3, 4],
        items=[
            CampaignItemTemplate("Tea Bags / Powder", ["tea", "red label", "tata tea", "tea bag", "brooke bond"]),
            CampaignItemTemplate("Coffee", ["coffee", "nescafe", "bru", "instant coffee"]),
            CampaignItemTemplate("Biscuits", ["biscuits", "marie", "digestive", "good day"]),
            CampaignItemTemplate("Namkeen / Snacks", ["namkeen", "bhujia", "mixture", "chips"]),
            CampaignItemTemplate("Quick Lunch", ["maggi", "noodles", "poha", "upma mix", "ready to cook"]),
            CampaignItemTemplate("Water / Juice", ["water bottle", "water", "juice", "nimbu pani"]),
        ],
    ),
    "colony": CampaignTemplate(
        campaign_id="area_colony",
        name="Sunday Family Basket",
        emoji="🏘️",
        description="Weekend treats for colony families",
        campaign_type="colony",
        active_days=[6],  # Sunday
        items=[
            CampaignItemTemplate("Soft Drinks", ["pepsi", "coke", "sprite", "soft drink", "cold drink"]),
            CampaignItemTemplate("Ice Cream", ["ice cream", "kwality walls", "amul ice cream"]),
            CampaignItemTemplate("Snacks", ["chips", "namkeen", "popcorn", "lays", "kurkure"]),
            CampaignItemTemplate("Paneer / Cheese", ["paneer", "cheese", "amul cheese", "tofu"]),
            CampaignItemTemplate("Sweets", ["sweets", "mithai", "ladoo", "gulab jamun"]),
        ],
    ),
}


# ── Scorer ────────────────────────────────────────────────────────────────────

def _context_score(template: CampaignTemplate, now: datetime) -> int:
    """Score 0–100 based on how well the campaign fits the current moment."""
    score = 20  # base

    h   = now.hour
    dow = now.weekday()    # 0=Mon
    dom = now.day
    mon = now.month

    if template.active_hours:
        s, e = template.active_hours
        if s <= h <= e:
            score += 40

    if template.active_days is not None:
        if dow in template.active_days:
            score += 30

    if template.active_month_days:
        s, e = template.active_month_days
        if s <= dom <= e:
            score += 35

    if template.active_months is not None:
        if mon in template.active_months:
            score += 45

    return min(score, 100)


# ── Product matcher ───────────────────────────────────────────────────────────

_MATCH_SQL = """
SELECT
    p.product_id,
    p.name,
    p.barcode,
    p.unit,
    p.weight::float            AS weight,
    COALESCE(pr.price, 0.0)::float AS price,
    pr.mrp::float              AS mrp,
    COALESCE(inv.quantity, 0)  AS stock_quantity
FROM kirana_oltp.product p
JOIN kirana_oltp.inventory inv
    ON inv.product_id = p.product_id AND inv.store_id = :sid
LEFT JOIN LATERAL (
    SELECT price, mrp FROM kirana_oltp.pricing
    WHERE product_id = p.product_id AND store_id = :sid AND valid_from <= NOW()
    ORDER BY valid_from DESC LIMIT 1
) pr ON TRUE
WHERE inv.quantity > 0
  AND (
    {conditions}
  )
LIMIT 3
"""


def _match_item(conn: Any, store_id: int, item: CampaignItemTemplate) -> list[dict]:
    from sqlalchemy import text
    conditions = " OR ".join(
        f"LOWER(p.name) LIKE LOWER(:term{i})"
        for i in range(len(item.search_terms))
    )
    params: dict = {"sid": store_id}
    for i, term in enumerate(item.search_terms):
        params[f"term{i}"] = f"%{term}%"

    sql = _MATCH_SQL.format(conditions=conditions)
    rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


# ── Public API ────────────────────────────────────────────────────────────────

def get_recommended_campaigns(
    engine,
    store_id: int,
    limit: int = 3,
    now: datetime | None = None,
) -> list[dict]:
    """
    Returns up to `limit` campaigns scored by context + stock availability.
    Each campaign dict contains resolved product matches from live inventory.
    """
    if now is None:
        now = datetime.now()

    results = []

    with engine.connect() as conn:
        from sqlalchemy import text
        vertical_code = conn.execute(
            text("SELECT vertical_code FROM kirana_oltp.store WHERE store_id = :sid"),
            {"sid": store_id},
        ).scalar() or "grocery"

        for template in TEMPLATES:
            if vertical_code not in template.vertical_codes:
                continue
            ctx_score = _context_score(template, now)

            matched_items = []
            available = 0
            total_price = 0.0

            for item in template.items:
                products = _match_item(conn, store_id, item)
                best = products[0] if products else None
                matched_items.append({
                    "display_name": item.display_name,
                    "quantity": item.quantity,
                    "product": best,
                    "in_stock": best is not None and best["stock_quantity"] > 0,
                })
                if best:
                    available += 1
                    total_price += best["price"] * item.quantity

            if available == 0:
                continue  # nothing from this campaign is in stock — skip

            availability_pct = available / len(template.items)
            # Final score: context (60%) + availability (40%)
            final_score = ctx_score * 0.6 + availability_pct * 100 * 0.4

            results.append({
                "campaign_id":      template.campaign_id,
                "name":             template.name,
                "emoji":            template.emoji,
                "description":      template.description,
                "campaign_type":    template.campaign_type,
                "items":            matched_items,
                "available_count":  available,
                "total_items":      len(template.items),
                "total_price":      round(total_price, 2),
                "score":            round(final_score, 1),
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def get_area_campaigns(
    engine,
    store_id: int,
    area_types: list[str],
    now: datetime | None = None,
) -> list[dict]:
    """
    Returns campaigns tailored to the given area types (from store associations).
    One campaign per unique area_type; each enriched with live inventory matches.
    """
    if now is None:
        now = datetime.now()

    seen_types: set[str] = set()
    results = []

    with engine.connect() as conn:
        from sqlalchemy import text
        vertical_code = conn.execute(
            text("SELECT vertical_code FROM kirana_oltp.store WHERE store_id = :sid"),
            {"sid": store_id},
        ).scalar() or "grocery"

        for area_type in area_types:
            if area_type in seen_types:
                continue
            seen_types.add(area_type)

            template = AREA_TEMPLATES.get(area_type)
            if not template or vertical_code not in template.vertical_codes:
                continue

            ctx_score = _context_score(template, now)
            matched_items = []
            available = 0
            total_price = 0.0

            for item in template.items:
                products = _match_item(conn, store_id, item)
                best = products[0] if products else None
                matched_items.append({
                    "display_name": item.display_name,
                    "quantity":     item.quantity,
                    "product":      best,
                    "in_stock":     best is not None and best["stock_quantity"] > 0,
                })
                if best:
                    available += 1
                    total_price += best["price"] * item.quantity

            if available == 0:
                continue

            availability_pct = available / len(template.items)
            final_score = ctx_score * 0.6 + availability_pct * 100 * 0.4

            results.append({
                "campaign_id":    template.campaign_id,
                "name":           template.name,
                "emoji":          template.emoji,
                "description":    template.description,
                "campaign_type":  template.campaign_type,
                "items":          matched_items,
                "available_count": available,
                "total_items":    len(template.items),
                "total_price":    round(total_price, 2),
                "score":          round(final_score, 1),
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results
