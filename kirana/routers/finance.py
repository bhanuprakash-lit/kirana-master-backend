import logging
from typing import Optional, List, TYPE_CHECKING
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

from kirana.schemas import (
    UdhaarAddRequest,
    UdhaarRecoveryRequest,
    UdhaarRemindRequest,
    CustomerSyncRequest,
    CustomerSyncItem,
    CashflowRequestCreate,
    PaymentOrderRequest,
    PaymentVerifyRequest,
)
from kirana.service import KiranaService

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


def _require_admin(user: dict = Depends(_auth)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _require_store(store_id: int, user: dict = Depends(_auth)):
    if user.get("role") == "admin":
        return user
    if user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return user


# ── Finance ───────────────────────────────────────────────────────────────────


@router.get("/finance/overview")
async def get_finance_overview(request: Request, user: dict = Depends(_auth)):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).get_finance_overview(int(sid))


@router.get("/finance/udhaar")
async def get_udhaar_list(
    request: Request, include_recovered: bool = False, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).get_udhaar_list(int(sid), include_recovered)


@router.post("/finance/udhaar/recovery")
async def record_recovery(
    request: Request, body: UdhaarRecoveryRequest, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).record_udhaar_recovery(int(sid), body.khata_id, body.amount)


@router.get("/finance/udhaar/{khata_id}/history")
async def get_udhaar_history(
    khata_id: int, request: Request, user: dict = Depends(_auth)
):
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = request.headers.get("X-Store-Id") or (user.get("store_id") or 0)
    repo = KiranaRepository(request.app.state.engine)
    payments = repo.get_khata_payments(int(sid), khata_id)
    return {"payments": payments}


