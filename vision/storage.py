"""Shelf-image storage.

P0: writes to a local directory on the backend host and returns a relative URL
served by FastAPI's existing /static mount-style handler (see routes: /kirana/vision
exposes the file). This is a SEAM — before any cloud deploy, swap save_image() for
an Azure Blob upload (container disk is ephemeral). The rest of the code only depends
on the returned URL string, so the swap is local to this file.

Images are retained on purpose: they're the (crop, label) training data for the
future self-hosted model (see BUILD_YOUR_OWN_MODEL.md in vision-ai).
"""
from __future__ import annotations

import os
import uuid
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend root
_VISION_DIR = os.path.join(_ROOT, "data", "vision_sessions")


def save_image(store_id: int, session_type: str, data: bytes, content_type: str | None) -> tuple[str, str]:
    """Persist a shelf image. Returns (absolute_path, public_url).

    public_url is a backend-relative path the app can GET (see routes).
    """
    ext = _ext_for(content_type)
    day = date.today().isoformat()
    sub = os.path.join(_VISION_DIR, str(store_id), day)
    os.makedirs(sub, exist_ok=True)
    fname = f"{session_type}_{uuid.uuid4().hex[:10]}{ext}"
    abs_path = os.path.join(sub, fname)
    with open(abs_path, "wb") as f:
        f.write(data)
    rel = os.path.relpath(abs_path, _VISION_DIR).replace(os.sep, "/")
    return abs_path, f"/kirana/vision/image/{rel}"


def resolve_url(rel_url: str) -> str | None:
    """Map a public_url produced by save_image back to an absolute file path,
    guarding against path traversal. Returns None if outside the vision dir."""
    prefix = "/kirana/vision/image/"
    if not rel_url.startswith(prefix):
        return None
    rel = rel_url[len(prefix):]
    abs_path = os.path.normpath(os.path.join(_VISION_DIR, rel))
    base = os.path.normpath(_VISION_DIR)
    if not abs_path.startswith(base + os.sep):
        return None
    return abs_path if os.path.exists(abs_path) else None


def _ext_for(content_type: str | None) -> str:
    if not content_type:
        return ".jpg"
    ct = content_type.lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    return ".jpg"
