"""
Kirana AI Service — orchestrates ML adapter + repository + explainer.
Replaces the old recommendation pipeline with the new ml_models outputs.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

from config import get_settings
from kirana.ml_adapter import MLAdapter
from kirana.schemas import (
    AgentQueryRequest, AgentQueryResponse,
    AuthUser, ExplainRequest, ExplainResponse,
    LoginRequest, LoginResponse, PhoneLoginRequest,
    ProfileUpdateRequest, RegisterStoreOwnerRequest, RegisterStoreOwnerResponse,
    RecommendationItem, RecommendationListResponse,
    RecommendationQueryRequest, SnapshotSummary,
    StoreRecommendationsResponse, StoreUpdateRequest,
    UserCreateRequest, UserCreateResponse,
    InventorySnapshotWriteRequest, InventorySnapshotWriteResponse,
    StoreSnapshotResponse,
    IssueReportCreate
)
from kirana.agents.mistral_explainer import MistralExplainer
from kirana.agents.query_agent import interpret as interpret_query
from whatsapp.templates import udhaar_reminder_payload

logger = logging.getLogger("kirana.service")

def _safe(v):
    """Return None if value is NaN/inf, else the value."""
    import math
    if v is None:
        return None
    try:
        if math.isnan(v) or math.isinf(v):
            return None
    except TypeError:
        pass
    return v


_PRIORITY = {
    "stockout_risk":     lambda r: "high" if (_safe(r.get("stockout_prob")) or 0) >= 0.8 else "medium",
    "reorder_now":       lambda r: "high" if (_safe(r.get("days_to_stockout")) or 99) <= 3 else "medium",
    "fast_moving":       lambda _: "medium",
    "profit_opportunity": lambda _: "medium",
    "dead_stock":        lambda r: "high" if (_safe(r.get("current_stock")) or 0) >= 20 else "medium",
}


def _round_int(value) -> int:
    """Round a decimal to the nearest integer for owner-friendly text."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _round_days(value) -> str:
    """Format days as 'today', 'tomorrow', or 'around N days'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "soon"
    if v <= 0.5:
        return "today"
    if v <= 1.5:
        return "tomorrow"
    return f"around {int(round(v))} days"


def _round_rupees(value) -> int:
    """Round rupees to a useful granularity (10 if <1k, else 100)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if v < 1000:
        return int(round(v / 10) * 10)
    return int(round(v / 100) * 100)


def _msg_stockout(r: dict) -> str:
    """Stockout risk: explain *what's running out and how soon*."""
    days = _safe(r.get("days_to_stockout"))
    velocity = _safe(r.get("forecast_demand")) or 0
    stock = _round_int(_safe(r.get("current_stock")) or 0)
    prob_pct = _round_int((_safe(r.get("stockout_prob")) or 0) * 100)
    if days is not None and days > 0 and velocity > 0:
        return (
            f"{stock} units left, selling around {_round_int(velocity)} units/day. "
            f"Likely to run out {_round_days(days)} "
            f"(AI confidence {prob_pct}%)."
        )
    return (
        f"Predicted to run out within a week "
        f"(AI confidence {prob_pct}%). Check stock and plan a reorder."
    )


def _msg_reorder(r: dict) -> str:
    """Reorder: explain qty, why now, and how long it covers."""
    qty = _round_int(_safe(r.get("reorder_qty")) or 0)
    days = _safe(r.get("days_to_stockout"))
    velocity = _safe(r.get("forecast_demand")) or 0
    stock = _round_int(_safe(r.get("current_stock")) or 0)
    rp = _round_int(_safe(r.get("reorder_point")) or 0)
    cover_days = (qty / velocity) if velocity > 0 else None
    parts = []
    if stock > 0 and rp > 0:
        parts.append(f"Stock {stock} units, below reorder point of {rp}")
    if velocity > 0:
        parts.append(f"selling around {_round_int(velocity)} units/day")
    if days is not None and days >= 0:
        # _round_days returns "around N days" already
        parts.append(f"only {_round_days(days)} of stock left")
    head = ", ".join(parts) if parts else "Stock has dipped below the reorder point"
    tail = f". Order around {qty} units"
    if cover_days:
        tail += f" (will last around {int(round(cover_days))} days)"
    return head + tail + "."


