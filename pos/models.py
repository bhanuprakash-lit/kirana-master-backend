"""
POS models — map directly to the kirana_oltp schema already in lit_db.
Auth users live in the public schema (kirana_app_users created by KiranaRepository).
"""
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime,
    Integer, Numeric, String, ForeignKey
)
from sqlalchemy.orm import DeclarativeBase, relationship


class POSBase(DeclarativeBase):
    pass


# ── kirana_oltp tables ────────────────────────────────────────────────────────

class KiranaStore(POSBase):
    __tablename__ = "store"
    __table_args__ = {"schema": "kirana_oltp"}

    store_id   = Column(BigInteger, primary_key=True)
    name       = Column(String)
    location   = Column(String)
    region     = Column(String)
    created_at = Column(DateTime)
    is_deleted = Column(Boolean, default=False)


class KiranaCategory(POSBase):
    __tablename__ = "category"
    __table_args__ = {"schema": "kirana_oltp"}

    category_id        = Column(BigInteger, primary_key=True)
    parent_category_id = Column(BigInteger)
    name               = Column(String, nullable=False)

    products = relationship("KiranaProduct", back_populates="category")


class KiranaProduct(POSBase):
    __tablename__ = "product"
    __table_args__ = {"schema": "kirana_oltp"}

    product_id    = Column(BigInteger, primary_key=True)
    category_id   = Column(BigInteger, ForeignKey("kirana_oltp.category.category_id"))
    name          = Column(String, nullable=False)
    brand         = Column(String)
    unit          = Column(String)
    weight        = Column(Numeric)
    is_loose      = Column(Boolean, default=False)
    is_perishable = Column(Boolean, default=False)
    sku           = Column(String)
    barcode       = Column(String)
    image_url     = Column(String)
    hsn_code      = Column(String)   # F3 — GST HSN/SAC code
    gst_rate      = Column(Numeric)  # F3 — per-product GST %
    created_at    = Column(DateTime)

    category    = relationship("KiranaCategory", back_populates="products")
    pricing     = relationship("KiranaPricing",  back_populates="product")
    order_items = relationship("KiranaOrderItem", back_populates="product")


class KiranaPricing(POSBase):
    __tablename__ = "pricing"
    __table_args__ = {"schema": "kirana_oltp"}

    pricing_id = Column(BigInteger, primary_key=True)
    product_id = Column(BigInteger, ForeignKey("kirana_oltp.product.product_id"))
    store_id   = Column(BigInteger)
    # price      = Column(Numeric)
    selling_price = Column("price", Numeric)
    mrp        = Column(Numeric)
    valid_from = Column(DateTime, nullable=False)
    valid_to   = Column(DateTime)

    product = relationship("KiranaProduct", back_populates="pricing")


class KiranaInventory(POSBase):
    __tablename__ = "inventory"
    __table_args__ = {"schema": "kirana_oltp"}

    inventory_id = Column(BigInteger, primary_key=True)
    store_id     = Column(BigInteger)
    product_id   = Column(BigInteger)
    quantity     = Column(Integer, default=0)


class KiranaOrder(POSBase):
    __tablename__ = "orders"
    __table_args__ = {"schema": "kirana_oltp"}

    order_id      = Column(BigInteger, primary_key=True)
    store_id      = Column(BigInteger)
    user_id       = Column(BigInteger)
    customer_id   = Column(BigInteger)
    order_status  = Column(String, default="completed")
    order_date    = Column(DateTime, default=datetime.utcnow)
    total_amount  = Column(Numeric, default=0)
    # Split / partial-udhaar support: how much was credited vs paid in cash.
    # Both are NULL for pure-cash or full-udhaar orders.
    udhaar_amount = Column(Numeric, nullable=True)
    cash_paid     = Column(Numeric, nullable=True)
    # Basket attribution snapshot — set only when the sale came from a basket
    # bundle. NULL for ordinary orders. Frozen at sale time so history is stable.
    basket_id      = Column(BigInteger, nullable=True)
    basket_name    = Column(String, nullable=True)
    basket_gross   = Column(Numeric, nullable=True)
    basket_savings = Column(Numeric, nullable=True)
    tax_amount     = Column(Numeric, nullable=True)   # F3 — total GST in the bill
    taxable_amount = Column(Numeric, nullable=True)   # F3 — total minus tax

    items   = relationship("KiranaOrderItem", back_populates="order")
    payment = relationship("KiranaPayment",   back_populates="order", uselist=False)


class KiranaOrderItem(POSBase):
    __tablename__ = "order_item"
    __table_args__ = {"schema": "kirana_oltp"}

    order_item_id = Column(BigInteger, primary_key=True)
    order_id      = Column(BigInteger, ForeignKey("kirana_oltp.orders.order_id"))
    product_id    = Column(BigInteger, ForeignKey("kirana_oltp.product.product_id"))
    variant_id    = Column(BigInteger, nullable=True)  # F2 — which variant sold
    quantity      = Column(Numeric)
    unit_price    = Column(Numeric)
    cost_price    = Column(Numeric)
    gst_rate      = Column(Numeric, nullable=True)   # F3 — GST % applied
    tax_amount    = Column(Numeric, nullable=True)   # F3 — tax in this line (incl.)

    order   = relationship("KiranaOrder",   back_populates="items")
    product = relationship("KiranaProduct", back_populates="order_items")


class KiranaPayment(POSBase):
    __tablename__ = "payments"
    __table_args__ = {"schema": "kirana_oltp"}

    payment_id     = Column(BigInteger, primary_key=True)
    order_id       = Column(BigInteger, ForeignKey("kirana_oltp.orders.order_id"))
    amount         = Column(Numeric)
    payment_method = Column(String)
    status         = Column(String, default="paid")
    created_at     = Column(DateTime, default=datetime.utcnow)

    order = relationship("KiranaOrder", back_populates="payment")
