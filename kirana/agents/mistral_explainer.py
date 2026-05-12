"""Mistral-powered explainer — port of the original with minor cleanups."""
import os
import logging

logger = logging.getLogger("kirana.mistral")


class MistralExplainer:
    def __init__(self, api_key: str = "", model: str = "mistral-small-latest"):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY", "")
        self.model   = model
        self._client = None

    @property
    def client(self):
        if self._client is None and self.api_key:
            try:
                from mistralai import Mistral
                self._client = Mistral(api_key=self.api_key)
            except ImportError:
                logger.warning("mistralai not installed; using fallback explainer")
        return self._client

    def explain(self, rec_type: str, ctx: dict) -> str:
        if self.client is None:
            return self._fallback(rec_type, ctx)
        prompt = self._prompt(rec_type, ctx)
        try:
            resp = self.client.chat.complete(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("Mistral explain failed: %s", exc)
            return self._fallback(rec_type, ctx)

    def _prompt(self, rec_type: str, ctx: dict) -> str:
        sku = ctx.get("sku_id", "?")
        cat = ctx.get("category", "product")
        name = ctx.get("product_name", f"SKU {sku}")

        if rec_type == "reorder_now":
            return (
                f"You are a retail inventory advisor for a kirana store owner.\n\n"
                f"Product: {name} ({cat})\n"
                f"Current stock: {ctx.get('current_stock', 0):.0f} units\n"
                f"Avg daily sales: {ctx.get('forecast_demand', 0):.1f} units/day\n"
                f"Lead time: {ctx.get('lead_time', 3)} days\n"
                f"Recommended reorder qty: {ctx.get('reorder_qty', 0):.0f} units\n"
                f"Days until stockout: {ctx.get('days_to_stockout', 0):.1f}\n\n"
                f"Write 2-3 plain sentences explaining why they must order now. Be direct and use real numbers."
            )
        if rec_type == "stockout_risk":
            return (
                f"You are a retail risk advisor for a kirana store owner.\n\n"
                f"Product: {name} ({cat})\n"
                f"Current stock: {ctx.get('current_stock', 0):.0f} units\n"
                f"Daily demand: {ctx.get('forecast_demand', 0):.1f} units/day\n"
                f"Stockout risk (7d): {ctx.get('stockout_prob', 0)*100:.0f}%\n"
                f"Days of cover: {ctx.get('days_to_stockout', 0):.1f}\n\n"
                f"Write 2-3 sentences with urgency about the risk. Use real numbers."
            )
        if rec_type == "fast_moving":
            return (
                f"You are a retail performance advisor.\n\n"
                f"Product: {name} ({cat})\n"
                f"Daily sales: {ctx.get('forecast_demand', 0):.1f} units/day\n\n"
                f"Write 2 sentences about why this is a star product and what smart action to take."
            )
        if rec_type == "profit_opportunity":
            return (
                f"You are a retail pricing advisor.\n\n"
                f"Product: {name} ({cat})\n"
                f"Current price: ₹{ctx.get('current_price', 0):.0f}\n"
                f"Effective margin: {ctx.get('expected_profit', 0):.1f}%\n\n"
                f"Write 2 sentences about the profit potential and recommended action."
            )
        return f"Explain the {rec_type} recommendation for {name} in 2 sentences."

    def _fallback(self, rec_type: str, ctx: dict) -> str:
        name = ctx.get("product_name", f"SKU {ctx.get('sku_id','?')}")
        cat  = ctx.get("category", "product")
        if rec_type == "reorder_now":
            qty  = ctx.get("reorder_qty", 0)
            days = ctx.get("days_to_stockout", 0)
            return (
                f"{name} ({cat}) needs {qty:.0f} units ordered urgently — shelves will be empty in "
                f"{days:.1f} days at the current rate. Place the order before your supplier's cutoff."
            )
        if rec_type == "stockout_risk":
            prob = ctx.get("stockout_prob", 0)
            days = ctx.get("days_to_stockout", 0)
            return (
                f"{name} ({cat}) has a {prob*100:.0f}% chance of stocking out within 7 days with only "
                f"{days:.1f} days of cover remaining — monitor closely and consider restocking."
            )
        if rec_type == "fast_moving":
            d = ctx.get("forecast_demand", 0)
            return (
                f"{name} ({cat}) is one of your fastest sellers at {d:.1f} units/day. "
                f"Keep it well-stocked to avoid losing sales on this high-velocity product."
            )
        if rec_type == "profit_opportunity":
            m = ctx.get("expected_profit", 0)
            return (
                f"{name} ({cat}) carries a {m:.1f}% effective margin — one of your top earners. "
                f"Ensure it's always available and consider promoting it to maximise revenue."
            )
        return f"Review {name} for a {rec_type.replace('_', ' ')} action."
