from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from kirana.service import KiranaService
from oltp.repository import OltpRepository

router = APIRouter(prefix="/oltp", tags=["OLTP"])


class RecordUpdateRequest(BaseModel):
    keys: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


def _repo(request: Request) -> OltpRepository:
    return OltpRepository(request.app.state.engine)


def _auth(request: Request) -> dict:
    svc: KiranaService = request.app.state.kirana_service
    s = request.app.state.settings

    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else ""

    if api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = svc.user_by_token(bearer)
        if user:
            return user
    raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _query_filters(request: Request) -> dict[str, str]:
    reserved = {"limit", "offset"}
    return {k: v for k, v in request.query_params.items() if k not in reserved}


def _key_filters(request: Request) -> dict[str, str]:
    return dict(request.query_params.items())


@router.get("/schema", summary="List schema metadata for all kirana_oltp tables")
async def schema_overview(request: Request, user: dict = Depends(_auth)):
    repo = _repo(request)
    return {"schema": "kirana_oltp", "tables": repo.schema_overview()}


@router.get("/schema/{table_name}", summary="Get schema metadata for one kirana_oltp table")
async def schema_for_table(table_name: str, request: Request, user: dict = Depends(_auth)):
    repo = _repo(request)
    return {"schema": "kirana_oltp", "table": repo.schema_for(table_name)}


@router.get("/{table_name}", summary="List rows from a kirana_oltp table")
async def list_table_rows(
    table_name: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(_auth),
):
    repo = _repo(request)
    return repo.list_rows(table_name, user, _query_filters(request), limit, offset)


@router.get("/{table_name}/record", summary="Get a single row by primary key")
async def get_table_row(table_name: str, request: Request, user: dict = Depends(_auth)):
    repo = _repo(request)
    return repo.get_row(table_name, user, _key_filters(request))


@router.post("/{table_name}", summary="Create a row in a kirana_oltp table")
async def create_table_row(
    table_name: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: dict = Depends(_auth),
):
    repo = _repo(request)
    return repo.create_row(table_name, user, payload)


@router.patch("/{table_name}", summary="Update a row by query parameter keys")
async def update_table_row_direct(
    table_name: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: dict = Depends(_auth),
):
    repo = _repo(request)
    return repo.update_row(table_name, user, _key_filters(request), payload)


@router.delete("/{table_name}", summary="Delete a row by query parameter keys")
async def delete_table_row_direct(table_name: str, request: Request, user: dict = Depends(_auth)):
    repo = _repo(request)
    return repo.delete_row(table_name, user, _key_filters(request))


@router.patch("/{table_name}/record", summary="Update a row using a structured keys/data body")
async def update_table_row(
    table_name: str,
    request: Request,
    body: RecordUpdateRequest,
    user: dict = Depends(_auth),
):
    repo = _repo(request)
    return repo.update_row(table_name, user, body.keys, body.data)


@router.delete("/{table_name}/record", summary="Delete a row in a kirana_oltp table")
async def delete_table_row(table_name: str, request: Request, user: dict = Depends(_auth)):
    repo = _repo(request)
    return repo.delete_row(table_name, user, _key_filters(request))
