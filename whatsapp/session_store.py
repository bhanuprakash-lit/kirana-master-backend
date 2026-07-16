"""
PostgreSQL-backed WhatsApp session store.
Tracks per-phone conversation state + language preference.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

logger = logging.getLogger("whatsapp.session")

# Conversation states
class State:
    NEW            = "new"               # First contact ever
    LANG_PENDING   = "lang_pending"      # Onboarding sent, awaiting language
    MAIN_MENU      = "main_menu"         # Showed kirana_welcome, awaiting choice
    SALES_MENU     = "sales_menu"        # Showed sales_dashboard, awaiting choice
    ANALYTICS_MENU = "analytics_menu"    # Showed view_analytics, awaiting choice
    IDLE           = "idle"              # Responded, back to resting


class WhatsAppSessionStore:
    def __init__(self, engine):
        self._engine = engine
        self._ensure_schema()

    def _conn(self):
        return self._engine.connect()

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Store WhatsApp phone keys in the same format Meta sends in webhooks."""
        return re.sub(r"\D", "", (phone or "").strip())

    def _ensure_schema(self):
        ddl = """
        CREATE TABLE IF NOT EXISTS wa_sessions (
            phone           VARCHAR(20) PRIMARY KEY,
            state           VARCHAR(30) NOT NULL DEFAULT 'new',
            language        VARCHAR(5)  NOT NULL DEFAULT 'en',
            store_id        BIGINT,
            owner_name      VARCHAR(255),
            store_name      VARCHAR(255),
            user_number     INT,
            last_message_at TIMESTAMPTZ,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS wa_message_log (
            id          BIGSERIAL PRIMARY KEY,
            phone       VARCHAR(20) NOT NULL,
            direction   VARCHAR(10) NOT NULL,  -- 'inbound' / 'outbound'
            content     TEXT,
            msg_type    VARCHAR(30),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        with self._conn() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
            conn.commit()

    def get(self, phone: str) -> dict | None:
        phone = self.normalize_phone(phone)
        sql = "SELECT * FROM wa_sessions WHERE phone = :p"
        with self._conn() as conn:
            row = conn.execute(text(sql), {"p": phone}).mappings().first()
            if not row:
                # Older/manual calls may have stored numbers with a leading +.
                legacy_phone = f"+{phone}" if phone else phone
                row = conn.execute(text(sql), {"p": legacy_phone}).mappings().first()
                if row:
                    conn.execute(
                        text("UPDATE wa_sessions SET phone = :new WHERE phone = :old"),
                        {"new": phone, "old": legacy_phone},
                    )
                    conn.commit()
                    row = conn.execute(text(sql), {"p": phone}).mappings().first()
        return dict(row) if row else None

    def get_or_create(self, phone: str) -> dict:
        phone = self.normalize_phone(phone)
        session = self.get(phone)
        if session:
            return session
        # Assign a sequential user number
        with self._conn() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM wa_sessions")).scalar()
            conn.execute(text("""
                INSERT INTO wa_sessions(phone, state, language, user_number, last_message_at)
                VALUES(:p, 'new', 'en', :n, NOW())
            """), {"p": phone, "n": int(count) + 1})
            conn.commit()
        return self.get(phone)

    # Columns update() is allowed to write. Column names become raw SQL
    # identifiers in the SET clause, so they MUST come from this fixed set —
    # never from arbitrary kwargs keys (SAST Finding 05: this is the most
    # externally-facing module, and a webhook-derived key would otherwise
    # become an injected identifier).
    _UPDATABLE = {
        "state", "language", "store_id", "owner_name",
        "store_name", "user_number", "last_message_at",
    }

    def update(self, phone: str, **kwargs):
        clean = {k: v for k, v in kwargs.items() if k in self._UPDATABLE}
        unknown = set(kwargs) - self._UPDATABLE
        if unknown:
            raise ValueError(f"wa_sessions has no updatable column(s): {sorted(unknown)}")
        if not clean:
            return
        phone = self.normalize_phone(phone)
        self.get(phone)
        clean["updated_at"] = datetime.now(timezone.utc)
        sets   = ", ".join(f"{k} = :{k}" for k in clean)
        params = {"phone": phone, **clean}
        sql    = f"UPDATE wa_sessions SET {sets} WHERE phone = :phone"
        with self._conn() as conn:
            conn.execute(text(sql), params)
            conn.commit()

    def log_message(self, phone: str, direction: str, content: str, msg_type: str = "text"):
        phone = self.normalize_phone(phone)
        sql = """
        INSERT INTO wa_message_log(phone, direction, content, msg_type)
        VALUES(:p, :d, :c, :t)
        """
        with self._conn() as conn:
            conn.execute(text(sql), {"p": phone, "d": direction, "c": content, "t": msg_type})
            conn.commit()

    def get_linked_store(self, phone: str) -> int | None:
        """Return the store_id linked to this WhatsApp number, if any."""
        session = self.get(phone)
        return session.get("store_id") if session else None
