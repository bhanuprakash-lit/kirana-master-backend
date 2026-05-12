"""
WhatsApp Business API client — adapted from the existing integration.
Adds support for interactive button messages.
"""
from __future__ import annotations

import re
import time
import logging
from typing import Optional
from urllib.parse import urlparse
import requests

logger = logging.getLogger("whatsapp.client")


class WhatsAppClient:
    def __init__(self, access_token: str, phone_number_id: str,
                 base_url: str = "https://graph.facebook.com/v25.0",
                 timeout: int = 30, max_retries: int = 3):
        self.access_token     = (access_token or "").strip()
        self.phone_number_id  = self._clean_phone_number_id(phone_number_id)
        self.base_url         = (base_url or "https://graph.facebook.com/v25.0").rstrip("/")
        self.timeout          = timeout
        self.max_retries      = max_retries
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        self.config_error = self._validate_config()
        self._url = (
            f"{self.base_url}/{self.phone_number_id}/messages"
            if self.config_error is None
            else ""
        )

    @property
    def is_configured(self) -> bool:
        return self.config_error is None

    # ── Public send methods ───────────────────────────────────────────────────

    def send_text(self, to: str, body: str) -> dict:
        return self._post({
            "messaging_product": "whatsapp",
            "to": self._fmt(to),
            "type": "text",
            "text": {"body": body, "preview_url": False},
        })

    def send_template(self, payload: dict) -> dict:
        """Send a pre-built template payload dict directly."""
        return self._post(payload)

    def send_interactive_buttons(self, to: str, body: str, buttons: list[str],
                                  header: str | None = None) -> dict:
        """
        Send an interactive quick-reply button message (max 3 buttons).
        buttons: list of button title strings.
        """
        btn_objs = [
            {"type": "reply", "reply": {"id": str(i), "title": t[:20]}}
            for i, t in enumerate(buttons[:3])
        ]
        payload: dict = {
            "messaging_product": "whatsapp",
            "to": self._fmt(to),
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {"buttons": btn_objs},
            },
        }
        if header:
            payload["interactive"]["header"] = {"type": "text", "text": header}
        return self._post(payload)

    def send_interactive_list(self, to: str, body: str, button_label: str,
                               sections: list[dict]) -> dict:
        """Send an interactive list message (supports > 3 options)."""
        return self._post({
            "messaging_product": "whatsapp",
            "to": self._fmt(to),
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body":   {"text": body},
                "action": {"button": button_label, "sections": sections},
            },
        })

    def mark_read(self, message_id: str) -> bool:
        try:
            self._post({
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            })
            return True
        except Exception:
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt(phone: str) -> str:
        """Format phone number for Meta (must have country code, no +)."""
        p = re.sub(r"\D", "", phone)
        # If it's 10 digits, assume India (91)
        if len(p) == 10:
            return f"91{p}"
        return p

    @staticmethod
    def _clean_phone_number_id(phone_number_id: str | None) -> str:
        raw = (phone_number_id or "").strip().strip('"').strip("'")
        if not raw:
            return ""

        # Accept a full Graph URL or a mistakenly pasted "/{id}/messages" path,
        # but store only the numeric phone-number ID Meta expects.
        parsed = urlparse(raw)
        path = parsed.path if parsed.scheme and parsed.netloc else raw
        parts = [p for p in path.split("/") if p]
        if parts and parts[-1] == "messages":
            parts = parts[:-1]
        for part in reversed(parts):
            if re.fullmatch(r"\d{8,}", part):
                return part
        return raw

    def _validate_config(self) -> str | None:
        if not self.access_token:
            return "WHATSAPP_ACCESS_TOKEN is not configured"
        if not self.phone_number_id:
            return "WHATSAPP_PHONE_NUMBER_ID is not configured"
        if self.phone_number_id == "messages":
            return "WHATSAPP_PHONE_NUMBER_ID must be the numeric Meta phone-number ID, not 'messages'"
        if not re.fullmatch(r"\d{8,}", self.phone_number_id):
            return "WHATSAPP_PHONE_NUMBER_ID must be the numeric Meta phone-number ID"
        return None

    def _post(self, payload: dict, attempt: int = 1) -> dict:
        if self.config_error:
            raise RuntimeError(f"WhatsApp is not configured: {self.config_error}")

        try:
            resp = requests.post(self._url, json=payload, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 429 and attempt < self.max_retries:
                time.sleep(2 ** attempt)
                return self._post(payload, attempt + 1)
            if resp.status_code >= 400:
                try:
                    error_obj = resp.json().get("error", {})
                except ValueError:
                    error_obj = {"message": resp.text}
                err = error_obj.get("message", resp.text)
                if resp.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    return self._post(payload, attempt + 1)
                logger.error(
                    "WhatsApp API error %s: %s | details=%s | template=%s",
                    resp.status_code,
                    err,
                    {
                        k: v
                        for k, v in error_obj.items()
                        if k in {"code", "error_subcode", "type", "error_data", "fbtrace_id"}
                    },
                    payload.get("template"),
                )
                raise RuntimeError(f"WhatsApp API {resp.status_code}: {err}")
            return resp.json()
        except requests.exceptions.RequestException as exc:
            if attempt < self.max_retries:
                return self._post(payload, attempt + 1)
            raise RuntimeError(f"WhatsApp request failed: {exc}") from exc
