"""
WhatsApp template catalogue and language-aware button routing.

Template naming convention (as approved in Meta Business Manager):
  onboarding_template         — number variable  {{0}}
  kirana_welcome_en/te/hi     — owner_name, store_name
  sales_dashboard_en/te/hi    — no variables (buttons drive UX)
  view_analytics_en/te/hi     — no variables
"""
from __future__ import annotations
from dataclasses import dataclass, field

__all__ = [
    "TemplateMessage",
    "onboarding_payload",
    "welcome_payload",
    "sales_dashboard_payload",
    "view_analytics_payload",
    "udhaar_reminder_payload",
    "basket_promo_payload",
    "match_button",
    "LANG_CODES",
    "LANG_MAP",
    "LANG_PROMPT",
    "WELCOME_BUTTONS",
    "SALES_BUTTONS",
    "ANALYTICS_BUTTONS",
]

print("DEBUG: Loading whatsapp.templates v3 (with udhaar and basket payloads)")


LANG_CODES = {
    "en": "en",
    "te": "te",
    "hi": "hi",
}

SALES_TEMPLATE_NAMES = {
    "en": "sales_dashboard_en",
    "te": "sales_dashboard_tee",
    "hi": "sales_dashboard_hi",
}

# ── Button text by language ────────────────────────────────────────────────────

# kirana_welcome buttons
WELCOME_BUTTONS: dict[str, list[str]] = {
    "en": ["View Sales Data", "View Inventory Data"],
    "te": ["సేల్స్ డేటా చూడండి", "అనలిటిక్స్ చూడండి"],
    "hi": ["सेल्स डेटा देखें", "एनालिटिक्स देखें"],
}

# sales_dashboard buttons
SALES_BUTTONS: dict[str, list[str]] = {
    "en": ["POS & Summary", "Daily Revenue"],
    "te": ["POS & సమ్మరీ", "రోజువారీ ఆదాయం"],
    "hi": ["POS और समरी", "दैनिक आय"],
}

# view_analytics buttons
ANALYTICS_BUTTONS: dict[str, list[str]] = {
    "en": ["Stockout Products", "Fast Moving SKUs", "High Profit Margin"],
    "te": ["అయిపోయిన స్టాక్", "ఫాస్ట్ మూవింగ్ వస్తువులు", "ఎక్కువ లాభం ఇచ్చేవి"],
    "hi": ["स्टॉक खत्म प्रोडक्ट्स", "फास्ट मूविंग प्रोडक्ट्स", "ज्यादा मुनाफ़ा वाले"],
}

# Language selection prompts (sent as text after onboarding template)
LANG_PROMPT: dict[str, str] = {
    "en": "Please choose your preferred language:\n1️⃣ English\n2️⃣ తెలుగు (Telugu)\n3️⃣ हिंदी (Hindi)\n\nReply with 1, 2, or 3.",
    "te": "Please choose your preferred language:\n1️⃣ English\n2️⃣ తెలుగు (Telugu)\n3️⃣ हिंदी (Hindi)\n\nReply with 1, 2, or 3.",
    "hi": "Please choose your preferred language:\n1️⃣ English\n2️⃣ తెలుగు (Telugu)\n3️⃣ हिंदी (Hindi)\n\nReply with 1, 2, or 3.",
}

LANG_MAP = {"1": "en", "2": "te", "3": "hi"}


@dataclass
class TemplateMessage:
    template_name: str
    language_code: str
    components: list[dict] = field(default_factory=list)

    def to_payload(self, recipient: str) -> dict:
        payload: dict = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": self.template_name,
                "language": {"code": self.language_code},
            },
        }
        if self.components:
            payload["template"]["components"] = self.components
        return payload


def onboarding_payload(recipient: str, user_number: int) -> dict:
    return TemplateMessage(
        template_name="onboarding_template",
        language_code="en",
        components=[{
            "type": "body",
            "parameters": [{"type": "text", "text": str(user_number)}],
        }],
    ).to_payload(recipient)


