"""Gemini shelf analyzer — prompt + parser (pure, no I/O).

Ported from vision-ai/src/recognize/gemini_analyzer.py. The actual Gemini HTTP
call is done by the caller via ai.routes.call_gemini using the shared warm-TLS
client; this module only owns the prompt and the anti-hallucination parsing so it
stays import-light and unit-testable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


# Anti-hallucination prompt. Key levers (kept identical to vision-ai):
#   1. "Only report what you can actually READ" — forbids inference/guessing.
#   2. "visible_text" — forces Gemini to quote the label it based the ID on;
#      empty/vague visible_text ⇒ almost certainly hallucinated ⇒ dropped.
#   3. "confidence" — Gemini self-grades; we drop low ones.
#   4. "Do NOT invent sizes" — stops fabricated weights.
SHELF_PROMPT = """You are doing visual inventory of an Indian kirana (grocery) store shelf.

CRITICAL RULES — read carefully:
- ONLY report a product if you can actually SEE and READ its packaging in THIS image.
- DO NOT guess, infer, or assume products that "should" be on a kirana shelf.
- DO NOT invent brands, products, or sizes. If you are not sure, leave it out.
- For each product, you MUST quote the exact text you can read on the package in
  the "visible_text" field. If you cannot read any text on it, DO NOT include it.
- Only state a size/weight if it is actually printed and legible. Otherwise omit the size.
- Treat each distinct VARIANT as a separate entry (e.g. Lays Classic vs Lays Masala).
- Count the individual visible units (facings) for each variant.
- Ignore shelf fixtures, price tags, posters, and anything that is not a sellable product.
- Give a "confidence" from 0.0 to 1.0 for how sure you are of the identification.

For each product return:
- "product_name": your best identification (brand + product + size if readable)
- "visible_text": the actual text you read on the package (REQUIRED, must be non-empty)
- "count": number of visible units of this variant
- "confidence": 0.0-1.0
- "bbox": [y_top, x_left, y_bottom, x_right] as fractions 0.0-1.0 of the image

Return ONLY a valid JSON array, nothing else:
[
  {
    "product_name": "Santoor Sandal & Turmeric Soap",
    "visible_text": "SANTOOR Sandal & Turmeric Soap",
    "count": 1,
    "confidence": 0.95,
    "bbox": [0.20, 0.35, 0.85, 0.75]
  }
]

If you cannot clearly read any product, return: []
"""

# Products below this self-reported confidence are dropped as likely hallucinations.
MIN_CONFIDENCE = 0.45
GEMINI_MODEL = "gemini-2.5-flash"


@dataclass
class DetectedProduct:
    """One product detection from Gemini (before catalog matching)."""
    raw_name: str
    count: int
    x1: float            # normalized bbox [0,1], x1<x2, y1<y2
    y1: float
    x2: float
    y2: float
    visible_text: str = ""
    confidence: float = 1.0
    # which detector produced this: 'gemini' (fallback) or 'yolo' (our custom model).
    # Persisted per item so analytics can measure own-model coverage vs Gemini.
    source: str = "gemini"
    # which of the session's photos this detection came from (index into the
    # session image_url array) — lets the review UI crop it from the right photo.
    image_index: int = 0
    # filled in by the catalog matcher:
    product_id: int | None = None
    display_name: str | None = None
    sku_id: str | None = None
    match_score: float = 0.0
    is_unknown: bool = True

    def bbox_json(self) -> str:
        return json.dumps([self.x1, self.y1, self.x2, self.y2])


def parse_detections(raw_text: str) -> list[DetectedProduct]:
    """Parse Gemini's JSON array into DetectedProduct list, applying the
    anti-hallucination filters (grounding + confidence). Never raises."""
    text = re.sub(r"```(?:json)?", "", raw_text or "").strip().strip("`").strip()
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        try:
            items = json.loads(m.group())
        except json.JSONDecodeError:
            return []

    if not isinstance(items, list):
        return []

    products: list[DetectedProduct] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            name = str(item.get("product_name", "")).strip()
            visible_text = str(item.get("visible_text", "")).strip()
            confidence = float(item.get("confidence", 1.0))
            count = max(1, int(item.get("count", 1)))
            bbox = item.get("bbox", [0, 0, 1, 1])
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                bbox = [0, 0, 1, 1]
            # Gemini returns [y1, x1, y2, x2] → convert to [x1, y1, x2, y2].
            y1, x1, y2, x2 = (float(v) for v in bbox[:4])

            # ── Anti-hallucination filters ──────────────────────────────
            if not name:
                continue
            if len(visible_text) < 2:        # require readable-label grounding
                continue
            if confidence < MIN_CONFIDENCE:  # drop low self-confidence
                continue

            products.append(DetectedProduct(
                raw_name=name,
                visible_text=visible_text,
                confidence=confidence,
                count=count,
                x1=min(x1, x2), y1=min(y1, y2),
                x2=max(x1, x2), y2=max(y1, y2),
            ))
        except (TypeError, ValueError, KeyError):
            continue
    return products
