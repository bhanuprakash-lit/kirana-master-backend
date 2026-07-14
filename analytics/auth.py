"""Auth for the director analytics dashboard.

A single dependency, ``require_director``, that accepts a dedicated read-only
``DIRECTOR_TOKEN`` via any of:

  - ``?token=...``           query param  (so a bookmarked link just works)
  - ``X-Director-Token``     header       (used by the dashboard's fetch calls)
  - ``X-API-Key`` / Bearer   admin key    (so existing admins can view it too)

Fails **closed**: if ``DIRECTOR_TOKEN`` is unset/empty, every request is denied —
a misconfigured deploy can never expose data behind a blank token. Comparisons
use ``hmac.compare_digest`` to avoid timing leaks. Mirrors the token style of
``kirana/routers/admin.py::_auth``.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request


def _matches(candidate: str, secret: str) -> bool:
    return bool(candidate) and bool(secret) and hmac.compare_digest(candidate, secret)


def require_director(request: Request) -> dict:
    s = request.app.state.settings
    director_token = getattr(s, "director_token", "") or ""
    admin_key = getattr(s, "kirana_api_key", "") or ""

    # Director token (query param first — that's the shared-link path — then header).
    token = request.query_params.get("token", "") or request.headers.get(
        "X-Director-Token", ""
    )
    if _matches(token, director_token):
        return {"role": "director"}

    # Admin key (header or Bearer) also authorizes.
    api_key = request.headers.get("X-API-Key", "")
    auth_hdr = request.headers.get("Authorization", "")
    bearer = auth_hdr[len("Bearer ") :] if auth_hdr.startswith("Bearer ") else ""
    if _matches(api_key, admin_key) or _matches(bearer, admin_key):
        return {"role": "admin"}

    raise HTTPException(status_code=401, detail="Unauthorized")
