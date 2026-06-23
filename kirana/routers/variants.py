import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from kirana.service import KiranaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kirana", tags=["Kirana AI"])


def _svc(request: Request) -> KiranaService:
    return request.app.state.kirana_service


def _auth(request: Request):
    s = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer ") :] if auth_hdr.startswith("Bearer ") else ""

    if api_key and api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = _svc(request).user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Unauthorized")


def _repo(request: Request):
    from kirana.repositories.main import KiranaRepository

    return KiranaRepository(request.app.state.engine)


# ── Variant axes / attribute defs ─────────────────────────────────────────────


@router.get("/attribute-defs")
async def attribute_defs(request: Request, user: dict = Depends(_auth)):
    """The variant axes + attributes the caller's vertical exposes (F2)."""
    repo = _repo(request)
    vc = repo.get_vertical_config(user.get("store_id") or 0).get("vertical_code", "grocery")
    return {"vertical_code": vc, "attributes": repo.list_attribute_defs(vc)}


# ── Product variants ──────────────────────────────────────────────────────────


@router.get("/products/{product_id}/variants")
async def list_variants(product_id: int, request: Request, user: dict = Depends(_auth)):
    include_inactive = request.query_params.get("include_inactive") == "true"
    return {
        "variants": _repo(request).list_variants(
            product_id, include_inactive=include_inactive
        )
    }


@router.post("/products/{product_id}/variants")
async def create_variant(product_id: int, request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    return _repo(request).create_variant(
        product_id,
        attributes=body.get("attributes") or {},
        sku=body.get("sku"),
        barcode=body.get("barcode"),
        price=body.get("price"),
        mrp=body.get("mrp"),
        cost=body.get("cost"),
        stock=body.get("stock") or 0,
    )


@router.patch("/variants/{variant_id}")
async def update_variant(variant_id: int, request: Request, user: dict = Depends(_auth)):
    body = await request.json()
    updated = _repo(request).update_variant(
        variant_id,
        attributes=body.get("attributes"),
        sku=body.get("sku"),
        barcode=body.get("barcode"),
        price=body.get("price"),
        mrp=body.get("mrp"),
        cost=body.get("cost"),
        stock=body.get("stock"),
        is_active=body.get("is_active"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Variant not found")
    return updated


@router.delete("/variants/{variant_id}")
async def deactivate_variant(variant_id: int, request: Request, user: dict = Depends(_auth)):
    if not _repo(request).deactivate_variant(variant_id):
        raise HTTPException(
            status_code=404,
            detail="Variant not found, or cannot deactivate the implicit variant",
        )
    return {"deactivated": True}
