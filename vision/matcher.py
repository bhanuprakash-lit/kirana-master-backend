"""Catalog matcher — map a free-text product name (from Gemini) to a real
kirana_oltp.product row, yielding product_id (the glue that ties vision detections
to inventory / pricing / baskets).

Lightweight by design: in-memory fuzzy match over product names. Prefers rapidfuzz
(fast C++ token_set_ratio, same scorer vision-ai used); if rapidfuzz isn't installed
it falls back to stdlib difflib so the server still boots and works (just slower /
slightly lower quality). NO torch / sentence-transformers / catalog.parquet.

The product catalog is a shared global table (not per-store), so the name index is
built once per process and reused across stores. Call refresh() after a bulk catalog
import if needed.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

from sqlalchemy import text

logger = logging.getLogger("vision.matcher")

# token_set_ratio is 0-100; normalized to 0-1 here. Below this ⇒ unknown.
UNKNOWN_THRESHOLD = 0.60

# Kirana packs are labelled inconsistently ('500g', '500 g', '500gm', '1 kg', '1kg'),
# and Gemini's read text vs the catalog name often disagree on spacing/spelling. Fold
# '<number> <unit>' into one canonical token so weight/volume VARIANTS still match.
_UNIT_MAP = {"gm": "g", "gms": "g", "gram": "g", "grams": "g", "kg": "kg", "kgs": "kg",
             "ml": "ml", "ltr": "l", "litre": "l", "liter": "l", "l": "l", "g": "g"}
_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(gms|gm|grams|gram|kgs|kg|ml|ltr|litre|liter|g|l)\b")


def _norm_units(s: str) -> str:
    return _UNIT_RE.sub(lambda m: m.group(1) + _UNIT_MAP.get(m.group(2), m.group(2)),
                        s.lower())


@dataclass
class MatchResult:
    product_id: int
    display_name: str
    score: float          # 0-1
    is_unknown: bool


class CatalogMatcher:
    def __init__(self, engine):
        self._engine = engine
        self._ids: list[int] = []
        self._names: list[str] = []          # canonical display name
        self._search: list[str] = []         # brand + name, used for matching
        self._loaded = False
        self._lock = threading.Lock()

    # ── Index ──────────────────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            ids, names, search = [], [], []
            with self._engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT product_id, name, COALESCE(brand, '') AS brand "
                    "FROM kirana_oltp.product"
                )).fetchall()
            for pid, name, brand in rows:
                name = (name or "").strip()
                if not name:
                    continue
                ids.append(int(pid))
                names.append(name)
                # Include brand for matching unless it's already inside the name.
                b = (brand or "").strip()
                search.append(f"{b} {name}".strip() if b and b.lower() not in name.lower() else name)
            self._ids, self._names, self._search = ids, names, search
            self._loaded = True
            logger.info("CatalogMatcher loaded %d product names", len(ids))

    def refresh(self) -> None:
        with self._lock:
            self._loaded = False
        self._ensure_loaded()

    # ── Matching ────────────────────────────────────────────────────────────────
    def match(self, query: str, unknown_threshold: float | None = None,
              visible_text: str | None = None) -> MatchResult | None:
        """Best product match for a free-text name, or None if the catalog is empty.

        [unknown_threshold] overrides the default cutoff below which a match is
        deemed 'unknown'. Terse labels (e.g. the YOLO class 'red label tea powder')
        collide with generic catalog names on shared words like 'powder', so callers
        matching those pass a stricter cutoff → weak matches become unknown (surfaced
        for owner review) rather than a silently-wrong auto-match.

        [visible_text] is the raw text read off the package (Gemini's `visible_text`).
        Look-alike VARIANTS (Santoor Sandal vs Neem; Honey 200g vs 500g) look nearly
        identical but differ in their PRINTED text — the variant/flavour/weight words.
        Feeding that text into the match lets the correct variant win where the name
        alone is ambiguous. We take the BETTER of name-only and name+text, so this can
        only help, never degrade the plain-name result."""
        if not query or not query.strip():
            return None
        self._ensure_loaded()
        if not self._ids:
            return None

        idx, score = self._best(query.strip(), visible_text)
        if idx is None:
            return None
        cutoff = unknown_threshold if unknown_threshold is not None else UNKNOWN_THRESHOLD
        return MatchResult(
            product_id=self._ids[idx],
            display_name=self._names[idx],
            score=score,
            is_unknown=score < cutoff,
        )

    def _best(self, query: str, visible_text: str | None = None) -> tuple[int | None, float]:
        """Match on the name, and — when the package text adds new tokens — also on
        name+text. Switch to the text-augmented pick when it scores strictly higher,
        OR ties but resolves to a DIFFERENT product: token_set_ratio saturates at 1.0
        whenever the query is a subset of a candidate, so a bare 'Santoor Soap' ties
        every Santoor variant — the variant/weight words in visible_text ('Sandal',
        '500g') break that tie toward the right SKU. Never lowers the name-only score
        (so it can't downgrade a borderline match into 'unknown')."""
        idx, score = self._extract(query)
        vt = (visible_text or "").strip()
        if vt and vt.lower() not in query.lower():
            idx2, score2 = self._extract(f"{query} {vt}")
            if idx2 is not None and (score2 > score or (score2 >= score and idx2 != idx)):
                return idx2, score2
        return idx, score

    def _extract(self, query: str) -> tuple[int | None, float]:
        # Preferred path — rapidfuzz (lazy import; never breaks boot if absent).
        try:
            from rapidfuzz import fuzz, process, utils

            def _proc(s: str) -> str:
                # Normalize weights BEFORE default_process so '500 g'/'500gm' (package
                # text) and '500g' (catalog) become the same token → weight variants
                # disambiguate. Applied to both query and choices by rapidfuzz.
                return utils.default_process(_norm_units(s))

            hit = process.extractOne(
                query, self._search,
                scorer=fuzz.token_set_ratio,
                processor=_proc,
            )
            if hit is None:
                return None, 0.0
            _, score100, idx = hit
            return idx, score100 / 100.0
        except ImportError:
            pass
        # Fallback — stdlib difflib (slower, no extra dep).
        import difflib
        q = _norm_units(query)
        best_idx, best = None, 0.0
        for i, cand in enumerate(self._search):
            r = difflib.SequenceMatcher(None, q, _norm_units(cand)).ratio()
            if r > best:
                best, best_idx = r, i
        return best_idx, best


# ── Module singleton ─────────────────────────────────────────────────────────────
_matcher: CatalogMatcher | None = None


def get_matcher(engine) -> CatalogMatcher:
    global _matcher
    if _matcher is None:
        _matcher = CatalogMatcher(engine)
    return _matcher


def match_detections(detections, engine, min_score: float | None = None) -> None:
    """Fill product_id / display_name / sku_id / match_score / is_unknown on each
    DetectedProduct in place, using the catalog matcher. [min_score] raises the bar
    for what counts as a confident match (used for terse YOLO class labels)."""
    matcher = get_matcher(engine)
    for d in detections:
        # Already resolved (e.g. YOLO's curated class map) — don't override it.
        if d.product_id is not None and not d.is_unknown:
            continue
        res = matcher.match(d.raw_name, unknown_threshold=min_score,
                            visible_text=getattr(d, "visible_text", None))
        if res is not None and not res.is_unknown:
            d.product_id = res.product_id
            d.display_name = res.display_name
            d.match_score = res.score
            d.is_unknown = False
        else:
            d.product_id = None
            d.display_name = None
            d.match_score = res.score if res else 0.0
            d.is_unknown = True