@router.post("/finance/udhaar/add")
async def add_udhaar(
    request: Request, body: UdhaarAddRequest, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return _svc(request).add_udhaar(
        int(sid), body.customer_name, body.phone, body.amount, body.due_date
    )


# ── Udhaar voice consent (Pro; durable Azure Blob; in-house model analysis) ───


@router.post("/finance/udhaar/consent")
async def upload_udhaar_consent(
    request: Request,
    audio: UploadFile = File(...),
    order_id: Optional[int] = Form(None),
    khata_id: Optional[int] = Form(None),
    customer_id: Optional[int] = Form(None),
    agreed_total: Optional[float] = Form(None),
    agreed_udhaar: Optional[float] = Form(None),
    promised_date: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    duration_sec: Optional[float] = Form(None),
    user: dict = Depends(_auth),
):
    """Receive a customer's voice-consent clip for an udhaar order. The clip
    persists to Azure Blob (durable legal record) and a 'pending' row is created;
    the in-house voice model later fills the analysis + speaker-match score.
    The mobile app uploads this from a persistent background queue, so the owner
    is never blocked — there is no synchronous AI work here."""
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")

    from consent import storage as consent_storage

    if not consent_storage.is_configured():
        raise HTTPException(status_code=503, detail="Consent storage not configured")

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio clip")
    try:
        blob = consent_storage.upload_consent_audio(
            int(sid), order_id, data, audio.content_type
        )
    except (RuntimeError, ImportError):
        # Not configured, or the azure-storage-blob package isn't installed yet.
        # Return 503 (not 500) so the client's persistent queue keeps the clip
        # and retries once the dependency/env is in place — no clips are lost.
        raise HTTPException(status_code=503, detail="Consent storage unavailable")

    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    rec = repo.create_udhaar_consent(
        store_id=int(sid),
        audio_blob=blob,
        order_id=order_id,
        khata_id=khata_id,
        customer_id=customer_id,
        duration_sec=duration_sec,
        language=language,
        agreed_total=agreed_total,
        agreed_udhaar=agreed_udhaar,
        promised_date=promised_date,
    )
    # TODO(voice-model): enqueue the in-house analysis job here (consent extraction
    # + speaker match). It writes udhaar_consent.analysis / voice_match_score and
    # flips status → 'analyzed'. Until that model ships, the row stays 'pending'.
    return rec


@router.get("/finance/udhaar/consent/audio/{blob:path}")
async def get_udhaar_consent_audio(
    blob: str, request: Request, user: dict = Depends(_auth)
):
    """Authed proxy that streams a consent clip from the private blob container."""
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    from consent import storage as consent_storage

    try:
        data, ctype = consent_storage.download_consent_audio(blob)
    except Exception:
        raise HTTPException(status_code=404, detail="Consent clip not found")
    return Response(content=data, media_type=ctype)


@router.get("/finance/udhaar/consent/{order_id}")
async def get_udhaar_consent(
    order_id: int, request: Request, user: dict = Depends(_auth)
):
    """Consent record + analysis for an order (order-details screen)."""
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    repo = KiranaRepository(request.app.state.engine)
    rec = repo.get_consent_for_order(int(sid), order_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No consent recorded")
    return rec


@router.get("/finance/udhaar/smart")
async def smart_udhaar(request: Request, user: dict = Depends(_auth)):
    """Open udhaar ranked by recovery risk, with a suggested action per entry."""
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository

    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    return {
        "udhaar": KiranaRepository(request.app.state.engine).get_smart_udhaar(int(sid))
    }


# ── Payments ──────────────────────────────────────────────────────────────────


@router.post("/payment/create-order")
async def create_payment_order(
    request: Request, body: PaymentOrderRequest, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    s = request.app.state.settings
    prices = _svc(request).get_segment_prices(int(sid))
    if body.tier not in prices:
        raise HTTPException(status_code=400, detail="Invalid tier")
    # If Razorpay keys not configured, return test-mode placeholder
    if not s.razorpay_key_id or not s.razorpay_key_secret:
        return {
            "mode": "test",
            "order_id": f"test_order_{body.tier}",
            "amount": int(prices[body.tier] * 100),
            "currency": "INR",
            "key_id": "",
            "tier": body.tier,
        }
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        order = await loop.run_in_executor(
            None, lambda: _svc(request).create_razorpay_order(int(sid), body.tier)
        )
        return {**order, "mode": "live"}
    except Exception:
        logger.exception(
            "Razorpay order creation failed for store %s tier %s", sid, body.tier
        )
        raise HTTPException(status_code=400, detail="Failed to create payment order")


@router.post("/payment/mock-confirm")
async def mock_confirm_payment(
    request: Request, body: PaymentOrderRequest, user: dict = Depends(_auth)
):
    """Directly upgrades subscription — only for test/dev mode. Blocked in production."""
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    s = request.app.state.settings
    # Block mock-confirm when Google Play credentials are configured (live mode)
    if s.google_play_credentials_json and s.google_play_package_name:
        raise HTTPException(
            status_code=403, detail="Mock payments disabled in live mode"
        )
    try:
        result = _svc(request).upgrade_subscription(int(sid), body.tier)
        user_id = user.get("user_id")
        if user_id:
            tier_name = "Pro" if body.tier == "pro" else "Basic"
            _svc(request).send_fcm_to_user(
                user_id,
                f"Welcome to Kirana AI {tier_name}!",
                f"Your {tier_name} plan is now active. Enjoy!",
                data={"action": "open_subscription", "channel": "kirana_account"},
            )
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Payment confirmation failed")


@router.post("/payment/verify-iap")
async def verify_iap_payment(request: Request, user: dict = Depends(_auth)):
    """Verify a Google Play IAP purchase and activate the subscription.

    Optional server-side verification with Google Play Developer API when
    GOOGLE_PLAY_CREDENTIALS_JSON is set in .env. Without credentials, the
    purchase token is trusted (acceptable for testing; add credentials before
    going live).
    """
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")

    body = await request.json()
    tier = body.get("tier", "")
    product_id = body.get("product_id", "")
    purchase_token = body.get("purchase_token", "")

    if tier not in ("basic", "pro"):
        raise HTTPException(status_code=400, detail="Invalid tier")
    if not purchase_token:
        raise HTTPException(status_code=400, detail="purchase_token required")

    s = request.app.state.settings

    # Optional: verify with Google Play Developer API
    if s.google_play_credentials_json and s.google_play_package_name:
        try:
            import json as _json
            from google.oauth2 import service_account as _sa
            from googleapiclient.discovery import build as _build

            creds_path = s.google_play_credentials_json
            if not creds_path.startswith("{"):
                with open(creds_path) as f:
                    creds_data = _json.load(f)
            else:
                creds_data = _json.loads(creds_path)

            creds = _sa.Credentials.from_service_account_info(
                creds_data,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )
            service = _build(
                "androidpublisher", "v3", credentials=creds, cache_discovery=False
            )
            result = (
                service.purchases()
                .subscriptions()
                .get(
                    packageName=s.google_play_package_name,
                    subscriptionId=product_id,
                    token=purchase_token,
                )
                .execute()
            )

            # paymentState: 1=received, 2=free trial, 0=pending
            if result.get("paymentState", 0) not in (1, 2):
                raise HTTPException(
                    status_code=402, detail="Payment not yet confirmed by Google Play"
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Play verification error: {exc}"
            ) from exc

    # Activate subscription
    result = _svc(request).upgrade_subscription(int(sid), tier)

    user_id = user.get("user_id")
    if user_id:
        tier_name = "Pro" if tier == "pro" else "Basic"
        try:
            _svc(request).send_fcm_to_user(
                user_id,
                f"Welcome to Kirana AI {tier_name}!",
                f"Your {tier_name} plan is now active. Enjoy all the features!",
                data={"action": "open_subscription", "channel": "kirana_account"},
            )
        except Exception:
            pass

    return result


@router.post("/payment/verify")
async def verify_payment(
    request: Request, body: PaymentVerifyRequest, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    s = request.app.state.settings
    if not s.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")
    try:
        result = _svc(request).verify_razorpay_payment(
            int(sid),
            body.tier,
            body.razorpay_order_id,
            body.razorpay_payment_id,
            body.razorpay_signature,
        )
        # Send FCM confirmation
        user_id = user.get("user_id")
        if user_id:
            tier_name = "Pro" if body.tier == "pro" else "Basic"
            _svc(request).send_fcm_to_user(
                user_id,
                f"Welcome to Kirana AI {tier_name}!",
                f"Your {tier_name} subscription is now active. Enjoy all features!",
                data={"action": "open_subscription", "channel": "kirana_account"},
            )
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Payment verification failed")


@router.get("/customers")
async def list_customers_segments(
    request: Request, store_id: int, user: dict = Depends(_auth)
):
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"customers": _svc(request).list_customers_with_segments(store_id)}


@router.post("/finance/customers/sync")
async def sync_customers(
    request: Request,
    body: List[CustomerSyncItem] | CustomerSyncRequest,
    user: dict = Depends(_auth),
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")

    # Handle both bare list and wrapped object for backward compatibility
    contacts = body.contacts if isinstance(body, CustomerSyncRequest) else body
    count = _svc(request).sync_customers(int(sid), [c.model_dump() for c in contacts])
    return {"synced": count}


@router.post("/finance/udhaar/remind")
async def remind_udhaar(
    request: Request, body: UdhaarRemindRequest, user: dict = Depends(_auth)
):
    sid = user.get("store_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Store owner login required")
    wa_client = request.app.state.wa_client
    try:
        return _svc(request).send_udhaar_reminder(int(sid), body.khata_id, wa_client)
    except ValueError as e:
        # Bad request / cannot deliver (no phone, not found, WhatsApp rejected) —
        # surface a clean message instead of a 500 so the app can show it.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(
            "Udhaar reminder failed for store=%s khata=%s", sid, body.khata_id
        )
        raise HTTPException(status_code=502, detail=f"Could not send reminder: {e}")


# ── Cashflow Support ──────────────────────────────────────────────────────────


@router.post("/cashflow/request")
async def create_cashflow_request(
    request: Request,
    body: CashflowRequestCreate,
    user: dict = Depends(_auth),
):
    user_id = user.get("user_id")
    store_id = body.store_id
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    result = _svc(request).create_cashflow_request(
        store_id=store_id,
        user_id=user_id,
        amount=body.amount_requested,
        selected_bank=body.selected_bank,
    )
    return {
        "request_id": result["request_id"],
        "status": result["status"],
        "message": "We've received your request! Our team will contact you within 2 business days.",
    }


@router.get("/cashflow/status")
async def get_cashflow_status(
    request: Request,
    store_id: int,
    user: dict = Depends(_auth),
):
    if user.get("role") != "admin" and user.get("store_id") != store_id:
        raise HTTPException(status_code=403, detail="Access denied to this store")
    return _svc(request).get_cashflow_status(store_id)
