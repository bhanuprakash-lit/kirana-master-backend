"""Pydantic request/response models for the call-center API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .repository import (VALID_DISPOSITIONS, VALID_NEXT_ACTION, VALID_ROLES,
                        VALID_SENTIMENT, VALID_TAGS, VALID_USAGE)


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginInput(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    executive_id: int
    full_name: str
    role: str


class MeResponse(BaseModel):
    executive_id: int
    username: str
    full_name: str
    role: str


# ── Executives (manager) ──────────────────────────────────────────────────────

class ExecutiveCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=6)
    phone: Optional[str] = None
    email: Optional[str] = None
    role: str = "call_executive"

    def normalized_role(self) -> str:
        return self.role if self.role in VALID_ROLES else "call_executive"


class ExecutiveOut(BaseModel):
    executive_id: int
    username: str
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    role: str
    is_active: bool
    created_at: Optional[str] = None
    assigned_count: int = 0
    calls_today: int = 0


class ExecutiveUpdate(BaseModel):
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=6)


# ── Assignments (manager) ─────────────────────────────────────────────────────

class AssignInput(BaseModel):
    executive_id: int
    store_ids: list[int] = Field(min_length=1)


# ── Call logging (executive) ──────────────────────────────────────────────────

class CallLogInput(BaseModel):
    disposition: str
    app_usage_status: Optional[str] = None
    feedback_text: Optional[str] = None
    sentiment: Optional[str] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    next_action: Optional[str] = None
    callback_at: Optional[str] = None   # ISO8601; required when next_action='callback'
    tags: list[str] = []

    def validation_error(self) -> Optional[str]:
        if self.disposition not in VALID_DISPOSITIONS:
            return f"disposition must be one of {sorted(VALID_DISPOSITIONS)}"
        if self.app_usage_status and self.app_usage_status not in VALID_USAGE:
            return f"app_usage_status must be one of {sorted(VALID_USAGE)}"
        if self.sentiment and self.sentiment not in VALID_SENTIMENT:
            return f"sentiment must be one of {sorted(VALID_SENTIMENT)}"
        if self.next_action and self.next_action not in VALID_NEXT_ACTION:
            return f"next_action must be one of {sorted(VALID_NEXT_ACTION)}"
        if self.next_action == "callback" and not self.callback_at:
            return "callback_at is required when next_action is 'callback'"
        bad = [t for t in self.tags if t not in VALID_TAGS]
        if bad:
            return f"invalid tags {bad}; allowed {sorted(VALID_TAGS)}"
        return None


class CallLogResponse(BaseModel):
    call_id: int
    called_at: str
