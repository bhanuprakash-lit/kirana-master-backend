"""
Intelligence triggers.

Each function:
  - Takes (store_id, repo: IntelligenceRepository)
  - Returns a dict {title, body, payload} if the notification should fire
  - Returns None to skip silently

payload.route is the deep-link screen the notification opens.
"""
from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger("kirana.intelligence.triggers")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_inr(amount) -> str:
    try:
        n = int(float(amount))
        if n >= 100_000:
            return f"₹{n/100_000:.1f}L"
        if n >= 1_000:
            return f"₹{n/1_000:.1f}K"
        return f"₹{n}"
    except Exception:
        return "₹0"


# ── Trigger functions ─────────────────────────────────────────────────────────

def morning_greeting(store_id: int, repo) -> dict | None:
    ctx = repo.get_store_context(store_id)
    store = ctx.get("store_name", "your store")
    rev = ctx.get("yesterday_revenue", 0)
    orders = int(ctx.get("yesterday_orders", 0))
    top = repo.get_yesterday_top_product(store_id)

    if orders == 0:
        bodies = [
            f"Ready to make today great? Your store is open and waiting.",
            f"New day, new sales. Let's make {store} shine today!",
            f"Good morning! Open the app and start billing — your first sale of the day is one tap away.",
        ]
        body = random.choice(bodies)
    else:
        top_text = f" {top} was your best seller." if top else ""
        body = (
            f"Yesterday you billed {_fmt_inr(rev)} across {orders} order{'s' if orders != 1 else ''}."
            f"{top_text} Let's beat it today!"
        )

    return {
        "title": f"Good morning, {store} 🌅",
        "body": body,
        "payload": {"route": "/home", "trigger": "morning_greeting"},
    }


def evening_summary(store_id: int, repo) -> dict | None:
    ctx = repo.get_store_context(store_id)
    store = ctx.get("store_name", "your store")
    rev = ctx.get("today_revenue", 0)
    orders = int(ctx.get("today_orders", 0))
    credit = ctx.get("today_credit", 0)

    if orders == 0:
        return {
            "title": f"Day wrap-up — {store}",
            "body": "No sales recorded today. Check if the POS is set up correctly, or add your first order.",
            "payload": {"route": "/home", "trigger": "evening_summary"},
        }

    credit_note = f" {_fmt_inr(credit)} is on credit." if float(credit) > 0 else ""
    return {
        "title": f"Today's Summary — {_fmt_inr(rev)} 📊",
        "body": f"{orders} order{'s' if orders != 1 else ''} billed today totalling {_fmt_inr(rev)}.{credit_note}",
        "payload": {"route": "/home", "trigger": "evening_summary"},
    }


def weekly_report(store_id: int, repo) -> dict | None:
    data = repo.get_weekly_summary(store_id)
    store_ctx = repo.get_store_context(store_id)
    store = store_ctx.get("store_name", "your store")
    rev = data.get("week_revenue", 0)
    orders = int(data.get("week_orders", 0))
    customers = int(data.get("unique_customers", 0))
    avg = data.get("avg_order_value", 0)

    if orders == 0:
        return {
            "title": "Weekly Report",
            "body": "No orders recorded last week. Start billing to see your weekly performance here.",
            "payload": {"route": "/profile/history", "trigger": "weekly_report"},
        }

    return {
        "title": f"Weekly Report — {_fmt_inr(rev)} this week 📈",
        "body": (
            f"{orders} orders · {customers} customer{'s' if customers != 1 else ''} · "
            f"avg {_fmt_inr(avg)}/order. Tap to see the full breakdown."
        ),
        "payload": {"route": "/profile/history", "trigger": "weekly_report"},
    }


def abandoned_cart(store_id: int, repo, cart_items: list, item_count: int) -> dict | None:
    store_ctx = repo.get_store_context(store_id)
    store = store_ctx.get("store_name", "your store")
    item_word = "item" if item_count == 1 else "items"

    names = [i.get("name", "") for i in cart_items[:3] if i.get("name")]
    item_list = ", ".join(names) if names else f"{item_count} {item_word}"

    return {
        "title": f"Cart waiting — {item_count} {item_word} 🛒",
        "body": f"{item_list} {'is' if item_count == 1 else 'are'} sitting in your cart. Complete the sale?",
        "payload": {"route": "/home", "trigger": "abandoned_cart"},
    }


