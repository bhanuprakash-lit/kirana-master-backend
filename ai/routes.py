"""
Kirana AI proxy endpoints — voice, handwriting, invoice OCR.

All requests MUST carry a valid Bearer token (same as every other /kirana/* route).
The Gemini API key lives only in the server .env — never in the mobile app.

A module-level httpx.AsyncClient is shared across requests so the TLS connection
to Google's API stays warm, removing ~200 ms handshake overhead per call.
"""
from __future__ import annotations

import json
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("ai")

router = APIRouter(prefix="/kirana/ai", tags=["AI"])

# ── Shared async HTTP client (keeps TLS connection warm) ──────────────────────

_gemini_client: httpx.AsyncClient | None = None

def get_gemini_client() -> httpx.AsyncClient:
    global _gemini_client
    if _gemini_client is None or _gemini_client.is_closed:
        _gemini_client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com",
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0),
            http2=True,  # multiplexed, faster for repeated calls
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _gemini_client


async def close_gemini_client() -> None:
    global _gemini_client
    if _gemini_client and not _gemini_client.is_closed:
        await _gemini_client.aclose()
        _gemini_client = None


# ── Auth (same pattern as kirana/routes.py) ───────────────────────────────────

def _auth(request: Request) -> dict:
    s = request.app.state.settings
    auth_hdr = request.headers.get("Authorization", "")
    bearer   = auth_hdr[len("Bearer "):] if auth_hdr.startswith("Bearer ") else ""
    api_key  = request.headers.get("X-API-Key", "")

    if api_key and api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        svc  = request.app.state.kirana_service
        user = svc.user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")


# ── Repo helper ───────────────────────────────────────────────────────────────

def _repo(request: Request):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository
    return KiranaRepository(request.app.state.engine)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gemini_api_key(request: Request) -> str:
    key = getattr(request.app.state.settings, "gemini_api_key", "") or \
          os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="AI service not configured")
    return key


def _call_gemini_sync_body(model: str, parts: list, api_key: str) -> dict:
    return {
        "model": f"models/{model}",
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }


import hashlib
_LLM_CACHE = {}

async def call_gemini(model: str, parts: list, api_key: str) -> str:
    """Public entry point for other modules (e.g. vision) to reuse the shared,
    warm-TLS Gemini client + JSON-mode call + response caching. No request needed."""
    return await _call_gemini(model, parts, api_key, None)


async def _call_gemini(
    model: str, parts: list, api_key: str, request: Request | None = None
) -> str:
    """POST to Gemini and return the raw text from the first candidate."""
    part_str = str(parts)
    cache_key = f"{model}:" + hashlib.md5(part_str.encode('utf-8')).hexdigest()
    if cache_key in _LLM_CACHE:
        return _LLM_CACHE[cache_key]

    client = get_gemini_client()
    url    = f"/v1beta/models/{model}:generateContent?key={api_key}"
    body   = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    try:
        resp = await client.post(url, json=body)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini request timed out")
    except httpx.RequestError as exc:
        logger.error("Gemini request error: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach AI service")

    if resp.status_code != 200:
        logger.error("Gemini error %s: %s", resp.status_code, resp.text[:500])
        raise HTTPException(status_code=502, detail=f"AI service error ({resp.status_code})")

    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        _LLM_CACHE[cache_key] = text
        if len(_LLM_CACHE) > 500:
            _LLM_CACHE.clear()
        return text
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected Gemini response shape: %s", data)
        raise HTTPException(status_code=502, detail="Unexpected AI response format")


# ── Schemas ───────────────────────────────────────────────────────────────────

class VoiceRequest(BaseModel):
    audio_b64: str
    mime_type: str = "audio/aac"

class HandwriteRequest(BaseModel):
    image_b64: str

class InvoiceRequest(BaseModel):
    data_b64: str
    mime_type: str

class AddCreditsRequest(BaseModel):
    feature: str   # 'voice' | 'handwrite' | 'invoice'
    count:   int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
async def ai_status(
    request: Request,
    user: dict = Depends(_auth),
):
    """Return remaining daily uses + credit balance for all AI features."""
    user_id = user.get("user_id")
    if user_id is None:
        # Admin key — unlimited, return max values
        return {f: {"used": 0, "limit": lim, "credits": 999, "remaining": lim}
                for f, lim in {"voice": 3, "handwrite": 5, "invoice": 2}.items()}
    return _repo(request).get_ai_status(user_id)


@router.post("/credits/add")
async def ai_credits_add(
    payload: AddCreditsRequest,
    request: Request,
    user: dict = Depends(_auth),
):
    """Add purchased credits for a feature. Returns updated status."""
    user_id = user.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=400, detail="Admin accounts don't use credits")
    if payload.feature not in ("voice", "handwrite", "invoice"):
        raise HTTPException(status_code=400, detail="Invalid feature")
    if payload.count <= 0:
        raise HTTPException(status_code=400, detail="count must be positive")
    return _repo(request).add_ai_credits(user_id, payload.feature, payload.count)


