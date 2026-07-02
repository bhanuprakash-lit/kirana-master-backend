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
import threading
from dataclasses import dataclass

from sqlalchemy import text

logger = logging.getLogger("vision.matcher")

# token_set_ratio is 0-100; normalized to 0-1 here. Below this ⇒ unknown.
UNKNOWN_THRESHOLD = 0.60


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
    def match(self, query: str, unknown_threshold: float | None = None) -> MatchResult | None:
        """Best product match for a free-text name, or None if the catalog is empty.

        [unknown_threshold] overrides the default cutoff below which a match is
        deemed 'unknown'. Terse labels (e.g. the YOLO class 'red label tea powder')
        collide with generic catalog names on shared words like 'powder', so callers
        matching those pass a stricter cutoff → weak matches become unknown (surfaced
        for owner review) rather than a silently-wrong auto-match."""
        if not query or not query.strip():
            return None
        self._ensure_loaded()
        if not self._ids:
            return None

        idx, score = self._best(query.strip())
        if idx is None:
            return None
        cutoff = unknown_threshold if unknown_threshold is not None else UNKNOWN_THRESHOLD
        return MatchResult(
            product_id=self._ids[idx],
            display_name=self._names[idx],
            score=score,
            is_unknown=score < cutoff,
        )

    def _best(self, query: str) -> tuple[int | None, float]:
        # Preferred path — rapidfuzz (lazy import; never breaks boot if absent).
        try:
            from rapidfuzz import fuzz, process, utils
            hit = process.extractOne(
                query, self._search,
                scorer=fuzz.token_set_ratio,
                processor=utils.default_process,   # lowercases + strips punctuation
            )
            if hit is None:
                return None, 0.0
            _, score100, idx = hit
            return idx, score100 / 100.0
        except ImportError:
            pass
        # Fallback — stdlib difflib (slower, no extra dep).
        import difflib
        q = query.lower()
        best_idx, best = None, 0.0
        for i, cand in enumerate(self._search):
            r = difflib.SequenceMatcher(None, q, cand.lower()).ratio()
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
        res = matcher.match(d.raw_name, unknown_threshold=min_score)
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