def overdue_udhaar(store_id: int, repo) -> dict | None:
    data = repo.get_overdue_udhaar(store_id, days=7)
    customers = int(data.get("overdue_customers", 0))
    total = data.get("total_overdue", 0)

    if customers == 0:
        return None

    customer_word = "customer" if customers == 1 else "customers"
    return {
        "title": f"Udhaar Reminder — {_fmt_inr(total)} pending 💰",
        "body": (
            f"{customers} {customer_word} {'has' if customers == 1 else 'have'} "
            f"unpaid dues older than 7 days. Send a reminder?"
        ),
        "payload": {"route": "/home", "trigger": "overdue_udhaar", "tab": "finance"},
    }


def distributor_due(store_id: int, repo) -> dict | None:
    data = repo.get_distributor_dues(store_id)
    suppliers = int(data.get("pending_suppliers", 0))
    total = data.get("total_due", 0)

    if suppliers == 0:
        return None

    supplier_word = "supplier" if suppliers == 1 else "suppliers"
    return {
        "title": f"Distributor Payment Due — {_fmt_inr(total)} 📦",
        "body": (
            f"You have outstanding payments to {suppliers} {supplier_word} "
            f"totalling {_fmt_inr(total)}. Review your distributor tab."
        ),
        "payload": {"route": "/home", "trigger": "distributor_due", "tab": "finance", "subtab": "1"},
    }


def low_stock_alert(store_id: int, repo) -> dict | None:
    count = repo.get_low_stock_count(store_id)
    if count == 0:
        return None

    item_word = "item is" if count == 1 else "items are"
    return {
        "title": f"Low Stock — {count} item{'s' if count != 1 else ''} need restocking 📉",
        "body": f"{count} {item_word} below their reorder level. Order now to avoid stockouts.",
        "payload": {"route": "/home", "trigger": "low_stock_alert", "tab": "pos", "subtab": "1"},
    }


def expiry_alert(store_id: int, repo) -> dict | None:
    count = repo.get_expiring_count(store_id, days=7)
    if count == 0:
        return None

    item_word = "item" if count == 1 else "items"
    return {
        "title": f"Expiry Alert — {count} {item_word} expiring soon ⚠️",
        "body": f"{count} {item_word} will expire within 7 days. Run a discount or move stock quickly.",
        "payload": {"route": "/home", "trigger": "expiry_alert", "tab": "pos", "subtab": "1"},
    }


def inactive_customer(store_id: int, repo) -> dict | None:
    count = repo.get_inactive_customer_count(store_id, days=45)
    if count == 0:
        return None

    customer_word = "customer" if count == 1 else "customers"
    return {
        "title": f"{count} {customer_word} haven't visited in 45+ days 👥",
        "body": f"Win them back — send a personal message or run a campaign targeting inactive shoppers.",
        "payload": {"route": "/profile/customers", "trigger": "inactive_customer"},
    }


_DISCOVERY_TIPS = [
    {
        "check": lambda u: not u["has_associations"],
        "title": "Know where your customers live 🏘️",
        "body": "Add nearby apartments and hostels under Area Associations. Tag customers to areas and see which neighbourhood brings you the most revenue.",
        "route": "/profile/associations",
    },
    {
        "check": lambda u: not u["has_kpi_subs"],
        "title": "Pick your KPIs 📊",
        "body": "Choose which business metrics matter most to you — revenue, margin, footfall — and track them on your dashboard every day.",
        "route": "/profile/kpis",
    },
    {
        "check": lambda u: not u["has_customers"],
        "title": "Track your regular shoppers 🤝",
        "body": "Add customers to keep track of credit (udhaar), order history, and get insights on who shops most with you.",
        "route": "/profile/customers",
    },
    {
        "check": lambda u: not u["has_referral"],
        "title": "Let your customers bring more customers 🎯",
        "body": "Set up a referral campaign and share a QR code. When your regulars refer someone new, they earn a reward automatically.",
        "route": "/profile/referral",
    },
]


def feature_discovery(store_id: int, repo) -> dict | None:
    usage = repo.get_feature_usage(store_id)

    # Pick the first tip the store hasn't acted on yet
    for tip in _DISCOVERY_TIPS:
        try:
            if tip["check"](usage):
                return {
                    "title": tip["title"],
                    "body": tip["body"],
                    "payload": {"route": tip["route"], "trigger": "feature_discovery"},
                }
        except Exception:
            continue

    return None
