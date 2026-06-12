"""
WhatsApp routes — webhook receiver + manual send endpoints.
Mounted at /whatsapp in the master app.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("whatsapp.routes")
router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


def _handler(request: Request):
    return request.app.state.wa_handler


def _wa(request: Request):
    return request.app.state.wa_client


def _send_error(exc: Exception) -> HTTPException:
    message = str(exc)
    status = 503 if "WhatsApp is not configured" in message else 500
    return HTTPException(status_code=status, detail=message)


def _auth(request: Request) -> dict:
    svc = request.app.state.kirana_service
    s = request.app.state.settings

    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else ""

    if api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = svc.user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _require_store_access(store_id: int, user: dict) -> dict:
    if user.get("role") == "admin":
        return user
    if user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return user


def _require_session_access(request: Request, phone: str, user: dict) -> dict | None:
    sessions = request.app.state.wa_sessions
    session = sessions.get(phone)
    if not session:
        return None
    if user.get("role") == "admin":
        return session
    if session.get("store_id") != user.get("store_id"):
        raise HTTPException(status_code=403, detail="Access denied to this WhatsApp session")
    return session


# ── Webhook verification (GET) ────────────────────────────────────────────────

@router.get("/webhook", summary="WhatsApp webhook verification")
async def verify_webhook(
    request: Request,
    hub_mode: str       = Query(None, alias="hub.mode"),
    hub_challenge: str  = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    expected = request.app.state.settings.whatsapp_verify_token
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid mode")
    if hub_verify_token != expected:
        raise HTTPException(status_code=403, detail="Verify token mismatch")
    return int(hub_challenge)


# ── Webhook receiver (POST) ───────────────────────────────────────────────────

@router.post("/webhook", summary="Receive WhatsApp messages and status updates")
def receive_webhook(request: Request, data: dict = Body(...)):
    """
    Meta sends all incoming messages here.
    We parse, route through ConversationHandler, and return 200 immediately.
    """
    # Log every delivery so it's possible to tell whether Meta is reaching us
    # at all (the #1 ambiguity when "nothing happens"). Statuses (sent/delivered
    # /read receipts) come through here too with field == "messages" but no
    # "messages" array — log them distinctly so they aren't mistaken for inbound.
    if data.get("object") != "whatsapp_business_account":
        logger.info("WA webhook: ignored object=%s", data.get("object"))
        return {"status": "ignored"}

    handler = _handler(request)
    wa = _wa(request)
    if not wa.is_configured:
        logger.error(
            "WA webhook received a message but the client is NOT configured "
            "(access token / phone_number_id missing) — cannot reply. config_error=%s",
            wa.config_error,
        )

    processed = 0
    inbound = 0
    statuses = 0

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})

            statuses += len(value.get("statuses", []))

            contacts = value.get("contacts", [])
            for message in value.get("messages", []):
                inbound += 1
                phone_raw = (contacts[0].get("wa_id") if contacts else None) or message.get("from", "")
                phone     = phone_raw.strip()
                msg_id    = message.get("id")
                logger.info("WA inbound from %s type=%s", phone, message.get("type"))

                if phone:
                    try:
                        handler.handle(phone, message, msg_id)
                        processed += 1
                    except Exception as exc:
                        logger.exception("Error handling message from %s: %s", phone, exc)

    if inbound == 0 and statuses > 0:
        logger.info("WA webhook: %d status update(s), no inbound messages", statuses)

    return {"status": "ok", "processed": processed, "inbound": inbound, "statuses": statuses}


# ── Manual send endpoints ─────────────────────────────────────────────────────

class SendTextRequest(BaseModel):
    phone_number: str
    message: str


class SendTemplateRequest(BaseModel):
    phone_number: str
    template_name: str
    template_language: str = "en_US"
    parameters: list[str] = []


class SendMediaRequest(BaseModel):
    phone_number: str
    media_type: str        # image | document | video | audio
    media_url: str
    caption: Optional[str] = None


@router.post("/send/text", summary="Send a plain text WhatsApp message")
async def send_text(request: Request, body: SendTextRequest, user: dict = Depends(_auth)):
    try:
        result = _wa(request).send_text(body.phone_number, body.message)
        return {"success": True, "message_id": result.get("messages", [{}])[0].get("id")}
    except Exception as exc:
        raise _send_error(exc)


@router.post("/send/template", summary="Send a WhatsApp template message")
async def send_template(request: Request, body: SendTemplateRequest, user: dict = Depends(_auth)):
    from whatsapp.templates import TemplateMessage
    payload = TemplateMessage(
        template_name=body.template_name,
        language_code=body.template_language,
        components=(
            [{
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body.parameters],
            }]
            if body.parameters else []
        ),
    ).to_payload(body.phone_number)
    try:
        result = _wa(request).send_template(payload)
        return {"success": True, "message_id": result.get("messages", [{}])[0].get("id")}
    except Exception as exc:
        raise _send_error(exc)


@router.post("/send/media", summary="Send a media (image/document/video) WhatsApp message")
async def send_media(request: Request, body: SendMediaRequest, user: dict = Depends(_auth)):
    if body.media_type not in ("image", "document", "video", "audio"):
        raise HTTPException(status_code=400, detail="Invalid media_type")
    media_payload: dict = {"link": body.media_url}
    if body.caption and body.media_type == "image":
        media_payload["caption"] = body.caption
    wa_payload = {
        "messaging_product": "whatsapp",
        "to": body.phone_number,
        "type": body.media_type,
        body.media_type: media_payload,
    }
    try:
        result = _wa(request).send_template(wa_payload)
        return {"success": True, "message_id": result.get("messages", [{}])[0].get("id")}
    except Exception as exc:
        raise _send_error(exc)


# ── Session management ────────────────────────────────────────────────────────

class LinkStoreRequest(BaseModel):
    phone_number: str
    store_id: int
    owner_name: Optional[str] = None
    store_name: Optional[str] = None


@router.post("/session/link-store", summary="Link a WhatsApp number to a store")
async def link_store(request: Request, body: LinkStoreRequest, user: dict = Depends(_auth)):
    """Associate a phone number with a store so analytics data is store-scoped."""
    _require_store_access(body.store_id, user)
    sessions = request.app.state.wa_sessions
    sessions.get_or_create(body.phone_number)
    sessions.update(
        body.phone_number,
        store_id=body.store_id,
        owner_name=body.owner_name,
        store_name=body.store_name,
    )
    return {"success": True, "phone": body.phone_number, "store_id": body.store_id}


@router.get("/session/{phone}", summary="Get session state for a phone number")
async def get_session(request: Request, phone: str, user: dict = Depends(_auth)):
    session = _require_session_access(request, phone, user)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/session/{phone}", summary="Reset conversation session for a phone number")
async def reset_session(request: Request, phone: str, user: dict = Depends(_auth)):
    session = _require_session_access(request, phone, user)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    sessions = request.app.state.wa_sessions
    sessions.update(phone, state="new", language="en")
    return {"success": True, "phone": phone, "state": "new"}


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", summary="WhatsApp service health")
async def wa_health(request: Request, user: dict = Depends(_auth)):
    s = request.app.state.settings
    wa = _wa(request)
    return {
        "status": "ok" if wa.is_configured else "misconfigured",
        "phone_number_id": wa.phone_number_id or "not_configured",
        "verify_token_set": bool(s.whatsapp_verify_token),
        "access_token_set": bool(s.whatsapp_access_token),
        "send_enabled": wa.is_configured,
        "config_error": wa.config_error,
        "mistral_enabled":  bool(s.mistral_api_key),
    }
