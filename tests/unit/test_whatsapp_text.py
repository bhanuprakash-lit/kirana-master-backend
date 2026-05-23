"""Tests for whatsapp/conversation_handler.py:_extract_text.

The state machine routes off the user's text input. _extract_text has to
pull a string out of every WhatsApp message shape (plain text, button,
interactive list/button reply) reliably.
"""
from __future__ import annotations

from whatsapp.conversation_handler import _extract_text


class TestExtractText:
    def test_plain_text_message(self):
        msg = {"type": "text", "text": {"body": "Hello"}}
        assert _extract_text(msg) == "Hello"

    def test_trims_whitespace(self):
        msg = {"type": "text", "text": {"body": "  Hello  "}}
        assert _extract_text(msg) == "Hello"

    def test_button_message(self):
        msg = {"type": "button", "button": {"text": "Sales Dashboard"}}
        assert _extract_text(msg) == "Sales Dashboard"

    def test_interactive_button_reply(self):
        msg = {
            "type": "interactive",
            "interactive": {"button_reply": {"id": "sales", "title": "Sales"}},
        }
        assert _extract_text(msg) == "Sales"

    def test_interactive_list_reply(self):
        msg = {
            "type": "interactive",
            "interactive": {"list_reply": {"id": "analytics", "title": "Analytics"}},
        }
        assert _extract_text(msg) == "Analytics"

    def test_interactive_prefers_button_over_list(self):
        # If both are present (unlikely but defensive), the button wins.
        msg = {
            "type": "interactive",
            "interactive": {
                "button_reply": {"title": "Button"},
                "list_reply": {"title": "List"},
            },
        }
        assert _extract_text(msg) == "Button"

    def test_unknown_message_type_returns_empty_string(self):
        assert _extract_text({"type": "image"}) == ""
        assert _extract_text({"type": "audio"}) == ""
        assert _extract_text({}) == ""

    def test_missing_body_returns_empty_string(self):
        assert _extract_text({"type": "text"}) == ""
        assert _extract_text({"type": "text", "text": {}}) == ""
