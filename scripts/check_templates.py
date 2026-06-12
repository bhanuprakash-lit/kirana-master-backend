"""
List Meta WhatsApp message templates with their status + parameter structure.

Usage:
    python scripts/check_templates.py            # all templates (summary)
    python scripts/check_templates.py basket_promo_en udhaar_reminder_en  # detail

Needs WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID in the environment
(the same vars the backend uses).
"""
import os
import sys
import json
import requests

TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
WABA = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()
BASE = os.getenv("WHATSAPP_API_BASE_URL", "https://graph.facebook.com/v25.0").rstrip("/")

if not TOKEN or not WABA:
    sys.exit("Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID env vars first.")


def fetch_all():
    out, url = [], f"{BASE}/{WABA}/message_templates"
    params = {"limit": 100, "fields": "name,status,category,language,components"}
    while url:
        r = requests.get(url, headers={"Authorization": f"Bearer {TOKEN}"}, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("data", []))
        url = body.get("paging", {}).get("next")
        params = None  # `next` already carries the query
    return out


def count_params(component):
    """Count {{n}} placeholders in a component's text."""
    import re
    text = component.get("text", "") or ""
    nums = {int(m) for m in re.findall(r"\{\{(\d+)\}\}", text)}
    return max(nums) if nums else 0


def main():
    wanted = set(sys.argv[1:])
    templates = fetch_all()
    templates.sort(key=lambda t: t["name"])
    for t in templates:
        if wanted and t["name"] not in wanted:
            continue
        comps = t.get("components", [])
        shape = []
        for c in comps:
            ctype = c.get("type", "?")
            n = count_params(c)
            shape.append(f"{ctype}:{n}params" if n else ctype)
        print(f"\n=== {t['name']}  [{t['status']}]  lang={t.get('language')}  cat={t.get('category')}")
        print(f"    shape: {' | '.join(shape)}")
        if wanted:
            for c in comps:
                print(f"    --- {c.get('type')} ---")
                if c.get("text"):
                    print("    " + c["text"].replace("\n", "\n    "))
                if c.get("example"):
                    print("    example: " + json.dumps(c["example"], ensure_ascii=False))


if __name__ == "__main__":
    main()
