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
    sku: Optional[str] = None
    barcode: Optional[str] = None
    is_perishable: bool = False
    is_loose: bool = False
    category_id: int
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
    quantity: int
    unit_price: Optional[float] = None
    selling_price: Optional[float] = None


class OrderCreate(BaseModel):
    items: List[OrderItemCreate]
    customer_id: Optional[int] = None
    total_amount: Optional[float] = None
    payment_method: str = "cash"


class OrderItemOut(BaseModel):
    order_item_id: int
    product_id: int
    product_name: Optional[str] = None
    quantity: int
    unit_price: float
    selling_price: Optional[float] = None
    cost_price: Optional[float] = None
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
