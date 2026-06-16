"""Kirana Vision — shelf inventory via Gemini.

Owner photographs the shelf morning + evening; Gemini detects products; we match
each to kirana_oltp.product (→ product_id) and compute the daily sales delta
(morning − evening). Results land in the app's Vision tab.

Reuses the shared Gemini client in ai.routes (no extra SDK). The local YOLO /
counter pipeline from the vision-ai project is intentionally NOT here yet — see
VISION_INTEGRATION.md in the Flutter repo for the full roadmap.
"""