def welcome_payload(recipient: str, lang: str, owner_name: str, store_name: str) -> dict:
    lc = LANG_CODES.get(lang, "en")
    return TemplateMessage(
        template_name=f"kirana_welcome_{lang}",
        language_code=lc,
        components=[{
            "type": "body",
            "parameters": [
                {"type": "text", "parameter_name": "owner_name", "text": owner_name},
                {"type": "text", "parameter_name": "store_name", "text": store_name},
            ],
        }],
    ).to_payload(recipient)


def sales_dashboard_payload(recipient: str, lang: str) -> dict:
    lc = LANG_CODES.get(lang, "en")
    return TemplateMessage(
        template_name=SALES_TEMPLATE_NAMES.get(lang, "sales_dashboard_en"),
        language_code=lc,
    ).to_payload(recipient)


def view_analytics_payload(recipient: str, lang: str) -> dict:
    lc = LANG_CODES.get(lang, "en")
    return TemplateMessage(
        template_name=f"view_analytics_{lang}",
        language_code=lc,
    ).to_payload(recipient)


def udhaar_reminder_payload(
    recipient: str,
    lang: str,
    customer_name: str,
    store_name: str,
    balance: str,
    days_pending: str,
) -> dict:
    """Build the udhaar (credit) payment-reminder template payload.

    Matches the APPROVED Meta template `udhaar_reminder_{lang}` which uses NAMED
    variables across two components:
        HEADER: {{store_name}}
        BODY:   {{customer_name}}, {{balance}}, {{days_pending}}
    The template already prints the rupee sign (`*Rs{{balance}}*`), so `balance`
    must be the bare amount (no currency symbol). Reminders are business-
    initiated (the customer hasn't necessarily messaged the store), so a
    free-form text would be blocked outside the 24h window — the approved
    template is required for reliable delivery.
    """
    lc = LANG_CODES.get(lang, "en")
    return TemplateMessage(
        template_name=f"udhaar_reminder_{lang}",
        language_code=lc,
        components=[
            {
                "type": "header",
                "parameters": [
                    {"type": "text", "parameter_name": "store_name", "text": store_name},
                ],
            },
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "parameter_name": "customer_name", "text": customer_name},
                    {"type": "text", "parameter_name": "balance", "text": balance},
                    {"type": "text", "parameter_name": "days_pending", "text": days_pending},
                ],
            },
        ],
    ).to_payload(recipient)


def basket_promo_payload(
    recipient: str,
    lang: str,
    store_name: str,
    basket_name: str,
    price: float,
    item_lines: str,
    valid_to: str,
) -> dict:
    """Build the basket promotion template payload.

    Matches the APPROVED Meta template `basket_promo_{lang}`, which uses NAMED
    params across two components:
        HEADER: {{store_name}}
        BODY:   {{basket_name}}, {{price}}, {{item_lines}}, {{valid_to}}
    The template already prints the rupee sign (`*₹ {{price}}*`), so `price` must
    be the bare amount. `item_lines` must be a single line — Meta rejects params
    with newlines/tabs or >4 consecutive spaces.
    """
    lc = LANG_CODES.get(lang, "en")
    return TemplateMessage(
        template_name=f"basket_promo_{lang}",
        language_code=lc,
        components=[
            {
                "type": "header",
                "parameters": [
                    {"type": "text", "parameter_name": "store_name", "text": store_name},
                ],
            },
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "parameter_name": "basket_name", "text": basket_name},
                    {"type": "text", "parameter_name": "price", "text": f"{price:,.2f}"},
                    {"type": "text", "parameter_name": "item_lines", "text": item_lines},
                    {"type": "text", "parameter_name": "valid_to", "text": valid_to},
                ],
            },
        ],
    ).to_payload(recipient)


def match_button(text: str, lang: str, button_set: dict[str, list[str]]) -> int | None:
    """Return 0-based index of matched button, or None."""
    buttons = button_set.get(lang, button_set["en"])
    t = text.strip().lower()
    for i, btn in enumerate(buttons):
        if t == btn.lower() or t == str(i + 1):
            return i
    return None
