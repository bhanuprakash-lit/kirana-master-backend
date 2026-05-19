"""
Firebase Cloud Messaging (FCM) push notification sender.

Set FIREBASE_CREDENTIALS_JSON to the path of your Firebase service account JSON
(absolute path OR relative to the backend root directory).

Gracefully no-ops if credentials are not configured.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("kirana.fcm")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_initialized = False
_messaging = None


def _ensure_init() -> bool:
    global _initialized, _messaging
    if _initialized:
        return _messaging is not None

    _initialized = True
    raw = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
    if not raw:
        logger.info("FIREBASE_CREDENTIALS_JSON not set — FCM push disabled")
        return False

    creds_path = raw if os.path.isabs(raw) else os.path.join(_ROOT, raw)
    logger.info("FCM: looking for credentials at %s", creds_path)

    if not os.path.isfile(creds_path):
        logger.warning("FCM: credentials file not found at %s — FCM push disabled", creds_path)
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging as fb_messaging

        if not firebase_admin._apps:
            cred = credentials.Certificate(creds_path)
            firebase_admin.initialize_app(cred)

        _messaging = fb_messaging
        logger.info("FCM: Firebase Admin SDK initialized successfully")
        return True
    except Exception as exc:
        logger.warning("FCM: Firebase Admin SDK init failed: %s", exc)
        return False


UNREGISTERED = "UNREGISTERED"  # sentinel returned when token is stale/invalid


def send_to_token(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool | str:
    """Send a push notification.

    Returns:
      True         — sent successfully
      False        — transient failure (network, server error)
      UNREGISTERED — token is stale (device uninstalled app); caller should delete it
    """
    if not fcm_token or not _ensure_init():
        return False
    try:
        msg = _messaging.Message(
            notification=_messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=_messaging.AndroidConfig(
                priority="high",
                notification=_messaging.AndroidNotification(
                    channel_id="kirana_ai_high",
                    priority="high",
                ),
            ),
            apns=_messaging.APNSConfig(
                payload=_messaging.APNSPayload(
                    aps=_messaging.Aps(sound="default", badge=1),
                ),
            ),
        )
        response = _messaging.send(msg)
        logger.info("FCM: push sent → message_id=%s token=...%s", response, fcm_token[-8:])
        return True
    except Exception as exc:
        exc_name = type(exc).__name__
        # UnregisteredError / SenderIdMismatch mean the token is permanently invalid
        if "Unregistered" in exc_name or "SenderIdMismatch" in exc_name or "NOT_FOUND" in str(exc):
            logger.warning("FCM: stale token removed (token=...%s): %s", fcm_token[-8:], exc)
            return UNREGISTERED
        logger.warning("FCM: send failed (token=...%s): %s", fcm_token[-8:] if fcm_token else "?", exc, exc_info=True)
        return False
