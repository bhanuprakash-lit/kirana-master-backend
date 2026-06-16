"""Udhaar voice-consent clip storage — Azure Blob.

Unlike vision images (local disk SEAM), consent clips are a financial/legal
record and MUST survive container redeploys, so they go to durable Azure Blob
Storage. The container is private; clips are read back through a Bearer-authed
backend proxy (see routes: /kirana/finance/udhaar/consent/audio/{blob}), never a
public blob URL.

Configure via env:
  AZURE_STORAGE_CONNECTION_STRING   (empty disables consent upload → 503)
  CONSENT_AUDIO_CONTAINER           (default "udhaar-consent")
"""
from __future__ import annotations

import threading
import uuid

from config import get_settings

# Lazily-initialised, process-wide singletons so we don't pay container-client
# setup on every request and don't import azure at module load when unconfigured.
_lock = threading.Lock()
_container_client = None  # type: ignore
_ready = False


def is_configured() -> bool:
    return bool(get_settings().azure_storage_connection_string)


def _client():
    """Return a ready ContainerClient, creating the container once if needed.
    Raises RuntimeError when Azure isn't configured (caller maps to 503)."""
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
        cc = svc.get_container_client(s.consent_audio_container)
        try:
            cc.create_container()
        except Exception:
            pass  # already exists (or no create perms — assume pre-provisioned)
        _container_client = cc
        _ready = True
        return cc


def _ext_for(content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "aac" in ct or "mp4" in ct or "m4a" in ct:
        return ".aac"
    if "wav" in ct:
        return ".wav"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "ogg" in ct or "opus" in ct:
        return ".ogg"
    return ".aac"


def upload_consent_audio(
    store_id: int, order_id: int | None, data: bytes, content_type: str | None
) -> str:
    """Upload a consent clip and return its blob name (stored in DB).

    Layout: {store_id}/{order_id or 'manual'}/{uuid}{ext}
    """
    cc = _client()
    ext = _ext_for(content_type)
    folder = str(order_id) if order_id is not None else "manual"
    blob_name = f"{store_id}/{folder}/{uuid.uuid4().hex}{ext}"
    cc.upload_blob(
        name=blob_name,
        data=data,
        overwrite=True,
        content_type=content_type or "audio/aac",
    )
    return blob_name


def download_consent_audio(blob_name: str) -> tuple[bytes, str]:
    """Fetch a clip's bytes + content-type for the authed proxy endpoint."""
    cc = _client()
    bc = cc.get_blob_client(blob_name)
    stream = bc.download_blob()
    props = stream.properties
    content_type = (
        getattr(props, "content_settings", None)
        and props.content_settings.content_type
    ) or "audio/aac"
    return stream.readall(), content_type
