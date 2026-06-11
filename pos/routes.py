"""
POS routes — mounted at /pos in the master app.
Uses kirana_oltp schema (lit_db). Auth via kirana_app_users (JWT).
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from pos import crud, schemas
from pos.auth import create_access_token, decode_token
from pos.models import KiranaStore

router = APIRouter(prefix="/pos", tags=["POS"])

_oauth2 = OAuth2PasswordBearer(tokenUrl="/pos/token")


# ── DB + auth dependency helpers ─────────────────────────────────────────────

def _db(request: Request) -> Session:
    return request.app.state.db_session()


def _get_session_factory(request: Request):
    return request.app.state.db_session


def _current_user(
    request: Request,
    token: str = Depends(_oauth2),
) -> dict:
    """Decode JWT → look up user in kirana_app_users."""
    payload = decode_token(token)
    username = payload.get("sub")
    from kirana.repository import KiranaRepository
    repo = KiranaRepository(request.app.state.engine)
    user = repo.get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _require_admin(user: dict = Depends(_current_user)):
    if user.get("role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin/owner access required")
    return user


def _resolve_store_scope(current: dict, requested_store_id: Optional[int] = None) -> Optional[int]:
    if current.get("role") == "admin":
        return requested_store_id
    user_store_id = current.get("store_id")
    if user_store_id is None:
        raise HTTPException(status_code=403, detail="No store assigned to this user")
    if requested_store_id is not None and requested_store_id != user_store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return user_store_id


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/token", response_model=schemas.Token, summary="POS login — returns JWT")
def pos_login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    from kirana.repository import KiranaRepository
    repo = KiranaRepository(request.app.state.engine)
    user = repo.authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": user["username"], "store_id": user.get("store_id")})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/token-from-kirana", response_model=schemas.Token, summary="Exchange Kirana Bearer token for POS JWT")
def pos_token_from_kirana(request: Request):
    """Phone-auth users have no password — they exchange their Kirana Bearer token for a POS JWT."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Kirana Bearer token required")
    kirana_token = auth[len("Bearer "):]
    from kirana.repository import KiranaRepository
    user = KiranaRepository(request.app.state.engine).get_user_by_token(kirana_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired Kirana token")
    token = create_access_token({"sub": user["username"], "store_id": user.get("store_id")})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=dict, summary="Current POS user info")
def pos_me(current: dict = Depends(_current_user)):
    return current


# ── Stores ────────────────────────────────────────────────────────────────────

@router.get("/stores", response_model=List[schemas.StoreOut], summary="List all kirana stores")
def list_stores(request: Request, current: dict = Depends(_current_user)):
    with request.app.state.db_session() as db:
        store_id = _resolve_store_scope(current)
        if store_id is None:
            return crud.get_stores(db)
        store = crud.get_store(db, store_id)
        return [store] if store else []


@router.get("/stores/{store_id}", response_model=schemas.StoreOut, summary="Get a specific store")
def get_store(request: Request, store_id: int, current: dict = Depends(_current_user)):
    store_id = _resolve_store_scope(current, store_id)
    with request.app.state.db_session() as db:
        s = crud.get_store(db, store_id)
    if not s:
        raise HTTPException(status_code=404, detail="Store not found")
    return s


# ── Categories ────────────────────────────────────────────────────────────────

@router.get("/categories", response_model=List[schemas.CategoryOut], summary="List all product categories")
def list_categories(request: Request, current: dict = Depends(_current_user)):
    with request.app.state.db_session() as db:
        return crud.get_categories(db)


# ── Products ──────────────────────────────────────────────────────────────────

@router.get("/products", response_model=List[schemas.ProductOut],
            summary="List products with current price and stock for a store")
@router.get("/products/", response_model=List[schemas.ProductOut], include_in_schema=False)
def list_products(
    request: Request,
    store_id: int = 1,
    skip: int = 0,
    limit: int = 100,
    current: dict = Depends(_current_user),
):
    effective_store = _resolve_store_scope(current, store_id)
    with request.app.state.db_session() as db:
        return crud.get_products(db, effective_store, skip, limit)


