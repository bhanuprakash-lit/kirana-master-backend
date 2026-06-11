"""
WhatsApp Intelligence Layer — uses Mistral to format data as natural,
mobile-friendly responses in the user's preferred language.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whatsapp.intelligence")


class WhatsAppIntelligence:
    """Generates natural-language summaries of store data for WhatsApp."""

    def __init__(self, api_key: str, model: str = "mistral-small-latest"):
        self.api_key = api_key
        self.model   = model
        self._client = None

    @property
    def client(self):
        if self._client is None and self.api_key:
            try:
                try:
                    from mistralai import Mistral
                except ImportError:
                    from mistralai.client import Mistral
                self._client = Mistral(api_key=self.api_key)
            except ImportError:
                logger.warning("mistralai SDK import failed")
        return self._client

    def _call(self, prompt: str) -> str:
        if not self.client:
            return ""
        
        # Use class-level cache to share across instances
        if not hasattr(self.__class__, '_LLM_CACHE'):
            self.__class__._LLM_CACHE = {}
        cache = self.__class__._LLM_CACHE
        
        cache_key = prompt
        if cache_key in cache:
            return cache[cache_key]

        try:
            resp = self.client.chat.complete(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            text = resp.choices[0].message.content.strip()
            cache[cache_key] = text
            if len(cache) > 500:
                cache.clear()
            return text
        except Exception as exc:
            logger.warning("Mistral call failed: %s", exc)
            return ""

    # ── POS & Summary ─────────────────────────────────────────────────────────

    def pos_summary(self, data: dict, lang: str) -> str:
        total_sales  = data.get("total_sales", 0)
        total_orders = data.get("total_orders", 0)
        date_str     = str(data.get("date", "today"))[:10]

        if lang == "te":
            lang_instruction = "Respond entirely in Telugu (తెలుగు)."
        elif lang == "hi":
            lang_instruction = "Respond entirely in Hindi (हिंदी)."
        else:
            lang_instruction = "Respond in English."

        prompt = (
            f"You are a smart kirana store assistant sending a WhatsApp message.\n"
            f"{lang_instruction}\n\n"
            f"Today's POS Summary ({date_str}):\n"
            f"- Total Revenue: ₹{total_sales:,.2f}\n"
            f"- Orders Completed: {total_orders}\n"
            f"- Average Order Value: ₹{total_sales/max(total_orders,1):,.2f}\n\n"
            f"Write a concise 3-4 line WhatsApp message with the key numbers. "
            f"Use emojis sparingly. End with a positive note."
        )
        result = self._call(prompt)
        if not result:
            return self._fallback_pos_summary(data, lang)
        return result

    def daily_revenue(self, data: dict, lang: str) -> str:
        total_sales  = data.get("total_sales", 0)
        total_orders = data.get("total_orders", 0)
        date_str     = str(data.get("date", "today"))[:10]

        if lang == "te":
            lang_instruction = "Respond entirely in Telugu (తెలుగు)."
        elif lang == "hi":
            lang_instruction = "Respond entirely in Hindi (हिंदी)."
        else:
            lang_instruction = "Respond in English."

        prompt = (
            f"You are a kirana store WhatsApp assistant.\n"
            f"{lang_instruction}\n\n"
            f"Daily Revenue for {date_str}:\n"
            f"- Revenue: ₹{total_sales:,.2f}\n"
            f"- Orders: {total_orders}\n\n"
            f"Write a 2-3 line WhatsApp message about today's revenue. Be brief and clear."
        )
        result = self._call(prompt)
        if not result:
            return self._fallback_daily_revenue(data, lang)
        return result

    # ── Stockout Products ─────────────────────────────────────────────────────

    def stockout_products(self, products: list[dict], lang: str) -> str:
        if not products:
            return self._no_data(lang, "stockout")

        product_list = "\n".join(
            f"- {p.get('product_name','SKU '+str(p.get('sku_id','')))} "
            f"({p.get('category_name','')}) — Risk: {p.get('prob_stockout_7d',0)*100:.0f}%"
            for p in products[:10]
        )

        if lang == "te":
            lang_instruction = "Respond entirely in Telugu (తెలుగు)."
        elif lang == "hi":
            lang_instruction = "Respond entirely in Hindi (हिंदी)."
        else:
            lang_instruction = "Respond in English."

        prompt = (
            f"You are a smart kirana inventory advisor. {lang_instruction}\n\n"
            f"The following products are at risk of stocking out in 7 days:\n"
            f"{product_list}\n\n"
            f"Write a WhatsApp message (max 5 lines) alerting the shop owner. "
            f"Mention the top 3-5 most critical ones and urge immediate reordering."
        )
        result = self._call(prompt)
        return result or self._fallback_stockout(products, lang)

    # ── Fast Moving SKUs ──────────────────────────────────────────────────────

    def fast_moving_skus(self, products: list[dict], lang: str) -> str:
        if not products:
            return self._no_data(lang, "fast_moving")

        product_list = "\n".join(
            f"- {p.get('product_name','SKU '+str(p.get('sku_id','')))} "
            f"({p.get('category_name','')}) — {p.get('forecast_demand',0):.1f} units/day"
            for p in products[:10]
        )

        if lang == "te":
            lang_instruction = "Respond entirely in Telugu (తెలుగు)."
        elif lang == "hi":
            lang_instruction = "Respond entirely in Hindi (हिंदी)."
        else:
            lang_instruction = "Respond in English."

        prompt = (
            f"You are a smart kirana performance advisor. {lang_instruction}\n\n"
            f"Your fastest-selling products:\n{product_list}\n\n"
            f"Write a positive 3-4 line WhatsApp message highlighting the star sellers "
            f"and advising the owner to keep them well-stocked."
        )
        result = self._call(prompt)
        return result or self._fallback_fast_moving(products, lang)

    # ── High Profit Margin ────────────────────────────────────────────────────

    def high_profit_skus(self, products: list[dict], lang: str) -> str:
        if not products:
            return self._no_data(lang, "profit")

        product_list = "\n".join(
            f"- {p.get('product_name','SKU '+str(p.get('sku_id','')))} "
            f"({p.get('category_name','')}) — Margin: {p.get('expected_profit',0):.1f}%"
            for p in products[:10]
        )

        if lang == "te":
            lang_instruction = "Respond entirely in Telugu (తెలుగు)."
        elif lang == "hi":
            lang_instruction = "Respond entirely in Hindi (हिंदी)."
        else:
            lang_instruction = "Respond in English."

        prompt = (
            f"You are a kirana profitability advisor. {lang_instruction}\n\n"
            f"Your highest-margin products:\n{product_list}\n\n"
            f"Write a WhatsApp message (3-4 lines) advising the owner to always "
            f"keep these high-profit items stocked and promoted."
        )
        result = self._call(prompt)
        return result or self._fallback_high_margin(products, lang)

    # ── Fallbacks (no Mistral) ────────────────────────────────────────────────

    def _fallback_pos_summary(self, data: dict, lang: str) -> str:
        s = data.get("total_sales", 0)
        o = data.get("total_orders", 0)
        d = str(data.get("date", "today"))[:10]
        aov = s / max(o, 1)
        if lang == "te":
            return f"📊 నేటి POS సారాంశం ({d})\n💰 మొత్తం ఆదాయం: ₹{s:,.0f}\n🛒 ఆర్డర్లు: {o}\n📦 సగటు ఆర్డర్ విలువ: ₹{aov:,.0f}"
        if lang == "hi":
            return f"📊 आज का POS सारांश ({d})\n💰 कुल कमाई: ₹{s:,.0f}\n🛒 ऑर्डर: {o}\n📦 औसत ऑर्डर मूल्य: ₹{aov:,.0f}"
        return f"📊 Today's POS Summary ({d})\n💰 Revenue: ₹{s:,.0f}\n🛒 Orders: {o}\n📦 Avg Order: ₹{aov:,.0f}"

    def _fallback_daily_revenue(self, data: dict, lang: str) -> str:
        s = data.get("total_sales", 0)
        d = str(data.get("date", "today"))[:10]
        if lang == "te":
            return f"💰 {d} రోజువారీ ఆదాయం\n₹{s:,.0f} సంపాదించారు 🎉"
        if lang == "hi":
            return f"💰 {d} दैनिक आय\n₹{s:,.0f} की कमाई हुई 🎉"
        return f"💰 Daily Revenue ({d}): ₹{s:,.2f} 🎉"

    def _fallback_stockout(self, products: list[dict], lang: str) -> str:
        top = products[:5]
        names = ", ".join(p.get("product_name", f"SKU {p.get('sku_id')}") for p in top)
        if lang == "te":
            return f"⚠️ స్టాక్ అయిపోతున్న వస్తువులు:\n{names}\n\nత్వరగా ఆర్డర్ చేయండి!"
        if lang == "hi":
            return f"⚠️ स्टॉक खत्म होने वाले प्रोडक्ट्स:\n{names}\n\nतुरंत ऑर्डर करें!"
        return f"⚠️ Stockout Alert!\nAt-risk items: {names}\n\nReorder immediately!"

    def _fallback_fast_moving(self, products: list[dict], lang: str) -> str:
        top = products[:5]
        names = ", ".join(
            f"{p.get('product_name','?')} ({p.get('forecast_demand',0):.1f}/day)" for p in top
        )
        if lang == "te":
            return f"🚀 వేగంగా అమ్ముడవుతున్న వస్తువులు:\n{names}\n\nస్టాక్ సిద్ధంగా ఉంచండి!"
        if lang == "hi":
            return f"🚀 तेज़ बिकने वाले प्रोडक्ट्स:\n{names}\n\nस्टॉक तैयार रखें!"
        return f"🚀 Fast Moving SKUs:\n{names}\n\nKeep these well-stocked!"

    def _fallback_high_margin(self, products: list[dict], lang: str) -> str:
        top = products[:5]
        names = ", ".join(
            f"{p.get('product_name','?')} ({p.get('expected_profit',0):.1f}%)" for p in top
        )
        if lang == "te":
            return f"💎 అధిక లాభం ఇచ్చే వస్తువులు:\n{names}\n\nఈ వస్తువులను ప్రమోట్ చేయండి!"
        if lang == "hi":
            return f"💎 ज्यादा मुनाफ़ा वाले प्रोडक्ट्स:\n{names}\n\nइन्हें प्रमोट करें!"
        return f"💎 High Margin SKUs:\n{names}\n\nPromote these for maximum profit!"

    def _no_data(self, lang: str, data_type: str) -> str:
        msgs = {
            "en": "No data available right now. Please try again later.",
            "te": "ఇప్పుడు డేటా అందుబాటులో లేదు. తర్వాత మళ్ళీ ప్రయత్నించండి.",
            "hi": "अभी डेटा उपलब्ध नहीं है। कृपया बाद में पुनः प्रयास करें।",
        }
        return msgs.get(lang, msgs["en"])
