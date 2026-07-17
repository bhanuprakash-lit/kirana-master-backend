"""
Kirana Master Backend — single-DB (lit_db) edition.
All three modules share one SQLAlchemy engine pointed at lit_db.

Run:
  uvicorn main:app --host 0.0.0.0 --port 9000 --workers 2
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import get_settings
from log_config import JsonFormatter, log_memory, request_id_var

# ── Logging bootstrap ──────────────────────────────────────────────────────────
_ENV = os.getenv("ENVIRONMENT", "production").lower()  # set ENVIRONMENT=local for dev

_json_handler = logging.StreamHandler(sys.stdout)
_json_handler.setFormatter(JsonFormatter())
_handlers: list[logging.Handler] = [_json_handler, log_memory]

# Local dev only: rotating plain-text file for grep comfort.
# Never used in Azure — containers have ephemeral disks; stdout is captured.
if _ENV in ("local", "development", "dev"):
    os.makedirs(os.path.join(_ROOT, "logs"), exist_ok=True)
    _fh = logging.handlers.RotatingFileHandler(
        os.path.join(_ROOT, "logs", "master.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
    _handlers.append(_fh)

logging.basicConfig(level=logging.INFO, handlers=_handlers, force=True)

# Silence high-volume noise with no analytical value.
for _noisy in (
    "apscheduler.executors.default",
    "apscheduler.scheduler",
    "urllib3", "httpx", "httpcore",
    "azure.core", "azure.identity",
    "multipart",
):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

logger = logging.getLogger("master")

# Accept a client-supplied correlation ID only if it's a sane token — otherwise
# generate our own. Prevents log/memory bloat and reflected-value abuse from a
# malicious or buggy client (the ID is logged on every line and echoed back).
_CID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logger.info("Starting Kirana Master Backend on %s:%d", s.host, s.port)

    # ── Single engine → lit_db ────────────────────────────────────────────────
    engine = create_engine(
        s.db_url,
        pool_size=15, max_overflow=30,
        pool_pre_ping=True,
    )
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("Database connected: %s", s.db_url)

    SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def db_session():
        db = SessionFactory()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Kirana AI service ─────────────────────────────────────────────────────
    from kirana.service import KiranaService
    kirana_svc = KiranaService(db_conn=engine, settings=s)
    kirana_svc.bootstrap()
    # NB: do not call get_frame() here — it would trigger the deferred ML load
    # at startup and reintroduce the boot-time OOM this deferral fixes.
    logger.info("Kirana AI service bootstrapped (ML frames load on first use)")

    # Trigger schema bootstrap (adds auth columns, migrates legacy public tables,
    # seeds store defaults) before any request handler runs.
    # from kirana.repository import KiranaRepository
    from kirana.repositories.main import KiranaRepository
    KiranaRepository(engine)
    logger.info("kirana_oltp schema bootstrapped")

    # ── FCM push notifications ────────────────────────────────────────────────
    from kirana.fcm_sender import _ensure_init as _fcm_init
    _fcm_init()

    # ── WhatsApp services ─────────────────────────────────────────────────────
    from whatsapp.client import WhatsAppClient
    from whatsapp.session_store import WhatsAppSessionStore
    from whatsapp.intelligence import WhatsAppIntelligence
    from whatsapp.conversation_handler import ConversationHandler

    wa_client  = WhatsAppClient(
        access_token=s.whatsapp_access_token,
        phone_number_id=s.whatsapp_phone_number_id,
        base_url=s.whatsapp_api_base_url,
    )
    wa_sessions = WhatsAppSessionStore(engine)       # sessions live in lit_db public schema
    wa_intel    = WhatsAppIntelligence(s.mistral_api_key, s.mistral_model)
    wa_handler  = ConversationHandler(
        wa_client=wa_client,
        session_store=wa_sessions,
        intelligence=wa_intel,
        pos_db=engine,           # same engine — ConversationHandler uses pos.crud directly
        kirana_service=kirana_svc,
    )

    # ── Intelligence engine (scheduled notifications) ─────────────────────────
    from kirana.intelligence.engine import IntelligenceEngine
    intelligence = IntelligenceEngine(engine, kirana_svc=kirana_svc)
    intelligence.start()

    # ── Attach to app state ───────────────────────────────────────────────────
    app.state.settings       = s
    app.state.engine         = engine
    app.state.db_session     = db_session
    app.state.kirana_service = kirana_svc
    app.state.wa_client      = wa_client
    app.state.wa_sessions    = wa_sessions
    app.state.wa_handler     = wa_handler
    app.state.intelligence   = intelligence

    logger.info("All services ready — http://%s:%d", s.host, s.port)
    yield

    intelligence.stop()
    engine.dispose()
    from ai.routes import close_gemini_client
    await close_gemini_client()
    logger.info("Master backend shut down cleanly")


# ── App factory ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Kirana Master Backend",
        description=(
            "Unified backend for **Kirana AI recommendations**, **POS system**, "
            "and **WhatsApp intelligence layer**. All backed by a single `lit_db` PostgreSQL database."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        # Honour a correlation ID from the client (Flutter, WhatsApp webhook,
        # admin panel) so the full call chain shares one traceable ID — but only
        # if it passes validation. Fall back to a generated ID otherwise.
        incoming = request.headers.get("X-Correlation-ID", "")
        rid   = incoming if _CID_RE.match(incoming) else uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        ms = round((time.perf_counter() - start) * 1000)
        # Echo the ID back so clients can correlate their own logs with ours
        response.headers["X-Correlation-ID"] = rid
        # Skip health-check noise — /health is polled by Azure every few seconds
        if request.url.path not in ("/health", "/kirana/health"):
            logger.info(
                "%s %s → %d (%dms)",
                request.method, request.url.path, response.status_code, ms,
                extra={
                    "http_method": request.method,
                    "http_path":   request.url.path,
                    "http_status": response.status_code,
                    "duration_ms": ms,
                },
            )
        return response

    # Error responses are produced by Starlette's outermost handler, OUTSIDE the
    # CORS middleware — so without this they'd lack Access-Control-Allow-Origin
    # and a browser would report a 4xx/5xx as a misleading "CORS error". The
    # admin panel authenticates with an X-API-Key header (no cookies), so a
    # wildcard origin is safe here.
    _CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        logger.warning("Bad request on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid request"}, headers=_CORS_HEADERS)

    @app.exception_handler(PermissionError)
    async def perm_error(request: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"success": False, "error": str(exc)}, headers=_CORS_HEADERS)

    @app.exception_handler(ClientDisconnect)
    async def client_disconnect(request: Request, exc: ClientDisconnect):
        # Client closed the TCP socket before the body finished arriving.
        # Routine on mobile lifecycle endpoints (/tracking/app-event). No client
        # is listening for the response, so just close quietly.
        logger.debug("Client disconnected mid-request on %s", request.url.path)
        return Response(status_code=499)  # nginx-style "client closed request"

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        """Turn any DB constraint violation into a clean 4xx instead of a 500.
        A bad reference (FK), a duplicate (unique), or a missing required field is a
        client problem, not a server crash — surface it as such and log at WARNING
        (no scary traceback). Endpoints should still validate up-front for a precise
        message; this is the backstop so a missed check never 500s."""
        pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
        if pgcode == "23503":      # foreign_key_violation
            status, msg = 400, "References a record that doesn't exist."
        elif pgcode == "23505":    # unique_violation
            status, msg = 409, "That record already exists."
        elif pgcode == "23502":    # not_null_violation
            status, msg = 400, "A required field is missing."
        else:
            status, msg = 409, "The request conflicts with existing data."
        logger.warning("Integrity error (%s) on %s: %s",
                       pgcode, request.url.path, str(getattr(exc, "orig", exc))[:200])
        return JSONResponse(status_code=status, content={"success": False, "error": msg},
                            headers=_CORS_HEADERS)

    @app.exception_handler(Exception)
    async def generic_error(request: Request, exc: Exception):
        logger.exception("Unhandled: %s", exc)
        return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error"}, headers=_CORS_HEADERS)

    # ── Routers ───────────────────────────────────────────────────────────────
    # from kirana.routes   import router as kirana_router
    from kirana.routers.main import router as kirana_router
    from pos.routes      import router as pos_router
    from oltp.routes     import router as oltp_router
    from whatsapp.routes import router as wa_router
    from kpis.routes                  import router as kpi_router
    from ai.routes                    import router as ai_router
    from vision.routes                import router as vision_router
    from callcenter.routes            import router as callcenter_router
    from kirana.forecasting.routes    import router as forecast_router
    from analytics.routes             import router as analytics_router

    app.include_router(kirana_router)
    app.include_router(pos_router)
    app.include_router(oltp_router)
    app.include_router(wa_router)
    app.include_router(kpi_router)
    app.include_router(ai_router)
    app.include_router(vision_router)
    app.include_router(callcenter_router)
    app.include_router(forecast_router)
    app.include_router(analytics_router)

    # ── Root ──────────────────────────────────────────────────────────────────
    @app.get("/", tags=["Root"], include_in_schema=False)
    async def root():
        return {
            "service": "Kirana Master Backend",
            "version": "1.0.0",
            "database": "lit_db (single)",
            "modules": {"kirana_ai": "/kirana", "pos": "/pos", "oltp": "/oltp", "whatsapp": "/whatsapp", "kpis": "/kirana/kpis"},
            "docs":       "/docs",
            "dashboard":  "/ui",
        }

    @app.get("/health", tags=["Root"])
    async def health(request: Request):
        svc = request.app.state.kirana_service
        return {
            "status": "ok",
            "kirana": svc.health(),
            "pos": "connected (kirana_oltp schema)",
            "whatsapp": {
                "phone_id_set":  bool(request.app.state.wa_client.phone_number_id),
                "send_enabled":  request.app.state.wa_client.is_configured,
                "config_error":  request.app.state.wa_client.config_error,
                "mistral_set":   bool(request.app.state.settings.mistral_api_key),
            },
        }

    # ── UI dashboard ──────────────────────────────────────────────────────────
    @app.get("/ui", tags=["Root"], include_in_schema=False, response_class=HTMLResponse)
    async def ui():
        path = os.path.join(_ROOT, "static", "dashboard.html")
        with open(path, encoding="utf-8") as f:
            return f.read()

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run(
        "main:app",
        host=s.host,
        port=s.port,
        reload=s.debug,
        workers=1 if s.debug else 2,
        log_level="info",
        # Our middleware already logs method/path/status/duration as JSON with a
        # correlation ID — uvicorn's plain access log would duplicate that and
        # break the single-format stdout that Azure Log Analytics parses.
        access_log=False,
    )