@router.post("/voice")
async def ai_voice(
    payload: VoiceRequest,
    request: Request,
    user: dict = Depends(_auth),
):
    """
    Transcribe audio and extract grocery items.
    Accepts base64-encoded AAC audio (16 kHz mono, max 15 s).
    Returns: {transcript: str, items: [{name, quantity}]}
    """
    user_id = user.get("user_id")
    repo = _repo(request)

    # Check & deduct limit (raises 429 if exhausted)
    if user_id is not None:
        repo.check_and_record_ai_use(user_id, "voice")

    api_key = _gemini_api_key(request)
    parts = [
        {"inline_data": {"mime_type": payload.mime_type, "data": payload.audio_b64}},
        {"text": _VOICE_PROMPT},
    ]
    raw = await _call_gemini("gemini-2.5-flash", parts, api_key, request)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Voice: non-JSON Gemini response: %s", raw[:300])
        raise HTTPException(status_code=502, detail="AI returned invalid JSON")

    # Return updated status alongside result so Flutter updates counts in one round-trip
    if user_id is not None:
        result["ai_status"] = repo.get_ai_status(user_id).get("voice")
    return result


@router.post("/handwrite")
async def ai_handwrite(
    payload: HandwriteRequest,
    request: Request,
    user: dict = Depends(_auth),
):
    """
    Read a handwritten grocery note (PNG canvas screenshot).
    Returns: {transcript: str, items: [{name, quantity}]}
    """
    user_id = user.get("user_id")
    repo = _repo(request)

    if user_id is not None:
        repo.check_and_record_ai_use(user_id, "handwrite")

    api_key = _gemini_api_key(request)
    parts = [
        {"inline_data": {"mime_type": "image/png", "data": payload.image_b64}},
        {"text": _HANDWRITE_PROMPT},
    ]
    raw = await _call_gemini("gemini-2.5-flash", parts, api_key, request)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Handwrite: non-JSON Gemini response: %s", raw[:300])
        raise HTTPException(status_code=502, detail="AI returned invalid JSON")

    if user_id is not None:
        result["ai_status"] = repo.get_ai_status(user_id).get("handwrite")
    return result


@router.post("/invoice")
async def ai_invoice(
    payload: InvoiceRequest,
    request: Request,
    user: dict = Depends(_auth),
):
    """
    Extract structured data from a supplier invoice (image or PDF).
    Returns full InvoiceExtraction JSON.
    """
    user_id = user.get("user_id")
    repo = _repo(request)

    if user_id is not None:
        repo.check_and_record_ai_use(user_id, "invoice")

    api_key = _gemini_api_key(request)
    parts = [
        {"inline_data": {"mime_type": payload.mime_type, "data": payload.data_b64}},
        {"text": _INVOICE_PROMPT},
    ]
    raw = await _call_gemini("gemini-2.0-flash", parts, api_key, request)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Invoice: non-JSON Gemini response: %s", raw[:300])
        raise HTTPException(status_code=502, detail="AI returned invalid JSON")

    if user_id is not None:
        result["ai_status"] = repo.get_ai_status(user_id).get("invoice")
    return result


# ── Prompts (identical to what was in the Flutter app) ────────────────────────

_VOICE_PROMPT = r"""
You are a grocery sales assistant for Indian kirana stores.
Listen to the audio and:
1. Transcribe what was said.
2. Extract each grocery item with its quantity.

The speech may be in Telugu, Hindi, Urdu, Tamil, Malayalam, Kannada, English, or a mix.

Return ONLY a JSON object in this exact shape — no explanation, no markdown:
{
  "transcript": "<what was said>",
  "items": [
    {"name": "<English name> (<regional word if spoken in regional language>)", "quantity": "<number + unit>"}
  ]
}

Rules for "name":
- Always English as the primary name (this goes into the database).
- If the item was spoken in a regional language, append the original word in parentheses.
- Examples: "Rice (బియ్యం)", "Wheat Flour (आटा)", "Sugar" (if said in English).

Rules for "quantity":
- Include number and unit: "2kg", "500g", "3 packets", "1 dozen", "2 liters".
- Normalise: "kilo" → "kg", "gram" → "g", "litre/liter" → "L".

Common translations:
Telugu  → biyyam/biyam→Rice, godhuma→Wheat Flour, pappu→Dal, nune→Oil, uppu→Salt,
           senagapappu→Chana Dal, minumulu→Urad Dal, pesalu→Moong Dal,
           pachi mirchi→Green Chilli, karam→Chilli Powder, pallilu→Peanuts,
           velulli→Garlic, ullipaya→Onion, aviselu→Mustard Seeds
Hindi/Urdu → chawal→Rice, gehu/atta/aata→Wheat Flour, dal→Dal, tel→Oil,
             namak→Salt, cheeni→Sugar, doodh→Milk, dahi→Curd,
             pyaaz→Onion, aloo→Potato, tamatar→Tomato,
             haldi→Turmeric, jeera→Cumin, sarson→Mustard Seeds, lahsun→Garlic
Tamil      → arisi→Rice, kodumai→Wheat Flour, paruppu→Dal, yennai→Oil, uppu→Salt
Kannada    → akki→Rice, godhi→Wheat Flour, bele→Dal, yenne→Oil, uppu→Salt
Malayalam  → ariyari→Rice, gothambu→Wheat Flour, parippu→Dal, velichenna→Coconut Oil
"""