def _msg_fast_moving(r: dict) -> str:
    """Fast-moving: explain it's a top mover and why to keep stocked."""
    velocity = _safe(r.get("forecast_demand")) or 0
    stock = _safe(r.get("current_stock"))
    parts = [f"Top mover — selling around {_round_int(velocity)} units/day"]
    if stock is not None and stock > 0 and velocity > 0:
        cover = stock / velocity
        # _round_days already includes "around"/"today"/"tomorrow", so don't
        # prepend "about" — gives "current stock lasts around 4 days", not
        # "current stock covers about around 4 days".
        parts.append(f"current stock lasts {_round_days(cover)}")
    return ". ".join(parts) + ". Keep it shelf-ready, especially in the morning rush."


def _msg_profit(r: dict) -> str:
    """Profit opportunity: explain margin and recommend action."""
    margin = _safe(r.get("effective_margin")) or 0
    velocity = _safe(r.get("forecast_demand")) or 0
    proj = _safe(r.get("expected_profit")) or 0
    parts = [f"Margin around {_round_int(margin)}% (one of your best in this store)"]
    if velocity > 0:
        parts.append(f"selling around {_round_int(velocity)} units/day")
    if proj > 0:
        parts.append(f"that's about ₹{_round_rupees(proj):,} profit over the next month")
    return ". ".join(parts) + ". Promote it and keep at eye-level."


def _msg_dead_stock(r: dict) -> str:
    """Dead stock: explain it's tied-up capital with no movement."""
    stock = _round_int(_safe(r.get("current_stock")) or 0)
    price = _safe(r.get("current_price"))
    capital = (stock * price) if (price is not None and price > 0) else None
    base = f"Hardly any sales in the last 3 weeks, {stock} units sitting on shelf"
    if capital and capital > 0:
        base += f" (about ₹{_round_rupees(capital):,} tied up)"
    return base + ". Try a markdown or return-to-vendor to free up cash."


_MSG = {
    "stockout_risk":      _msg_stockout,
    "reorder_now":        _msg_reorder,
    "fast_moving":        _msg_fast_moving,
    "profit_opportunity": _msg_profit,
    "dead_stock":         _msg_dead_stock,
}


def _build_item(row: dict) -> RecommendationItem:
    rt = row.get("recommendation_type", "")
    pri_fn  = _PRIORITY.get(rt, lambda _: "low")
    msg_fn  = _MSG.get(rt, lambda _: "")
    return RecommendationItem(
        store_id=int(row.get("store_id", 0)),
        sku_id=int(row.get("sku_id", 0)),
        product_name=str(row.get("product_name", "")),
        category_name=str(row.get("category_name", "")),
        recommendation_type=rt,
        priority=pri_fn(row),
        stockout_probability=_safe(row.get("stockout_prob")),
        prob_stockout_3d=_safe(row.get("prob_stockout_3d")),
        prob_stockout_7d=_safe(row.get("prob_stockout_7d")),
        prob_stockout_30d=_safe(row.get("prob_stockout_30d")),
        reorder_qty=_safe(row.get("reorder_qty")),
        forecast_demand=_safe(row.get("forecast_demand")),
        current_stock=_safe(row.get("current_stock")),
        days_to_stockout=_safe(row.get("days_to_stockout")),
        current_price=_safe(row.get("current_price")),
        optimal_price=_safe(row.get("optimal_price")),
        price_change_pct=_safe(row.get("price_change_pct")),
        expected_profit_impact=_safe(row.get("expected_profit")),
        effective_margin=_safe(row.get("effective_margin")),
        reorder_point=_safe(row.get("reorder_point")),
        message=msg_fn(row),
    )


