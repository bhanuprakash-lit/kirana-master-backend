from typing import Any, Optional, List
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    user_id: int
    username: str
    full_name: str
    role: str
    store_id: Optional[int] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser


class PhoneLoginRequest(BaseModel):
    phone_number: str
    firebase_uid: str  # client-side verified; backend trusts mobile app


class CashflowRequestCreate(BaseModel):
    store_id: int
    amount_requested: float
    selected_bank: Optional[str] = None


class CashflowRequestResponse(BaseModel):
    request_id: int
    status: str
    message: str


class RegisterStoreOwnerRequest(BaseModel):
    username: str
    password: str = ""          # empty for phone-auth users
    full_name: str
    store_name: str
    store_type: str = "kirana"
    footfall: int = 40
    location: Optional[str] = None
    region: Optional[str] = None
    email: Optional[str] = None          # store owner's contact email
    phone_number: Optional[str] = None   # set for phone-auth registrations
    firebase_uid: Optional[str] = None   # for audit trail
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class RegisterStoreOwnerResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser
    store: dict


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None


class UserCreateRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    store_id: Optional[int] = None


class UserCreateResponse(BaseModel):
    user_id: int
    username: str
    full_name: str
    role: str
    store_id: Optional[int] = None


class StoreUpdateRequest(BaseModel):
    store_name: Optional[str] = None
    store_type: Optional[str] = None
    footfall: Optional[int] = None
    budget: Optional[float] = None
    daily_budget: Optional[float] = None
    location: Optional[str] = None
    region: Optional[str] = None


class RecommendationItem(BaseModel):
    store_id: int
    sku_id: int
    product_name: str = ""
    category_name: str = ""
    recommendation_type: str
    priority: str = "medium"
    stockout_probability: Optional[float] = None
    prob_stockout_3d: Optional[float] = None
    prob_stockout_7d: Optional[float] = None
    prob_stockout_30d: Optional[float] = None
    reorder_qty: Optional[float] = None
    forecast_demand: Optional[float] = None
    current_stock: Optional[float] = None
    days_to_stockout: Optional[float] = None
    current_price: Optional[float] = None
    optimal_price: Optional[float] = None
    price_change_pct: Optional[float] = None
    expected_profit_impact: Optional[float] = None
    effective_margin: Optional[float] = None
    reorder_point: Optional[float] = None
    message: str = ""


class RecommendationQueryRequest(BaseModel):
    store_id: Optional[int] = None
    sku_ids: Optional[List[int]] = None
    top_n: Optional[int] = None
    only_reorder: bool = False
    only_high_priority: bool = False
    recommendation_type: Optional[str] = None
    sort_by: str = "expected_profit"


class RecommendationListResponse(BaseModel):
    count: int
    results: List[RecommendationItem]


class SnapshotSummary(BaseModel):
    store_id: int
    total_skus: int
    reorder_candidates: int
    high_risk_skus: int
    fast_moving_skus: int
    profit_opportunities: int
    dead_stock_skus: int = 0
    customer_insights: int = 0
    sales_insights: int = 0


class StoreRecommendationsResponse(BaseModel):
    summary: SnapshotSummary
    recommendations: List[RecommendationItem]


class InventorySnapshotWriteItem(BaseModel):
    sku_id: int
    units_sold: Optional[float] = None
    stock: Optional[float] = None
    revenue: Optional[float] = None
    profit: Optional[float] = None
    price: Optional[float] = None
    promo_flag: Optional[bool] = None


class InventorySnapshotWriteRequest(BaseModel):
    snapshot_date: str
    items: List[InventorySnapshotWriteItem]


class InventorySnapshotWriteResponse(BaseModel):
    store_id: int
    snapshot_date: str
    upserted_count: int


class InventorySnapshotReadItem(BaseModel):
    sku_id: int
    snapshot_date: str
    units_sold: Optional[float] = None
    stock: Optional[float] = None
    lost_sales: Optional[float] = None
    revenue: Optional[float] = None
    profit: Optional[float] = None
    price: Optional[float] = None
    promo_flag: Optional[int] = None
    category: Optional[str] = None
    product_name: Optional[str] = None


