from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = None


# ── Store ──────────────────────────────────────────────────────────────────────

class StoreOut(BaseModel):
    store_id: int
    name: str
    location: Optional[str] = None
    region: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# ── Category ───────────────────────────────────────────────────────────────────

class CategoryOut(BaseModel):
    category_id: int
    name: str
    parent_category_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


# ── Product ────────────────────────────────────────────────────────────────────

class ProductOut(BaseModel):
    product_id: int
    name: str
    brand: Optional[str] = None
    unit: Optional[str] = None
    weight: Optional[float] = None
    sku: Optional[str] = None
    barcode: Optional[str] = None
    is_perishable: bool = False
    is_loose: bool = False
    category_id: int
    image_url: Optional[str] = None
    hsn_code: Optional[str] = None       # F3 — GST HSN/SAC
    gst_rate: Optional[float] = None     # F3 — per-product GST %
    warranty_months: Optional[int] = None  # tester #11 — per-product warranty
    # joined from pricing
    price: Optional[float] = None
    mrp: Optional[float] = None
    # joined from inventory
    stock_quantity: Optional[int] = None
    expiry_date: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# ── Order ──────────────────────────────────────────────────────────────────────

class OrderItemCreate(BaseModel):
    product_id: int
    variant_id: Optional[int] = None  # F2 — chosen variant (None = implicit/grocery)
    quantity: float
    unit_price: Optional[float] = None
    selling_price: Optional[float] = None


class SerialItemCreate(BaseModel):
    """Tester #4 — a serial/IMEI captured per cart line so it links to the
    specific phone it was billed against (not a flat list)."""
    serial_no: str
    product_id: int
    variant_id: Optional[int] = None


class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    customer_id: Optional[int] = None
    total_amount: Optional[float] = None
    payment_method: str = "cash"
    # Split / partial-udhaar: cash collected now + amount put on credit.
    # Only set when payment_method == "udhaar" and it's a partial split.
    udhaar_amount: Optional[float] = None
    cash_paid: Optional[float] = None
    # Repayment deadline for the auto-created khata (ISO yyyy-mm-dd). Only used
    # for udhaar orders; defaults to issue + 30 days when omitted.
    due_date: Optional[str] = None
    # Basket attribution — set only when the cart was filled from a basket bundle.
    basket_id: Optional[int] = None
    basket_name: Optional[str] = None
    basket_gross: Optional[float] = None
    basket_savings: Optional[float] = None
    # M1 — loyalty/offers applied at checkout (recorded against the order).
    coupon_id: Optional[int] = None
    coupon_discount: Optional[float] = None
    redeem_points: Optional[float] = None
    # POS deep-links (best-effort, never fail the sale):
    #   M7 serials sold on this bill, M4 membership session used + appointment
    #   billed, M9 job card billed.
    serials: Optional[List[str]] = None
    # Tester #4 — per-line serials (preferred over the flat `serials` list); each
    # links to its product/variant for warranty + receipt.
    serial_items: Optional[List[SerialItemCreate]] = None
    membership_id: Optional[int] = None
    appointment_id: Optional[int] = None
    job_card_id: Optional[int] = None


class OrderItemOut(BaseModel):
    order_item_id: int
    product_id: int
    product_name: Optional[str] = None
    quantity: float
    unit_price: float
    selling_price: Optional[float] = None
    cost_price: Optional[float] = None
    variant_id: Optional[int] = None     # F2
    gst_rate: Optional[float] = None      # F3
    tax_amount: Optional[float] = None    # F3
    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    order_id: int
    store_id: int
    user_id: int
    order_status: str
    order_date: datetime
    total_amount: float
    items: List[OrderItemOut] = []
    payment_method: Optional[str] = None
    customer_id: Optional[int] = None
    # Split / partial-udhaar breakdown (null for pure cash or full-udhaar orders)
    udhaar_amount: Optional[float] = None
    cash_paid: Optional[float] = None
    # Basket attribution (null unless the sale came from a basket bundle)
    basket_id: Optional[int] = None
    basket_name: Optional[str] = None
    basket_gross: Optional[float] = None
    basket_savings: Optional[float] = None
    # F3 — GST breakup (null when no taxable items)
    tax_amount: Optional[float] = None
    taxable_amount: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


# ── Payment ────────────────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    order_id: int
    amount: float
    payment_method: str   # cash | upi | card | credit


class PaymentOut(BaseModel):
    payment_id: int
    order_id: int
    amount: float
    payment_method: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Reports ────────────────────────────────────────────────────────────────────

class DailySalesReport(BaseModel):
    date: datetime
    store_id: Optional[int] = None
    total_sales: float
    total_orders: int
    avg_order_value: float