_HANDWRITE_PROMPT = r"""
You are a grocery sales assistant for Indian kirana stores.
Look at this handwritten note and:
1. Read what is written (may be Telugu, Hindi, Urdu, Tamil, Malayalam, Kannada, English, or mixed).
2. Extract each grocery item with its quantity.

Return ONLY a JSON object in this exact shape — no explanation, no markdown:
{
  "transcript": "<everything you read from the image>",
  "items": [
    {"name": "<English name> (<regional word if written in regional script>)", "quantity": "<number + unit>"}
  ]
}

Rules for "name":
- Always English as primary (stored in the database).
- If written in a regional script, add the original word in parentheses.
- Examples: "Rice (బియ్యం)", "Wheat Flour (आटा)", "Sugar" (if written in English).

Rules for "quantity":
- Include number and unit: "2kg", "500g", "3 packets", "1 dozen", "2L".
- Normalise: "kilo"→"kg", "gram/grams"→"g", "litre/liter"→"L".
- If no unit is written, infer from context (grocery items usually kg/g, liquids L/ml).

Common translations:
Telugu  → biyyam→Rice, godhuma→Wheat Flour, pappu→Dal, nune→Oil, uppu→Salt,
           senagapappu→Chana Dal, minumulu→Urad Dal, pesalu→Moong Dal,
           pachi mirchi→Green Chilli, karam→Chilli Powder, pallilu→Peanuts
Hindi/Urdu → chawal→Rice, gehu/atta→Wheat Flour, dal→Dal, tel→Oil,
             namak→Salt, cheeni→Sugar, doodh→Milk, dahi→Curd,
             pyaaz→Onion, aloo→Potato, tamatar→Tomato, haldi→Turmeric, jeera→Cumin
Tamil      → arisi→Rice, kodumai→Wheat Flour, paruppu→Dal, yennai→Oil
Kannada    → akki→Rice, godhi→Wheat Flour, bele→Dal, yenne→Oil
Malayalam  → ariyari→Rice, gothambu→Wheat Flour, parippu→Dal, velichenna→Coconut Oil
"""

_INVOICE_PROMPT = r"""
You are extracting structured data from an Indian GST / kirana supplier invoice.
Look at the document carefully and return ALL fields as described.

RULES:
1. The first column (#, Sr.No.) is a ROW SERIAL NUMBER — NOT the quantity.
   Read the actual "Qty / Quantity" column for each item's quantity.

2. Price labels embedded in item names (e.g. "Chekkalu 300/-", "kara 25/-(new)")
   are MRP labels on the packet — NOT price_per_unit.
   item_name must be a clean product name: "Chekkalu", "kara".

3. "(2.5%)", "(15%)" next to an amount are TAX RATES, not amounts.
   "₹25.00 (2.5%)" → cgst = 25.00, cgst_rate = 2.5

4. The "Total" row at the BOTTOM of the items table is a summary — NOT a line item.
   Do NOT include it in the items list.

5. GRAND TOTAL is in the separate "Amounts" summary box (usually bottom-right).
   Do NOT use the items-table Total row as grand_total.

6. When tax summary shows two columns for the same tax, sum them.

7. The acknowledgment section at the bottom is NOT part of the items — ignore it.

8. Use null for every field not present in the document. Never hallucinate.

Return ONLY a JSON object in this exact shape — no markdown, no explanation:
{
  "vendor": {
    "vendor_name": "...",
    "vendor_gstin": "...",
    "vendor_address": "...",
    "vendor_phone": "..."
  },
  "invoice_details": {
    "invoice_number": "...",
    "invoice_date": "YYYY-MM-DD",
    "place_of_supply": "..."
  },
  "items": [
    {
      "item_name": "...",
      "quantity": 0.0,
      "unit": "kg",
      "price_per_unit": 0.0,
      "taxable_amount": 0.0,
      "cgst_rate": 0.0,
      "cgst": 0.0,
      "sgst_rate": 0.0,
      "sgst": 0.0,
      "igst_rate": null,
      "igst": null,
      "final_amount": 0.0
    }
  ],
  "totals": {
    "subtotal": 0.0,
    "cgst_total": 0.0,
    "sgst_total": 0.0,
    "igst_total": null,
    "round_off": 0.0,
    "grand_total": 0.0
  },
  "validation_status": "valid",
  "confidence_score": 0.95
}
"""