class KiranaService:
    def __init__(self, db_conn, settings=None):
        self._db = db_conn       # psycopg2 connection or SQLAlchemy engine
        self._s  = settings or get_settings()
        # Pass the engine so the adapter can resolve store_id from inventory
        # if a legacy CSV without store_id is encountered.
        self.ml  = MLAdapter(self._s.ml_results_dir, engine=db_conn)
        self.explainer = MistralExplainer(
            api_key=self._s.mistral_api_key,
            model=self._s.mistral_model,
        )

    def bootstrap(self):
        # ML recommendation frames are loaded lazily on first use (get_frame /
        # get_ml_state call refresh()), NOT at startup: eager-loading the ~170k
        # row reorder CSV + building the joined state peaked over the 1Gi
        # container limit and OOM-killed the process before uvicorn could bind
        # the port, crash-looping the whole app. Deferring lets the service
        # boot and serve non-ML traffic immediately.
        logger.info("Kirana service bootstrapped (ML frames deferred to first use)")

    # ── Health ────────────────────────────────────────────────────────────────

    def health(self) -> dict:
        df = self.ml.get_frame()
        return {
            "status": "ok",
            "ml_rows": int(len(df)),
            "ml_results_dir": self._s.ml_results_dir,
        }

    # ── Auth / Users ──────────────────────────────────────────────────────────

    def login(self, req: LoginRequest, telemetry: dict | None = None) -> LoginResponse:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        logger.info(f"Login attempt for user: {req.username}")
        user = repo.authenticate_user(req.username.strip(), req.password)
        if not user:
            logger.warning(f"Authentication failed for user: {req.username}")
            raise ValueError("Invalid username or password")

        try:
            token = repo.create_session(user["user_id"], login_method="password", telemetry=telemetry)
            logger.info(f"Session created for user_id: {user['user_id']}")
            res = LoginResponse(access_token=token, user=AuthUser(**user))
            return res
        except Exception as e:
            logger.exception(f"Error during login processing for {req.username}: {e}")
            raise

    def user_by_token(self, token: str) -> dict | None:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_user_by_token(token)

    def phone_login(self, req: PhoneLoginRequest, telemetry: dict | None = None) -> LoginResponse:
        """Log in using a Firebase-verified phone number. Raises ValueError if no account found."""
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        user = repo.authenticate_by_phone(req.phone_number, req.firebase_uid)
        if not user:
            raise ValueError(f"No account found for phone number {req.phone_number}")
        token = repo.create_session(user["user_id"], login_method="phone", telemetry=telemetry)
        logger.info(f"Phone login for user_id={user['user_id']} phone={req.phone_number}")
        return LoginResponse(access_token=token, user=AuthUser(**user))

    def check_username_available(self, username: str) -> bool:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).check_username_available(username)

    def register_store_owner(self, req: RegisterStoreOwnerRequest, telemetry: dict | None = None) -> RegisterStoreOwnerResponse:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        try:
            store, user = repo.register_store_owner_atomic(
                store_name=req.store_name,
                store_type=req.store_type,
                vertical_code=req.vertical_code,
                footfall=req.footfall,
                budget=req.budget,
                location=req.location,
                region=req.region,
                city=req.city,
                username=req.username.strip(),
                password=req.password,
                full_name=req.full_name.strip(),
                email=req.email,
                phone_number=req.phone_number,
                firebase_uid=req.firebase_uid,
                latitude=req.latitude,
                longitude=req.longitude,
            )
        except Exception as exc:
            msg = str(exc)
            if "unique" in msg.lower() and "username" in msg.lower():
                raise ValueError("An account with this username already exists")
            if "unique" in msg.lower() and "phone" in msg.lower():
                raise ValueError("An account with this phone number already exists")
            raise
        token = repo.create_session(user["user_id"], login_method="register", telemetry=telemetry)
        return RegisterStoreOwnerResponse(access_token=token, user=AuthUser(**user), store=store)

    def create_user(self, req: UserCreateRequest) -> UserCreateResponse:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        user = repo.create_user(req.username.strip(), req.password, req.full_name.strip(),
                                 req.role, req.store_id)
        return UserCreateResponse(**user)

    def list_users(self) -> list[dict]:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).list_users()

    def delete_user(self, user_id: int) -> bool:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).delete_user(user_id)

    def update_my_profile(self, user_id: int, req: ProfileUpdateRequest) -> AuthUser:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        updated = KiranaRepository(self._db).update_user_profile(
            user_id, full_name=req.full_name, password=req.password
        )
        if not updated:
            raise ValueError("User not found")
        return AuthUser(**updated)

    # ── Stores ────────────────────────────────────────────────────────────────

    def list_stores(self, only_store_ids: list[int] | None = None) -> list[dict]:
        """Stores with their ML summary. Pass `only_store_ids` to summarise just
        those (the common case: a store owner only needs their own store) instead
        of computing a summary for every store and discarding the rest."""
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        stores = repo.list_store_master()
        if only_store_ids is not None:
            wanted = {int(s) for s in only_store_ids}
            stores = [s for s in stores if int(s["store_id"]) in wanted]
        result = []
        for s in stores:
            summary = self.ml.store_summary(int(s["store_id"]))
            result.append({**s, **summary})
        return result

    def update_store_profile(self, store_id: int, req: StoreUpdateRequest) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        updated = KiranaRepository(self._db).update_store(
            store_id, store_name=req.store_name, store_type=req.store_type,
            footfall=req.footfall, budget=req.budget, daily_budget=req.daily_budget,
            location=req.location, region=req.region,
            city=req.city, vertical_code=req.vertical_code,
            gst_enabled=req.gst_enabled,
        )
        if not updated:
            raise ValueError("Store not found")
        return updated

    # ── Recommendations ───────────────────────────────────────────────────────

    def _apply_filters(self, df: pd.DataFrame, q: RecommendationQueryRequest) -> pd.DataFrame:
        if df.empty:
            return df
        if q.store_id is not None:
            df = df[df["store_id"] == q.store_id]
        if q.sku_ids:
            df = df[df["sku_id"].isin(q.sku_ids)]
        if q.only_reorder:
            df = df[(df["recommendation_type"] == "reorder_now") & (df["reorder_qty"] > 0)]
        if q.recommendation_type:
            df = df[df["recommendation_type"] == q.recommendation_type]
        return df

    def _get_patched_items(self, store_id: int) -> list[RecommendationItem]:
        """Get recommendations: ML CSV results first, then SQL-scored fallbacks for missing SKUs."""
        from sqlalchemy import text

        # 1. Base results from ML CSVs
        df = self.ml.get_frame()
        if not df.empty:
            df = df[df["store_id"] == store_id]
        items = [_build_item(r) for r in df.to_dict("records")]
        existing_skus = {i.sku_id for i in items}

        # 2. SQL-scored fallbacks — real velocity + risk signals, no fake cycling
        try:
            with self._db.connect() as conn:
                sql = """
                SELECT
                    i.product_id,
                    i.quantity,
                    p.name,
                    c.name AS category,
                    COALESCE(pr.price, 100)            AS price,
                    COALESCE(sales.avg_daily_sales, 0) AS avg_daily_sales,
                    COALESCE(sales.last_sale_days_ago, 999) AS last_sale_days_ago
                FROM kirana_oltp.inventory i
                JOIN kirana_oltp.product  p ON i.product_id  = p.product_id
                JOIN kirana_oltp.category c ON p.category_id = c.category_id
                LEFT JOIN LATERAL (
                    SELECT price
                    FROM kirana_oltp.pricing pr
                    WHERE pr.product_id = i.product_id AND pr.store_id = i.store_id
                      AND pr.valid_from <= NOW()
                      AND (pr.valid_to IS NULL OR pr.valid_to >= NOW())
                    ORDER BY pr.valid_from DESC LIMIT 1
                ) pr ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        ROUND(SUM(oi.quantity)::numeric / 30.0, 4)              AS avg_daily_sales,
                        EXTRACT(DAY FROM NOW() - MAX(o.order_date))::int         AS last_sale_days_ago
                    FROM kirana_oltp.orders o
                    JOIN kirana_oltp.order_item oi ON o.order_id = oi.order_id
                    WHERE oi.product_id = i.product_id
                      AND o.store_id   = i.store_id
                      AND o.order_date >= NOW() - INTERVAL '30 days'
                ) sales ON TRUE
                WHERE i.store_id = :sid
                """
                inv_rows = conn.execute(text(sql), {"sid": store_id}).mappings().all()

            for row in inv_rows:
                sku = row["product_id"]
                if sku in existing_skus:
                    continue

                qty          = max(float(row["quantity"] or 0), 0)
                avg_daily    = max(float(row["avg_daily_sales"] or 0), 0.01)
                last_sale_ago = int(row["last_sale_days_ago"] or 999)
                price        = float(row["price"] or 100)
                margin       = 25.0  # kirana typical gross margin ~25%

                days_left   = qty / avg_daily
                reorder_qty = max(round(avg_daily * 14), 5)  # 2 weeks of stock

                # Assign type from real signals (priority order matters)
                if qty <= 0:
                    rtype, priority, stockout_prob = "stockout_risk", "high", 1.0
                elif days_left <= 3:
                    rtype, priority = "stockout_risk", "high"
                    stockout_prob = round(min(0.70 + (3 - days_left) * 0.10, 0.99), 2)
                elif days_left <= 7:
                    rtype, priority = "reorder_now", "high"
                    stockout_prob = round(0.30 + (7 - days_left) * 0.08, 2)
                elif last_sale_ago > 45:
                    rtype, priority, stockout_prob = "dead_stock", "low", 0.0
                elif avg_daily >= 2.0:
                    rtype, priority, stockout_prob = "fast_moving", "medium", 0.15
                elif margin > 28:
                    rtype, priority, stockout_prob = "profit_opportunity", "medium", 0.10
                else:
                    rtype, priority, stockout_prob = "reorder_now", "low", 0.20

                # Contextual message based on real numbers
                if rtype == "stockout_risk":
                    msg = (f"Only {qty:.0f} units left — runs out in ~{days_left:.0f} days at "
                           f"current rate of {avg_daily:.1f}/day. Restock {reorder_qty} units urgently.")
                elif rtype == "reorder_now":
                    msg = (f"{qty:.0f} units in stock (~{days_left:.0f} days). "
                           f"Reorder {reorder_qty} units to stay covered for 2 weeks.")
                elif rtype == "dead_stock":
                    msg = (f"No sales in {last_sale_ago} days. {qty:.0f} units sitting idle. "
                           f"Consider a promotion or return to supplier.")
                elif rtype == "fast_moving":
                    msg = (f"Selling {avg_daily:.1f} units/day. {qty:.0f} units left "
                           f"(~{days_left:.0f} days). Keep shelf stocked.")
                else:
                    msg = (f"{margin:.0f}% gross margin. {qty:.0f} units in stock. "
                           f"Cross-sell with complementary products to boost basket value.")

                items.append(RecommendationItem(
                    store_id=store_id,
                    sku_id=sku,
                    product_name=str(row["name"]),
                    category_name=str(row["category"]),
                    recommendation_type=rtype,
                    priority=priority,
                    current_stock=qty,
                    current_price=price,
                    effective_margin=margin,
                    days_to_stockout=round(days_left, 1),
                    reorder_qty=float(reorder_qty),
                    stockout_probability=stockout_prob,
                    forecast_demand=round(avg_daily, 2),
                    expected_profit_impact=(margin * avg_daily * 7) if rtype == "profit_opportunity" else 0.0,
                    message=msg,
                ))
        except Exception:
            logger.exception("Failed to build recommendation fallbacks for store %s", store_id)

        return items

    def query_recommendations(self, q: RecommendationQueryRequest) -> RecommendationListResponse:
        store_id = q.store_id or 1
        items = self._get_patched_items(store_id)

        # Apply schema-level filters manually since we have a list of objects now
        if q.sku_ids:
            items = [i for i in items if i.sku_id in q.sku_ids]
        if q.only_reorder:
            items = [i for i in items if i.recommendation_type == "reorder_now"]
        if q.recommendation_type:
            items = [i for i in items if i.recommendation_type == q.recommendation_type]
        if q.only_high_priority:
            items = [i for i in items if i.priority == "high"]

        # Sort
        def get_val(i, key):
            if key == "stockout_probability": return i.stockout_probability or 0.0
            if key == "forecast_demand": return i.forecast_demand or 0.0
            return i.expected_profit_impact or 0.0
            
        items = sorted(items, key=lambda i: get_val(i, q.sort_by), reverse=True)
        
        if q.top_n:
            items = items[:q.top_n]
            
        return RecommendationListResponse(count=len(items), results=items)

    def store_recommendations(self, store_id: int) -> StoreRecommendationsResponse:
        # Re-use the patched logic
        items = self._get_patched_items(store_id)
        
        # Fetch finance data for insights (kept separate so one failure doesn't kill both)
        try:
            finance = self.get_finance_overview(store_id)
            customer_insights = finance['udhaar_stats']['customer_count']
        except Exception as e:
            logger.warning(f"Failed to fetch udhaar insights for store {store_id}: {e}")
            customer_insights = 0

        try:
            # from kirana.repository import KiranaRepository
            from kirana.repositories.main import KiranaRepository
            sales_insights = KiranaRepository(self._db).get_today_items_sold(store_id)
        except Exception as e:
            logger.warning(f"Failed to fetch sales insights for store {store_id}: {e}")
            sales_insights = 0

        summary = SnapshotSummary(
            store_id=store_id,
            total_skus=len({i.sku_id for i in items}),
            reorder_candidates=sum(1 for i in items if i.recommendation_type == "reorder_now"),
            high_risk_skus=sum(1 for i in items if i.recommendation_type == "stockout_risk"),
            fast_moving_skus=sum(1 for i in items if i.recommendation_type == "fast_moving"),
            profit_opportunities=sum(1 for i in items if i.recommendation_type == "profit_opportunity"),
            dead_stock_skus=sum(1 for i in items if i.recommendation_type == "dead_stock"),
            customer_insights=customer_insights,
            sales_insights=sales_insights,
        )
        return StoreRecommendationsResponse(summary=summary, recommendations=items)

    # ── Inventory Snapshots ───────────────────────────────────────────────────

    def ingest_store_snapshot(self, store_id: int, req: InventorySnapshotWriteRequest) -> InventorySnapshotWriteResponse:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        upserted = KiranaRepository(self._db).upsert_inventory_snapshot(
            store_id, req.snapshot_date, [i.model_dump() for i in req.items]
        )
        self.ml.refresh()
        return InventorySnapshotWriteResponse(
            store_id=store_id, snapshot_date=req.snapshot_date, upserted_count=upserted
        )

    def get_store_snapshot(self, store_id: int) -> StoreSnapshotResponse:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        snapshot = KiranaRepository(self._db).get_store_snapshot(store_id)
        return StoreSnapshotResponse(**snapshot)

    # ── AI Agents ─────────────────────────────────────────────────────────────

    def explain(self, req: ExplainRequest) -> ExplainResponse:
        q = RecommendationQueryRequest(
            store_id=req.store_id, sku_ids=req.sku_ids,
            recommendation_type=req.recommendation_type, top_n=req.top_n,
        )
        items = self.query_recommendations(q).results
        explanations = [
            self.explainer.explain(i.recommendation_type, {
                "sku_id": i.sku_id, "product_name": i.product_name, "category": i.category_name,
                "stockout_prob": i.stockout_probability, "reorder_qty": i.reorder_qty,
                "forecast_demand": i.forecast_demand, "current_stock": i.current_stock,
                "days_to_stockout": i.days_to_stockout, "current_price": i.current_price,
                "expected_profit": i.expected_profit_impact,
            })
            for i in items
        ]
        return ExplainResponse(count=len(explanations), explanations=explanations)

    def agent_query(self, req: AgentQueryRequest) -> AgentQueryResponse:
        intent, filters = interpret_query(req.query, store_id=req.store_id, top_n=req.top_n)
        result = self.query_recommendations(filters)
        explanations = [
            self.explainer.explain(i.recommendation_type, {
                "sku_id": i.sku_id, "product_name": i.product_name, "category": i.category_name,
                "stockout_prob": i.stockout_probability, "reorder_qty": i.reorder_qty,
                "forecast_demand": i.forecast_demand, "current_stock": i.current_stock,
                "days_to_stockout": i.days_to_stockout,
            })
            for i in result.results
        ]
        return AgentQueryResponse(
            intent=intent, filters=filters.model_dump(exclude_none=True),
            results=result.results, explanations=explanations,
        )

    def report_issue(self, user_id: int, store_id: int, req: IssueReportCreate) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        with repo._conn() as conn:
            sql = """
            INSERT INTO kirana_oltp.issue_report (user_id, store_id, category, title, description)
            VALUES (:uid, :sid, :cat, :t, :desc)
            RETURNING report_id
            """
            rid = conn.execute(text(sql), {
                "uid": user_id, "sid": store_id, "cat": req.category,
                "t": req.title, "desc": req.description
            }).scalar()
            conn.commit()
        return {"report_id": rid, "status": "submitted"}

    def update_fcm_token(self, user_id: int, fcm_token: str) -> bool:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        with repo._conn() as conn:
            # Update legacy single-token column for backward compat with intelligence engine
            conn.execute(text(
                "UPDATE kirana_oltp.users SET fcm_token = :tok WHERE user_id = :uid"
            ), {"tok": fcm_token, "uid": user_id})
            # Upsert into multi-device token table
            conn.execute(text("""
                INSERT INTO kirana_oltp.user_fcm_tokens (user_id, fcm_token, last_seen)
                VALUES (:uid, :tok, NOW())
                ON CONFLICT (fcm_token)
                DO UPDATE SET user_id = :uid, last_seen = NOW()
            """), {"tok": fcm_token, "uid": user_id})
            conn.commit()
        return True

    def send_fcm_to_user(self, user_id: int, title: str, body: str, data: dict | None = None) -> bool:
        """Send push notification to all registered devices for this user.
        Automatically removes stale/unregistered tokens from the DB.
        """
        import logging as _log
        _logger = _log.getLogger("kirana.fcm")
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        from kirana.fcm_sender import send_to_token, UNREGISTERED
        repo = KiranaRepository(self._db)
        with repo._conn() as conn:
            rows = conn.execute(
                text("SELECT fcm_token FROM kirana_oltp.user_fcm_tokens WHERE user_id = :uid ORDER BY last_seen DESC"),
                {"uid": user_id},
            ).mappings().all()
            tokens = [r["fcm_token"] for r in rows if r["fcm_token"]]

            if not tokens:
                row = conn.execute(
                    text("SELECT fcm_token FROM kirana_oltp.users WHERE user_id = :uid"),
                    {"uid": user_id},
                ).mappings().first()
                if row and row["fcm_token"]:
                    tokens = [row["fcm_token"]]

        if not tokens:
            _logger.warning("send_fcm_to_user: user_id=%s has no FCM token stored", user_id)
            return False

        _logger.info("send_fcm_to_user: sending to user_id=%s, %d device(s)", user_id, len(tokens))
        any_ok = False
        stale_tokens = []
        for token in tokens:
            result = send_to_token(token, title, body, data)
            if result is True:
                any_ok = True
            elif result == UNREGISTERED:
                stale_tokens.append(token)

        # Purge stale tokens so the intelligence engine stops hitting them
        if stale_tokens:
            # from kirana.repository import KiranaRepository
            from kirana.repositories.main import KiranaRepository
            repo2 = KiranaRepository(self._db)
            with repo2._conn() as conn:
                for t in stale_tokens:
                    conn.execute(text(
                        "DELETE FROM kirana_oltp.user_fcm_tokens WHERE fcm_token = :tok"
                    ), {"tok": t})
                    conn.execute(text(
                        "UPDATE kirana_oltp.users SET fcm_token = NULL WHERE fcm_token = :tok"
                    ), {"tok": t})
                conn.commit()
            _logger.info("send_fcm_to_user: purged %d stale token(s) for user_id=%s", len(stale_tokens), user_id)

        return any_ok

    def refresh_ml(self) -> dict:
        self.ml.refresh()
        return self.health()

    # ── Customer Segments ─────────────────────────────────────────────────────

    def list_customers_with_segments(self, store_id: int) -> list[dict]:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).list_customers_with_segments(store_id)

    # ── Subscription ──────────────────────────────────────────────────────────

    def get_active_subscription(self, store_id: int) -> dict | None:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_active_subscription(store_id)

    def request_trial(self, store_id: int, requested_tier: str = "basic") -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).request_trial(store_id, requested_tier)

    def approve_trial(self, store_id: int, trial_days: int) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).approve_trial(store_id, trial_days)

    def extend_trial(self, store_id: int, days: int) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).extend_trial(store_id, days)

    def get_admin_setting(self, key: str, default: str = "") -> str:
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_admin_setting(key, default)

    def set_admin_setting(self, key: str, value: str) -> None:
        from kirana.repositories.main import KiranaRepository
        KiranaRepository(self._db).set_admin_setting(key, value)

    def cancel_subscription(self, store_id: int) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).cancel_subscription(store_id)

    def upgrade_subscription(self, store_id: int, tier: str) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).upgrade_subscription(store_id, tier)

    def create_razorpay_order(self, store_id: int, tier: str) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        s = self._s
        return KiranaRepository(self._db).create_razorpay_order(
            store_id, tier, s.razorpay_key_id, s.razorpay_key_secret
        )

    def verify_razorpay_payment(self, store_id: int, tier: str,
                                 order_id: str, payment_id: str, signature: str) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        s = self._s
        return KiranaRepository(self._db).verify_razorpay_payment(
            store_id, tier, order_id, payment_id, signature, s.razorpay_key_secret
        )

    # ── User preferences ──────────────────────────────────────────────────────

    def get_user_prefs(self, user_id: int) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_user_prefs(user_id)

    def update_user_prefs(self, user_id: int, body) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        fields = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
        return KiranaRepository(self._db).upsert_user_prefs(user_id, **fields)

    # ── Finance ───────────────────────────────────────────────────────────────

    def get_finance_overview(self, store_id: int) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_finance_overview(store_id)

    def get_udhaar_list(self, store_id: int, include_recovered: bool = False) -> list[dict]:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_udhaar_list(store_id, include_recovered)

    def record_udhaar_recovery(self, store_id: int, khata_id: int, amount: float) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).record_udhaar_recovery(store_id, khata_id, amount)

    def add_udhaar(self, store_id: int, customer_name: str, phone: str, amount: float,
                   due_date: str | None = None) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).add_udhaar(store_id, customer_name, phone, amount, due_date)

    def sync_customers(self, store_id: int, contacts: list[dict]) -> int:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).sync_customers(store_id, contacts)

    def send_udhaar_reminder(self, store_id: int, khata_id: int, wa_client: Any) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        repo = KiranaRepository(self._db)
        
        # 1. Fetch record
        records = repo.get_udhaar_list(store_id, include_recovered=False)
        record = next((r for r in records if r["khata_id"] == khata_id), None)
        
        if not record:
            raise ValueError("Udhaar record not found or already recovered")
        
        phone = record.get("phone")
        if not phone:
            raise ValueError("Customer has no phone number")

        # Server-side throttle: at most one WhatsApp reminder per customer per day
        # (prevents repeated taps from spamming templates / burning WhatsApp quota).
        customer_id = record.get("customer_id")
        if customer_id is not None and repo.udhaar_reminded_today(store_id, int(customer_id)):
            raise ValueError("You've already reminded this customer today. Try again tomorrow.")

        # 2. Build payload
        store_name = repo.get_store(store_id).get("store_name", "Our Store")
        balance = record["balance"]
        days = record["days_pending"]

        payload = udhaar_reminder_payload(

            recipient=phone,
            lang="en",  # Defaulting to English, could be pulled from customer pref later
            customer_name=record['customer_name'],
            store_name=store_name,
            balance=f"{balance:,.2f}",
            days_pending=str(days)
        )
        
        # 3. Send via WhatsApp — prefer the approved template; if it isn't live
        #    yet (or Meta rejects it), fall back to a plain text reminder, which
        #    still delivers when the customer is inside the 24h service window.
        try:
            wa_client.send_template(payload)
            if customer_id is not None:
                repo.mark_udhaar_reminded(store_id, int(customer_id))
            return {"success": True, "phone": phone, "message": "Template message sent"}
        except Exception as template_err:
            logger.warning(
                "Udhaar reminder template send failed (%s); trying text fallback", template_err
            )
            try:
                fallback_text = (
                    f"Namaste {record['customer_name']}, this is a friendly reminder from "
                    f"{store_name}. Your pending balance is Rs.{balance} "
                    f"({days} days). Kindly clear it at your convenience. Thank you!"
                )
                wa_client.send_text(phone, fallback_text)
            except Exception as text_err:
                logger.error("Failed to send WhatsApp reminder: %s", text_err)
                raise ValueError(f"WhatsApp service error: {text_err}")
            if customer_id is not None:
                repo.mark_udhaar_reminded(store_id, int(customer_id))
            return {"success": True, "phone": phone, "message": "Text reminder sent"}


    def create_cashflow_request(self, store_id: int, user_id: int,
                                amount: float, selected_bank: str | None) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        result = KiranaRepository(self._db).create_cashflow_request(
            store_id, user_id, amount, selected_bank
        )
        return result

    def get_cashflow_status(self, store_id: int) -> dict:
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_cashflow_status(store_id)

    # ── Referral System ───────────────────────────────────────────────────────

    def create_referral_campaign(self, store_id, name, referral_discount_pct, milestone_every_n, milestone_reward_pct, max_referrals_per_referrer=50):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).create_referral_campaign(
            store_id, name, referral_discount_pct, milestone_every_n, milestone_reward_pct, max_referrals_per_referrer)

    def list_referral_campaigns(self, store_id):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).list_referral_campaigns(store_id)

    def toggle_referral_campaign(self, campaign_id, is_active, store_id=None):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).toggle_referral_campaign(
            campaign_id, is_active, store_id)

    def get_or_create_referral_token(self, store_id, customer_id, campaign_id):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_or_create_referral_token(store_id, customer_id, campaign_id)

    def get_token_info(self, token_hash):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_token_info(token_hash)

    def process_referral(self, token_hash, new_phone, new_name, order_id=None):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).process_referral(token_hash, new_phone, new_name, order_id)

    def get_pending_vouchers(self, customer_id, store_id):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).get_pending_vouchers(customer_id, store_id)

    def use_voucher(self, voucher_id, order_id=None):
        # from kirana.repository import KiranaRepository
        from kirana.repositories.main import KiranaRepository
        return KiranaRepository(self._db).use_voucher(voucher_id, order_id)
