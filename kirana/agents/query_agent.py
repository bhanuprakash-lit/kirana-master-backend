"""Natural language intent parser for recommendation queries."""
from kirana.schemas import RecommendationQueryRequest

_INTENT_MAP = {
    "reorder":  (["reorder", "order", "buy", "purchase", "stock up", "replenish"], "reorder_now"),
    "risk":     (["risk", "stockout", "stock out", "empty", "critical", "danger"], "stockout_risk"),
    "velocity": (["fast", "slow", "moving", "velocity", "turnover"], "fast_moving"),
    "profit":   (["profit", "margin", "revenue", "earning", "price", "opportunity"], "profit_opportunity"),
}


def interpret(query: str, store_id: int | None = None, top_n: int = 5) -> tuple[str, RecommendationQueryRequest]:
    q = query.lower()
    intent = "general_recommendations"
    rec_type = None

    for name, (keywords, rtype) in _INTENT_MAP.items():
        if any(k in q for k in keywords):
            intent = name
            rec_type = rtype
            break

    filters = RecommendationQueryRequest(
        store_id=store_id,
        top_n=top_n,
        recommendation_type=rec_type,
        sort_by="stockout_probability" if rec_type == "stockout_risk" else "expected_profit",
    )
    return intent, filters
