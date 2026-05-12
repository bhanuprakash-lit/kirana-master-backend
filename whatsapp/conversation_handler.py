"""
Conversation state machine for the WhatsApp intelligence layer.

Flow:
  NEW          → send onboarding_template + language prompt → LANG_PENDING
  LANG_PENDING → save language → send kirana_welcome         → MAIN_MENU
  MAIN_MENU    → "Sales"       → send sales_dashboard        → SALES_MENU
               → "Analytics"  → send view_analytics         → ANALYTICS_MENU
  SALES_MENU   → "POS"        → fetch POS summary → send text → IDLE
               → "Revenue"    → fetch daily rev   → send text → IDLE
  ANALYTICS    → "Stockout"   → fetch stockout    → send text → IDLE
               → "Fast"       → fetch fast moving → send text → IDLE
               → "Profit"     → fetch margin      → send text → IDLE
  IDLE         → any message  → send kirana_welcome           → MAIN_MENU
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text as sql_text

from whatsapp.client import WhatsAppClient
from whatsapp.session_store import WhatsAppSessionStore, State
from whatsapp.templates import (
    LANG_MAP, LANG_PROMPT,
    WELCOME_BUTTONS, SALES_BUTTONS, ANALYTICS_BUTTONS,
    onboarding_payload, welcome_payload,
    sales_dashboard_payload, view_analytics_payload,
    match_button,
)
from whatsapp.intelligence import WhatsAppIntelligence

logger = logging.getLogger("whatsapp.handler")

DEFAULT_OWNER_NAME = "user"
DEFAULT_STORE_NAME = "sample store name"


def _extract_text(message: dict) -> str:
    """Pull the plain-text content out of any WhatsApp message type."""
    mtype = message.get("type", "")
    if mtype == "text":
        return message.get("text", {}).get("body", "").strip()
    if mtype == "button":
        return message.get("button", {}).get("text", "").strip()
    if mtype == "interactive":
        iv = message.get("interactive", {})
        return (
            iv.get("button_reply", {}).get("title", "") or
            iv.get("list_reply", {}).get("title", "")
        ).strip()
    return ""


class ConversationHandler:
    def __init__(
        self,
        wa_client: WhatsAppClient,
        session_store: WhatsAppSessionStore,
        intelligence: WhatsAppIntelligence,
        pos_db=None,         # SQLAlchemy engine for POS DB
        kirana_service=None, # KiranaService instance
    ):
        self.wa      = wa_client
        self.sessions = session_store
        self.intel   = intelligence
        self._pos_db  = pos_db
        self._kirana  = kirana_service

    # ── Main dispatch ─────────────────────────────────────────────────────────

    def handle(self, phone: str, message: dict, message_id: str | None = None):
        session = self.sessions.get_or_create(phone)
        text    = _extract_text(message)
        state   = session.get("state", State.NEW)

        self.sessions.update(phone, last_message_at=datetime.utcnow())

        if message_id:
            self.wa.mark_read(message_id)

        self.sessions.log_message(phone, "inbound", text, message.get("type", "text"))

        try:
            if state == State.NEW:
                self._handle_new(phone, session)

            elif state == State.LANG_PENDING:
                self._handle_lang_selection(phone, session, text)

            elif state == State.MAIN_MENU:
                self._handle_main_menu(phone, session, text)

            elif state == State.SALES_MENU:
                self._handle_sales_menu(phone, session, text)

            elif state == State.ANALYTICS_MENU:
                self._handle_analytics_menu(phone, session, text)

            else:
                # IDLE or unknown: show welcome again
                self._send_welcome(phone, session)
                self.sessions.update(phone, state=State.MAIN_MENU)

        except Exception as exc:
            logger.exception("Error handling message from %s: %s", phone, exc)
            try:
                self.wa.send_text(phone, "Sorry, something went wrong. Please try again.")
            except Exception as fallback_exc:
                logger.error(
                    "Failed to send WhatsApp error fallback to %s: %s",
                    phone,
                    fallback_exc,
                )

    # ── State handlers ────────────────────────────────────────────────────────

    def _handle_new(self, phone: str, session: dict):
        user_number = session.get("user_number", 1)
        # 1. Send onboarding template
        self.wa.send_template(onboarding_payload(phone, user_number))
        # 2. Follow up with language selection (plain text with numbered options)
        self.wa.send_text(phone, LANG_PROMPT["en"])
        self.sessions.update(phone, state=State.LANG_PENDING)
        self.sessions.log_message(phone, "outbound", "onboarding + lang prompt", "template")

    def _handle_lang_selection(self, phone: str, session: dict, text: str):
        lang = LANG_MAP.get(text.strip(), None)
        if lang is None:
            # If they type the language name directly
            t = text.strip().lower()
            if "english" in t or "en" == t:
                lang = "en"
            elif "telugu" in t or "te" == t or "తెలుగు" in text:
                lang = "te"
            elif "hindi" in t or "hi" == t or "हिंदी" in text:
                lang = "hi"

        if lang is None:
            # Unrecognised — ask again
            self.wa.send_text(phone, LANG_PROMPT["en"])
            return

        self.sessions.update(phone, language=lang)
        self._send_welcome(phone, {**session, "language": lang})
        self.sessions.update(phone, state=State.MAIN_MENU)

    def _handle_main_menu(self, phone: str, session: dict, text: str):
        lang = session.get("language", "en")
        idx  = match_button(text, lang, WELCOME_BUTTONS)

        if idx == 0:  # Sales
            self.wa.send_template(sales_dashboard_payload(phone, lang))
            self.sessions.update(phone, state=State.SALES_MENU)
            self.sessions.log_message(phone, "outbound", f"sales_dashboard_{lang}", "template")

        elif idx == 1:  # Analytics
            self.wa.send_template(view_analytics_payload(phone, lang))
            self.sessions.update(phone, state=State.ANALYTICS_MENU)
            self.sessions.log_message(phone, "outbound", f"view_analytics_{lang}", "template")

        else:
            # Unrecognised — show welcome again
            self._send_welcome(phone, session)

    def _handle_sales_menu(self, phone: str, session: dict, text: str):
        lang     = session.get("language", "en")
        store_id = session.get("store_id")
        idx      = match_button(text, lang, SALES_BUTTONS)

        if idx == 0:  # POS & Summary
            data    = self._fetch_daily_sales(store_id)
            reply   = self.intel.pos_summary(data, lang)
            self.wa.send_text(phone, reply)
            self.sessions.log_message(phone, "outbound", reply, "text")

        elif idx == 1:  # Daily Revenue
            data    = self._fetch_daily_sales(store_id)
            reply   = self.intel.daily_revenue(data, lang)
            self.wa.send_text(phone, reply)
            self.sessions.log_message(phone, "outbound", reply, "text")

        else:
            self._send_welcome(phone, session)
            self.sessions.update(phone, state=State.MAIN_MENU)
            return

        self.sessions.update(phone, state=State.IDLE)

    def _handle_analytics_menu(self, phone: str, session: dict, text: str):
        lang     = session.get("language", "en")
        store_id = session.get("store_id")
        idx      = match_button(text, lang, ANALYTICS_BUTTONS)

        if idx == 0:  # Stockout Products
            products = self._fetch_stockout(store_id)
            reply    = self.intel.stockout_products(products, lang)
            self.wa.send_text(phone, reply)
            self.sessions.log_message(phone, "outbound", reply, "text")

        elif idx == 1:  # Fast Moving SKUs
            products = self._fetch_fast_moving(store_id)
            reply    = self.intel.fast_moving_skus(products, lang)
            self.wa.send_text(phone, reply)
            self.sessions.log_message(phone, "outbound", reply, "text")

        elif idx == 2:  # High Profit Margin
            products = self._fetch_high_margin(store_id)
            reply    = self.intel.high_profit_skus(products, lang)
            self.wa.send_text(phone, reply)
            self.sessions.log_message(phone, "outbound", reply, "text")

        else:
            self._send_welcome(phone, session)
            self.sessions.update(phone, state=State.MAIN_MENU)
            return

        self.sessions.update(phone, state=State.IDLE)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _send_welcome(self, phone: str, session: dict):
        lang       = session.get("language", "en")
        owner_name, store_name = self._resolve_store_identity(phone, session)
        self.wa.send_template(welcome_payload(phone, lang, owner_name, store_name))
        self.sessions.log_message(phone, "outbound", f"kirana_welcome_{lang}", "template")

    @staticmethod
    def _usable_template_value(value: object, fallback: str) -> str:
        text = str(value or "").strip()
        if not text or text.isdigit():
            return fallback
        return text

    def _resolve_store_identity(self, phone: str, session: dict) -> tuple[str, str]:
        owner_name = self._usable_template_value(session.get("owner_name"), DEFAULT_OWNER_NAME)
        store_name = self._usable_template_value(session.get("store_name"), DEFAULT_STORE_NAME)

        if owner_name != DEFAULT_OWNER_NAME and store_name != DEFAULT_STORE_NAME:
            return owner_name, store_name

        latest = self.sessions.get(phone) or session
        store_id = latest.get("store_id")
        if store_id is None or self._pos_db is None:
            return owner_name, store_name

        try:
            with self._pos_db.connect() as conn:
                row = conn.execute(sql_text("""
                    SELECT
                        COALESCE(
                            NULLIF(MAX(u.full_name), ''),
                            NULLIF(MAX(u.username), '')
                        ) AS owner_name,
                        COALESCE(
                            NULLIF(MAX(ks.store_name), ''),
                            NULLIF(MAX(oltp.name), '')
                        ) AS store_name
                    FROM (SELECT CAST(:store_id AS BIGINT) AS store_id) s
                    LEFT JOIN kirana_app_users u
                        ON u.store_id = s.store_id
                       AND u.is_active = TRUE
                    LEFT JOIN kirana_stores ks
                        ON ks.store_id = s.store_id
                    LEFT JOIN kirana_oltp.store oltp
                        ON oltp.store_id = s.store_id
                """), {"store_id": store_id}).mappings().first()
        except Exception as exc:
            logger.warning("Failed to resolve WhatsApp store identity for %s: %s", phone, exc)
            return owner_name, store_name

        resolved_owner = self._usable_template_value(row.get("owner_name") if row else None, owner_name)
        resolved_store = self._usable_template_value(row.get("store_name") if row else None, store_name)

        updates = {}
        if resolved_owner != latest.get("owner_name"):
            updates["owner_name"] = resolved_owner
        if resolved_store != latest.get("store_name"):
            updates["store_name"] = resolved_store
        if updates:
            self.sessions.update(phone, **updates)

        return resolved_owner, resolved_store

    def _fetch_daily_sales(self, store_id: int | None) -> dict:
        if self._pos_db is None:
            return {"total_sales": 0, "total_orders": 0, "date": datetime.utcnow()}
        try:
            from pos.crud import get_daily_sales
            from sqlalchemy.orm import Session
            with Session(self._pos_db) as db:
                return get_daily_sales(db, datetime.utcnow(), store_id)
        except Exception as exc:
            logger.warning("Failed to fetch POS daily sales: %s", exc)
            return {"total_sales": 0, "total_orders": 0, "date": datetime.utcnow(), "avg_order_value": 0}

    def _fetch_stockout(self, store_id: int | None) -> list[dict]:
        if self._kirana is None:
            return []
        try:
            return self._kirana.ml.get_stockout_products(store_id, top_n=10)
        except Exception as exc:
            logger.warning("Failed to fetch stockout: %s", exc)
            return []

    def _fetch_fast_moving(self, store_id: int | None) -> list[dict]:
        if self._kirana is None:
            return []
        try:
            return self._kirana.ml.get_fast_moving(store_id, top_n=10)
        except Exception as exc:
            logger.warning("Failed to fetch fast moving: %s", exc)
            return []

    def _fetch_high_margin(self, store_id: int | None) -> list[dict]:
        if self._kirana is None:
            return []
        try:
            return self._kirana.ml.get_high_margin(store_id, top_n=10)
        except Exception as exc:
            logger.warning("Failed to fetch high margin: %s", exc)
            return []