@router.get("/products/barcode/{barcode}", response_model=schemas.ProductOut,
            summary="Look up a product by barcode")
def product_by_barcode(
    request: Request,
    barcode: str,
    store_id: int = 1,
    current: dict = Depends(_current_user),
):
    effective_store = _resolve_store_scope(current, store_id)
    with request.app.state.db_session() as db:
        p = crud.get_product_by_barcode(db, barcode, effective_store)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@router.get("/products/{product_id}", response_model=schemas.ProductOut,
            summary="Get a single product with price and stock")
def get_product(
    request: Request,
    product_id: int,
    store_id: int = 1,
    current: dict = Depends(_current_user),
):
    effective_store = _resolve_store_scope(current, store_id)
    with request.app.state.db_session() as db:
        p = crud.get_product(db, product_id, effective_store)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


# ── Orders ────────────────────────────────────────────────────────────────────

@router.post("/orders", response_model=schemas.OrderOut, status_code=201,
             summary="Create a new POS order (deducts stock automatically)")
@router.post("/orders/", response_model=schemas.OrderOut, status_code=201, include_in_schema=False)
def create_order(
    request: Request,
    order: schemas.OrderCreate,
    background_tasks: BackgroundTasks,
    current: dict = Depends(_current_user),
):
    store_id = current.get("store_id")
    if not store_id:
        raise HTTPException(status_code=403, detail="No store assigned to this user")
    with request.app.state.db_session() as db:
        new_order = crud.create_order(db, order, current["user_id"], store_id)
        from kirana.repository import KiranaRepository
        background_tasks.add_task(KiranaRepository(request.app.state.engine).compute_store_footfall, store_id)
        return new_order


@router.get("/orders", response_model=List[schemas.OrderOut],
            summary="List orders for the current user's store")
@router.get("/orders/", response_model=List[schemas.OrderOut], include_in_schema=False)
def list_orders(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    payment_method: Optional[str] = None,
    customer_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    current: dict = Depends(_current_user),
):
    store_id = current.get("store_id")
    if not store_id:
        raise HTTPException(status_code=403, detail="No store assigned")
    with request.app.state.db_session() as db:
        return crud.get_orders(
            db, 
            store_id, 
            skip, 
            limit,
            status=status,
            payment_method=payment_method,
            customer_id=customer_id,
            start_date=start_date,
            end_date=end_date,
            min_amount=min_amount,
            max_amount=max_amount,
        )


@router.get("/orders/{order_id}", response_model=schemas.OrderOut,
            summary="Get a single order")
def get_order(
    request: Request,
    order_id: int,
    current: dict = Depends(_current_user),
):
    with request.app.state.db_session() as db:
        o = crud.get_order(db, order_id)
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    _resolve_store_scope(current, o.store_id)
    return o


# ── Payments ──────────────────────────────────────────────────────────────────

@router.post("/payments", response_model=schemas.PaymentOut, status_code=201,
             summary="Record a payment for an order")
@router.post("/payments/", response_model=schemas.PaymentOut, status_code=201, include_in_schema=False)
def create_payment(
    request: Request,
    payment: schemas.PaymentCreate,
    current: dict = Depends(_current_user),
):
    with request.app.state.db_session() as db:
        o = crud.get_order(db, payment.order_id)
        if not o:
            raise HTTPException(status_code=404, detail="Order not found")
        _resolve_store_scope(current, o.store_id)
        return crud.create_payment(db, payment)


# ── Reports ───────────────────────────────────────────────────────────────────

@router.get("/reports/daily-sales", response_model=schemas.DailySalesReport,
            summary="Daily revenue summary for the store (defaults to today IST)")
@router.get("/reports/daily-sales/", response_model=schemas.DailySalesReport, include_in_schema=False)
def daily_sales(
    request: Request,
    date: Optional[str] = None,
    store_id: Optional[int] = None,
    current: dict = Depends(_current_user),
):
    effective_store = _resolve_store_scope(current, store_id)
    if date:
        try:
            query_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    else:
        query_date = datetime.utcnow()

    with request.app.state.db_session() as db:
        return crud.get_daily_sales(db, query_date, effective_store)
