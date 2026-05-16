"""
Kirana Master Backend — single-DB (lit_db) edition.
All three modules share one SQLAlchemy engine pointed at lit_db.

Run:
  uvicorn main:app --host 0.0.0.0 --port 9000 --workers 2
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import get_settings

os.makedirs(os.path.join(_ROOT, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_ROOT, "logs", "master.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("master")


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
    logger.info("Kirana AI service bootstrapped — %d ML rows loaded", kirana_svc.ml.get_frame().shape[0])

    # Trigger schema bootstrap (adds auth columns, migrates legacy public tables,
    # seeds store defaults) before any request handler runs.
    from kirana.repository import KiranaRepository
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
    intelligence = IntelligenceEngine(engine)
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
    async def log_requests(request: Request, call_next):
        rid   = uuid.uuid4().hex[:8]
        start = time.time()
        request.state.request_id = rid
        response = await call_next(request)
        ms = int((time.time() - start) * 1000)
        logger.info("[%s] %s %s → %d  (%dms)", rid, request.method, request.url.path, response.status_code, ms)
        return response

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        logger.error("Bad Request: %s", exc)
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})

    @app.exception_handler(PermissionError)
    async def perm_error(request: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"success": False, "error": str(exc)})

    @app.exception_handler(Exception)
    async def generic_error(request: Request, exc: Exception):
        logger.exception("Unhandled: %s", exc)
        return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error"})

    # ── Routers ───────────────────────────────────────────────────────────────
    from kirana.routes   import router as kirana_router
    from pos.routes      import router as pos_router
    from oltp.routes     import router as oltp_router
    from whatsapp.routes import router as wa_router
    from kpis.routes     import router as kpi_router

    app.include_router(kirana_router)
    app.include_router(pos_router)
    app.include_router(oltp_router)
    app.include_router(wa_router)
    app.include_router(kpi_router)

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
    )
