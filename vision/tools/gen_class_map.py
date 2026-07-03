"""Generate a REVIEWABLE class_id → product_id map for the YOLO detector.

Fuzzy-matching YOLO's terse class labels ('red label tea powder') against the
catalog at runtime is unreliable (collisions on generic words). This produces a
static, human-curatable map so confident classes resolve DETERMINISTICALLY and
wrong auto-matches can be corrected once, by hand.

Each label is run through the catalog matcher; matches at/above CONFIRM_SCORE are
pre-'confirmed', the rest are left for a human to fill/fix. Output:
  vision/models/kirana_v6_class_map.json
    { "<class_index>": {"class_name","product_id","display_name","score","confirmed"} }

Usage (backend env, DATABASE_URL set):
  python -m vision.tools.gen_class_map
Re-running MERGES: existing confirmed/hand-edited rows are preserved; only new or
still-unconfirmed labels are refreshed from the matcher.
"""
from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import create_engine

from vision.matcher import get_matcher

# Which model version's labels/map to generate. Override with --version (e.g. v7).
_VERSION = os.getenv("VISION_MODEL_VERSION", "v6")
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _paths(version: str) -> tuple[str, str]:
    return (
        os.path.join(_HERE, "models", f"kirana_{version}_labels.txt"),
        os.path.join(_HERE, "models", f"kirana_{version}_class_map.json"),
    )

# Auto-confirm only near-exact matches — even 0.9 mis-binds ('black_hit'→'Black
# Shirt') and size variants collide ('dabur_honey_200g'→'Dabur Honey 500g'). Anything
# below this is left for human review rather than risking wrong stock.
CONFIRM_SCORE = 0.97


def _prettify(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=_VERSION,
                    help="model version tag, e.g. v6 or v7 (default env VISION_MODEL_VERSION or v6)")
    args = ap.parse_args()
    labels_path, out_path = _paths(args.version)

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    engine = create_engine(url)
    matcher = get_matcher(engine)

    with open(labels_path, encoding="utf-8") as f:
        labels = [ln.strip() for ln in f if ln.strip()]

    existing: dict = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)

    out: dict = {}
    confirmed = 0
    for idx, label in enumerate(labels):
        prev = existing.get(str(idx))
        # Preserve anything a human already confirmed/edited.
        if prev and prev.get("confirmed"):
            out[str(idx)] = prev
            confirmed += 1
            continue
        res = matcher.match(_prettify(label))
        score = round(res.score, 3) if res else 0.0
        is_conf = bool(res and score >= CONFIRM_SCORE)
        out[str(idx)] = {
            "class_name": label,
            "product_id": (res.product_id if res else None) if is_conf else None,
            "display_name": (res.display_name if res else None) if is_conf else (res.display_name if res else None),
            "score": score,
            "confirmed": is_conf,
        }
        if is_conf:
            confirmed += 1

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"wrote {out_path}: {len(out)} classes, {confirmed} confirmed, "
          f"{len(out) - confirmed} need review")


if __name__ == "__main__":
    main()
