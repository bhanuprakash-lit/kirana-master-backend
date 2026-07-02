"""Bulk stock-in shelf-photo storage — Azure Blob.

The daily shelf-scan images use a local-disk seam (storage.py). Onboarding photos
are different: they're the raw (image → detected products) training data that grows
the model, and they're captured once at store setup, so they MUST survive container
redeploys → durable Azure Blob (same infra as udhaar consent clips).

Configure via env:
  AZURE_STORAGE_CONNECTION_STRING   (empty disables onboarding upload → 503)
  ONBOARDING_SHELF_CONTAINER        (default "onboarding-shelf")
"""
from __future__ import annotations

import threading
import uuid
from datetime import date

from config import get_settings

_lock = threading.Lock()
_container_client = None  # type: ignore
_ready = False


def is_configured() -> bool:
    return bool(get_settings().azure_storage_connection_string)


def _client():
    global _container_client, _ready
    if _ready and _container_client is not None:
        return _container_client
    with _lock:
        if _ready and _container_client is not None:
            return _container_client
        s = get_settings()
        if not s.azure_storage_connection_string:
            raise RuntimeError("Azure Blob not configured (AZURE_STORAGE_CONNECTION_STRING)")
        from azure.storage.blob import BlobServiceClient  # local import: optional dep
        svc = BlobServiceClient.from_connection_string(s.azure_storage_connection_string)
        cc = svc.get_container_client(s.onboarding_shelf_container)
        try:
            cc.create_container()
        except Exception:
            pass  # already exists / pre-provisioned
        _container_client = cc
        _ready = True
        return cc


def _ext_for(content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    return ".jpg"


def upload_shelf_image(store_id: int, data: bytes, content_type: str | None) -> str:
    """Upload one onboarding shelf photo and return its blob name (stored in DB).

    Layout: {store_id}/{YYYY-MM-DD}/{uuid}{ext}
    """
    cc = _client()
    ext = _ext_for(content_type)
    blob_name = f"{store_id}/{date.today().isoformat()}/{uuid.uuid4().hex}{ext}"
    cc.upload_blob(
        name=blob_name,
        data=data,
        overwrite=True,
        content_type=content_type or "image/jpeg",
    )
    return blob_name


def download_shelf_image(blob_name: str) -> tuple[bytes, str]:
    """Fetch a photo's bytes + content-type for the authed proxy endpoint."""
    cc = _client()
    bc = cc.get_blob_client(blob_name)
    stream = bc.download_blob()
    props = stream.properties
    content_type = (
        getattr(props, "content_settings", None)
        and props.content_settings.content_type
    ) or "image/jpeg"
    return stream.readall(), content_type