class StoreSnapshotResponse(BaseModel):
    store_id: int
    snapshot_count: int
    snapshot_date: Optional[str] = None
    items: List[InventorySnapshotReadItem]


class ExplainRequest(BaseModel):
    store_id: Optional[int] = None
    sku_ids: Optional[List[int]] = None
    recommendation_type: Optional[str] = None
    top_n: int = 5


class ExplainResponse(BaseModel):
    count: int
    explanations: List[str]


class AgentQueryRequest(BaseModel):
    query: str
    store_id: Optional[int] = None
    top_n: int = 5


class AgentQueryResponse(BaseModel):
    intent: str
    filters: dict
    results: List[RecommendationItem]
    explanations: List[str]


class UserPrefs(BaseModel):
    forecast_horizon_days: int = 7
    alert_stockout_threshold: float = 0.5
    alert_min_velocity: float = 0.3
    alert_reorder_days: int = 3
    alert_dead_stock_days: int = 21
    notify_whatsapp: bool = False
    notify_in_app: bool = True
    quiet_hours_start: int = 22
    quiet_hours_end: int = 7
    allow_social_marketing: bool = False
    alert_expiry_days: int = 7


class UserPrefsUpdate(BaseModel):
    forecast_horizon_days: Optional[int] = None
    alert_stockout_threshold: Optional[float] = None
    alert_min_velocity: Optional[float] = None
    alert_reorder_days: Optional[int] = None
    alert_dead_stock_days: Optional[int] = None
    notify_whatsapp: Optional[bool] = None
    notify_in_app: Optional[bool] = None
    quiet_hours_start: Optional[int] = None
    quiet_hours_end: Optional[int] = None
    allow_social_marketing: Optional[bool] = None
    alert_expiry_days: Optional[int] = None
    subscribed_kpis: Optional[str] = None  # comma-separated KPI IDs


class SubscriptionUpgradeRequest(BaseModel):
    tier: str  # 'basic' | 'pro'


class PaymentOrderRequest(BaseModel):
    tier: str  # 'basic' | 'pro'

class PaymentVerifyRequest(BaseModel):
    tier: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ── Finance ───────────────────────────────────────────────────────────────────

class FinanceSalesStats(BaseModel):
    amount: float
    sku_count: int


class FinanceUdhaarStats(BaseModel):
    total_pending: float
    total_recovered: float
    customer_count: int


class FinanceOverviewResponse(BaseModel):
    today_sales: FinanceSalesStats
    udhaar_stats: FinanceUdhaarStats


class UdhaarRecord(BaseModel):
    khata_id: int
    customer_id: int
    customer_name: str
    phone: Optional[str] = None
    balance: float
    date_taken: str
    days_pending: int


class UdhaarRecoveryRequest(BaseModel):
    khata_id: int
    amount: float


class UdhaarAddRequest(BaseModel):
    customer_name: str
    phone: str
    amount: float


class UdhaarRemindRequest(BaseModel):
    khata_id: int


class CustomerSyncItem(BaseModel):
    name: str
    phone: str


class CustomerSyncRequest(BaseModel):
    contacts: List[CustomerSyncItem]


class IssueReportCreate(BaseModel):
    category: str
    title: str
    description: str


class FcmTokenUpdate(BaseModel):
    fcm_token: str


class ReferralCampaignCreate(BaseModel):
    store_id: int
    name: str
    referral_discount_pct: float = 10.0
    milestone_every_n: int = 10
    milestone_reward_pct: float = 5.0
    max_referrals_per_referrer: int = 50


class ReferralTokenRequest(BaseModel):
    store_id: int
    customer_id: int
    campaign_id: int


class ReferralScanRequest(BaseModel):
    token_hash: str
    new_customer_phone: str
    new_customer_name: str = ""
    order_id: Optional[int] = None


class VoucherUseRequest(BaseModel):
    voucher_id: int
    order_id: Optional[int] = None
