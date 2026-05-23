# Kirana Master Backend — Developer Guide

> Audience: any engineer joining the **kirana-master-backend** FastAPI service.
> This document explains how the codebase is laid out, how authentication
> works across the modules, how the database is bootstrapped, how the ML
> recommendations are wired in, and how to add a new endpoint without
> breaking existing contracts.
>
> The HTTP contract (every endpoint, every payload) is documented separately
> in [`../API_REFERENCE.md`](../API_REFERENCE.md). This guide is about
> **the codebase itself** — not what it serves.

---

## 1. What this service is

A single FastAPI application that powers the entire Kirana AI platform:

- Authentication (custom Bearer tokens + a separate POS JWT)
- AI-driven recommendations (stockout risk, reorder, fast-moving, dead-stock, profit opportunities)
- POS billing and inventory
- Generic CRUD over the OLTP tables for the mobile app
- 24+ production KPIs with ML inference on top
- WhatsApp Business intelligence layer (Meta webhook + Mistral NLU)
- Scheduled push notifications via FCM
- A small admin panel UI (Vite + Tailwind + Chart.js)

Everything runs against a single PostgreSQL database, `lit_db`, primarily in
the `kirana_oltp` schema.

---

## 2. Tech stack

| Concern | Library | Notes |
| --- | --- | --- |
| Web framework | `fastapi` >= 0.115 | App factory in `main.py:create_app()` |
| ASGI server | `uvicorn[standard]` | `--workers 2` in production, `--reload` in debug |
| ORM / SQL | `sqlalchemy` >= 2.0 | Used both as ORM (POS module) and via raw `text()` (most modules) |
| DB driver | `psycopg2-binary` | PostgreSQL only |
| Config | `pydantic` >= 2.7, `python-dotenv` | Plain dataclass `Settings`, not pydantic-settings — see `config.py` |
| Auth | `python-jose[cryptography]`, `passlib[bcrypt]` | POS JWTs only; Kirana tokens are server-issued opaque hex strings |
| ML inference | `pandas`, `numpy`, `joblib`, `xgboost`, `scikit-learn`, `imbalanced-learn` | Loaded once at startup, hot-reloaded every 6 h |
| AI | `mistralai` (NLU for WhatsApp + query agent), Gemini via REST (`httpx`) | Gemini key never leaves the server |
| Push | `firebase-admin` >= 6.5 | Service-account JSON at `serviceAccountKey.json` (gitignored) |
| HTTP | `httpx`, `requests` | `httpx.AsyncClient` is reused for Gemini |
| Scheduling | `apscheduler` 3.x | `AsyncIOScheduler` lifecycle-managed by the FastAPI lifespan |

Dependency list is authoritative in [`../requirements.txt`](../requirements.txt).

---

## 3. Repository layout

```
kirana-master-backend/
├── main.py                  App factory, lifespan, middleware, router mounts
├── config.py                Settings dataclass + .env loader
├── requirements.txt
├── serviceAccountKey.json   Firebase service account (GITIGNORED)
├── .env / .env.example      Server secrets (GITIGNORED)
│
├── kirana/                  Auth + AI + finance + subscription  (/kirana)
│   ├── routes.py            FastAPI router — every /kirana/* endpoint
│   ├── service.py           KiranaService — orchestrates ml_adapter + repo + agents
│   ├── repository.py        KiranaRepository — PostgreSQL queries + schema bootstrap
│   ├── schemas.py           All Pydantic request/response models for the module
│   ├── ml_adapter.py        ML CSV loader → RecommendationItem builder
│   ├── fcm_sender.py        Firebase Cloud Messaging wrapper (idempotent init)
│   ├── campaigns.py         Daily-basket campaign engine (time/area-based)
│   ├── agents/
│   │   ├── mistral_explainer.py    Mistral-powered natural-language explanations
│   │   └── query_agent.py          Intent + filter extraction
│   └── intelligence/
│       ├── engine.py        APScheduler lifecycle + job registration
│       ├── repository.py    Helper queries used by the scheduler
│       └── triggers.py      Individual trigger functions (low_stock, expiry, …)
│
├── pos/                     POS module  (/pos)
│   ├── routes.py            Orders, products, payments, daily-sales report
│   ├── crud.py              SQLAlchemy CRUD helpers
│   ├── models.py            Declarative ORM models on the kirana_oltp schema
│   ├── schemas.py           Pydantic schemas
│   └── auth.py              JWT encode/decode for the POS token
│
├── oltp/                    Generic CRUD  (/oltp)
│   ├── routes.py            HTTP layer + table allowlist + RBAC
│   └── repository.py        Dynamic query builder over the SQLAlchemy reflect metadata
│
├── kpis/                    KPI module  (/kirana/kpis)
│   ├── routes.py            27 KPI endpoints + registry + tier config
│   ├── calculator.py        Pure-SQL KPI calculations (CTE-heavy, no N+1)
│   ├── registry.py          46 KPI metadata definitions + status flags
│   ├── ml_inference.py      XGBoost / scikit-learn inference wrappers
│   └── schemas.py
│
├── whatsapp/                WhatsApp intelligence  (/whatsapp)
│   ├── routes.py            Meta webhook (GET verify + POST receive) + send endpoints
│   ├── conversation_handler.py    State machine (NEW → LANG → MENU → …)
│   ├── intelligence.py      Mistral AI NLU layer
│   ├── client.py            WhatsApp Cloud API HTTP client
│   ├── session_store.py     Sessions persisted in lit_db
│   └── templates.py         Message templates + button definitions
│
├── ai/                      Mobile AI proxy  (/kirana/ai)
│   └── routes.py            Voice / handwriting / invoice OCR via Gemini
│
├── ml_models/
│   ├── artifacts/           *.pkl trained models (gitignored — store in cloud)
│   ├── results/             *.csv prediction outputs consumed by ml_adapter
│   └── kpi_models/          KPI-specific models + training script
│
├── db_generation/           Schema setup + seed scripts (one-off)
│   ├── master_db_generation_script.py
│   ├── seed_kirana_final.py
│   ├── seed_blinkit_catalog.py
│   ├── upgrade_lit_db.py
│   ├── db_cleanup_and_upgrade.py
│   ├── store27_sales_sim.py
│   └── …
│
├── admin-panel/             Vite + Tailwind + Chart.js admin frontend
│   ├── index.html
│   ├── src/api.js
│   ├── src/main.js
│   ├── src/style.css
│   └── package.json
│
├── misc/                    Scratch / debug scripts — gitignored
├── outputs/                 Runtime data dumps — gitignored
├── static/dashboard.html    HTML page served at /ui
├── temp/                    Ephemeral request storage (uploads, etc.)
└── logs/                    master.log (rotated externally)
```

### Files I want to call out

- **`config.py`** — note this is a plain `@dataclass(frozen=True)` + `os.getenv`, **not** `pydantic-settings` despite being in `requirements.txt`. Treat it as the canonical entry point for any new environment variable.
- **`serviceAccountKey.json`** — Firebase service account credentials. **Do not commit.** The `.gitignore` excludes it explicitly; a previous commit (`d8f8c46`) removed it from history.
- **`misc/`** — explicitly gitignored. Contains one-off debug/inspection scripts. Do not import from it in production code.

---

## 4. Build, run, and tooling

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ with a database named `lit_db` reachable at the URL in `DATABASE_URL`
- A Firebase project (service account JSON) for FCM
- Optional: Mistral API key, Gemini API key, WhatsApp Cloud API credentials, Razorpay credentials, Google Play service account

### Setup

```bash
python -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — DATABASE_URL, POS_SECRET_KEY, KIRANA_API_KEY, and (optional) keys for AI/WhatsApp/Razorpay
```

### Run

```bash
# Development (hot reload, single worker)
uvicorn main:app --host 0.0.0.0 --port 9000 --reload

# Production (2 workers, no reload)
uvicorn main:app --host 0.0.0.0 --port 9000 --workers 2
```

Once running:

- `http://localhost:9000/docs` — Swagger UI (auto-generated, every endpoint)
- `http://localhost:9000/redoc` — ReDoc
- `http://localhost:9000/openapi.json` — raw OpenAPI spec
- `http://localhost:9000/health` — health probe (Kirana, POS, WhatsApp status)
- `http://localhost:9000/ui` — the static HTML dashboard from `static/dashboard.html`
- `http://localhost:9000/` — root JSON describing the available modules

### Environment variables

The complete list is in [`../.env.example`](../.env.example). The ones that
matter most:

| Key | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | PostgreSQL connection string (`postgresql+psycopg2://…/lit_db`) |
| `KIRANA_API_KEY` | yes | Admin API key — also used as the `X-API-Key` header for the admin panel |
| `POS_SECRET_KEY` | yes | Signing key for POS JWTs — **change in production** |
| `POS_ALGORITHM` | no | Default `HS256` |
| `POS_TOKEN_EXPIRE_MINUTES` | no | Default `43200` (30 days) |
| `MASTER_HOST`, `MASTER_PORT`, `MASTER_DEBUG` | no | Server bind + debug switch |
| `FIREBASE_CREDENTIALS_JSON` | optional | Path (absolute or relative to repo root) to the Firebase service-account JSON. If unset, FCM is silently disabled. |
| `WHATSAPP_*` | optional | Meta Cloud API credentials + verify token |
| `MISTRAL_API_KEY`, `MISTRAL_MODEL` | optional | WhatsApp NLU + KPI explanations |
| `GEMINI_API_KEY` | optional | Backend-side proxy for the mobile app's voice / handwriting / invoice AI features |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | optional | When both unset, subscription payments fall back to **test mode** |
| `GOOGLE_PLAY_PACKAGE_NAME`, `GOOGLE_PLAY_CREDENTIALS_JSON` | optional | When both set, `/payment/mock-confirm` is blocked (production safety) |
| `TRIAL_DAYS`, `BASIC_PRICE_INR`, `PRO_PRICE_INR` | no | Subscription configuration (defaults: 14 / 200 / 500) |

Anything missing falls back to the defaults declared inside
`config.py:get_settings()`.

---

## 5. Application bootstrap

`main.py` is the single entrypoint. Reading it top-to-bottom is the fastest
way to understand the runtime.

### What happens on startup

`lifespan()` (registered via the `@asynccontextmanager`) runs once before the
first request:

1. Load `Settings` from `.env`.
2. Create a single SQLAlchemy `Engine` against `DATABASE_URL` (`pool_size=15`, `max_overflow=30`, `pool_pre_ping=True`).
3. Run `SELECT 1` to confirm the DB is reachable.
4. Build a `db_session()` context manager backed by a shared `sessionmaker`.
5. Boot `KiranaService` — loads all ML CSVs from `ml_models/results/` into pandas frames.
6. Instantiate `KiranaRepository(engine)` — this triggers the **idempotent schema bootstrap** (see §7).
7. Initialise FCM (`kirana.fcm_sender._ensure_init`) — silent no-op if no credentials.
8. Instantiate the WhatsApp stack: `WhatsAppClient`, `WhatsAppSessionStore`, `WhatsAppIntelligence`, `ConversationHandler`.
9. Start the `IntelligenceEngine` — APScheduler jobs for push notifications.
10. Attach everything to `app.state` so route handlers can pull it via `request.app.state`.

On shutdown the scheduler stops, the engine is disposed, and the Gemini HTTP
client is closed.

### `app.state` keys

| Key | Type | What it is |
| --- | --- | --- |
| `settings` | `Settings` | Frozen dataclass |
| `engine` | `sqlalchemy.Engine` | Single shared engine — use this for raw `text()` queries |
| `db_session` | `Callable -> ContextManager[Session]` | Use inside `pos/` and other ORM-style code |
| `kirana_service` | `KiranaService` | Recommendations + auth + finance |
| `wa_client`, `wa_sessions`, `wa_handler` | WhatsApp stack | Used by `whatsapp/routes.py` |
| `intelligence` | `IntelligenceEngine` | Lifecycle only — handlers don't usually need it |

### Middleware and global error handlers

- **CORS** is wide open (`allow_origins=["*"]`) — fine for internal API usage, tighten before going public.
- **Request logger**: every request is tagged with a random 8-char `request_id` and logged with method, path, status, latency in `logs/master.log`.
- **`ValueError`** → 400 `{success: false, error: "Invalid request"}`
- **`PermissionError`** → 403
- **Any other exception** → 500 `{success: false, error: "Internal server error"}` (full trace in `master.log`)

---

## 6. Routers and authentication

### Mounted prefixes

| Prefix | Module | Auth |
| --- | --- | --- |
| `/kirana` | `kirana.routes` | `X-API-Key` admin **or** `Authorization: Bearer <kirana token>` |
| `/pos` | `pos.routes` | POS JWT (`Authorization: Bearer <pos_jwt>`); `/pos/token*` are public |
| `/oltp` | `oltp.routes` | Same as `/kirana` (admin or Bearer) |
| `/kirana/kpis` | `kpis.routes` | Same as `/kirana` |
| `/whatsapp` | `whatsapp.routes` | Same as `/kirana`, **except** `/webhook` (Meta cannot send auth headers) |
| `/kirana/ai` | `ai.routes` | Same as `/kirana` |

### The two auth models

**1. Kirana auth (covers everything except `/pos/*`)** — defined inline in `kirana/routes.py:_auth()` and duplicated in each module's routes file:

```python
def _auth(request: Request) -> dict:
    s = request.app.state.settings
    api_key = request.headers.get("X-API-Key", "")
    bearer  = request.headers["Authorization"][7:] if request.headers.get("Authorization", "").startswith("Bearer ") else ""

    if api_key == s.kirana_api_key:
        return {"role": "admin", "user_id": None, "store_id": None}
    if bearer:
        user = request.app.state.kirana_service.user_by_token(bearer)
        if user: return user
    raise HTTPException(401, "Unauthorized")
```

The Kirana token is **opaque hex** (not a JWT) — created in `KiranaRepository.create_session()`, stored in `kirana_oltp.user_sessions.access_token`. Look it up via `KiranaService.user_by_token()`.

**2. POS auth** — JWT issued by `/pos/token` (username + password) or `/pos/token-from-kirana` (Bearer exchange for phone-OTP users). The signing key is `POS_SECRET_KEY`, algorithm `HS256`, 30-day expiry. Decoded in `pos.auth.decode_token`.

### Scoping helpers

- `_require_admin(user)` — 403 unless `user["role"] == "admin"`
- `_require_store(store_id, user)` — admin bypasses; store_owners must match `user["store_id"]`

These are duplicated across the route files. If you're adding a new module,
copy the `_auth` block from `kirana/routes.py` — it's the canonical version.

---

## 7. Database

### Connection model

- **One** PostgreSQL database: `lit_db`
- **One** application schema: `kirana_oltp` (31 tables)
- **One** SQLAlchemy `Engine` shared by every module
- ORM-style access in `pos/` (declarative models in `pos/models.py`)
- Raw SQL via `engine.connect()` + `text(...)` everywhere else

### Schema bootstrap

`KiranaRepository.__init__()` is the source of truth for schema migrations.
It runs **once per process** at startup (gated by a module-level flag) and
uses a **PostgreSQL advisory lock** so multiple uvicorn workers serialise on
the same `ALTER TABLE` statements instead of deadlocking:

```python
conn.execute(text("SELECT pg_advisory_xact_lock(1919191919)"))
```

All DDL is `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` — running the bootstrap against an already-migrated DB is a no-op.

Tables created or extended by the bootstrap:

`users` (adds full_name, password_hash, fcm_token, phone_number, firebase_uid …),
`user_sessions`, `issue_report`, `user_fcm_tokens` (multi-device tokens),
`app_activity` (lifecycle events), `subscription`, `referral_*` tables,
`basket`, `basket_item`, `association`, `app_activity`, `cashflow_requests`,
and more.

### Table allowlists for the OLTP generic CRUD

`oltp/routes.py:_ALLOWED_TABLES` is the gatekeeper — any `/oltp/<table>` request for a table not in this set returns 404. Tables fall into three buckets in `oltp/repository.py`:

| Bucket | Tables | Behaviour |
| --- | --- | --- |
| `GLOBAL_READ_TABLES` | `calendar`, `category`, `product` | Anyone (including admins) can read; only admins can write |
| `ADMIN_ONLY_WRITE_TABLES` | `calendar`, `store` | Admin-only writes |
| `DIRECT_STORE_TABLES` | `customer`, `inventory`, `khata`, `orders`, `pricing`, `supplier`, `subscription`, … | Auto-scoped to the caller's `store_id` |
| `INDIRECT_SCOPE_TABLES` | `order_item` → `orders`, `payments` → `orders`, `purchase_items` → `purchases`, etc. | Scope inherited from a parent table via a join |

### Schema quirks the mobile app already accommodates (do not "fix")

- `product.barcode` is **globally unique** across stores — shared catalogue.
- `inventory` has **no `reorder_level` column** — clients must not send one on insert.
- `pricing.price` is nullable but `pricing.valid_from` is `NOT NULL`.
- `GET /oltp/pricing` returns 422 if you pass `limit` — the OLTP repo strips reserved params (`limit`, `offset`) from filter dicts; if a client adds `limit` explicitly, the underlying query rejects it.
- `orders.user_id` references `kirana_oltp.users` (NOT the legacy `kirana_app_users`).

---

## 8. Module-by-module

### 8.1 `kirana/` — auth, recommendations, finance, subscription, admin

This is the biggest module by far. Key seams:

| File | Role |
| --- | --- |
| `routes.py` | One FastAPI `APIRouter` mounted at `/kirana`. Every endpoint lives here. |
| `service.py` | `KiranaService` orchestrates the ML adapter, the repository, and the Mistral agents. Inject collaborators via `__init__`, not lazy `ref.read` — there is no Riverpod here. |
| `repository.py` | All PostgreSQL queries that aren't KPI-specific. Runs the schema bootstrap. |
| `schemas.py` | One Pydantic model per request/response — single file, no submodules. |
| `ml_adapter.py` | Loads the five prediction CSVs from `ml_models/results/`, builds `RecommendationItem`s. |
| `fcm_sender.py` | Wrapper around `firebase-admin` — `send_to_token()` returns `True` / `False` / `"UNREGISTERED"` so the caller can prune stale tokens. |
| `campaigns.py` | Time-of-day + area-based campaign suggestions (morning, weekend, festival, monthly, school, general). |
| `agents/mistral_explainer.py` | Mistral chat completion for natural-language explanations of recommendations. |
| `agents/query_agent.py` | Mistral-based intent extraction for natural-language KPI queries. |
| `intelligence/engine.py` | APScheduler engine — see §8.6. |

### 8.2 `pos/` — billing, products, payments

The only module that uses the SQLAlchemy ORM. Models in `pos/models.py` map
directly to the `kirana_oltp.*` tables — `KiranaStore`, `KiranaProduct`,
`KiranaPricing`, `KiranaInventory`, `KiranaOrder`, `KiranaOrderItem`,
`KiranaPayment`.

`pos/routes.py` exposes:

- `POST /pos/token` (form-encoded `username` + `password`) — issues the POS JWT.
- `POST /pos/token-from-kirana` — exchange Kirana Bearer for a POS JWT (used by phone-OTP users who have no password).
- `GET /pos/me` — current POS user from the JWT.
- `GET /pos/stores`, `/pos/stores/{id}`, `/pos/categories`.
- `GET /pos/products`, `GET /pos/products/barcode/{barcode}`.
- `POST /pos/orders` — places an order **and** auto-deducts stock in the same transaction.
- `GET /pos/orders`, `GET /pos/orders/{id}` — history with filters.
- `POST /pos/payments` — explicit payment recording (Cash, UPI, Card, Credit).
- `GET /pos/reports/daily-sales` — used by the mobile app's Overview tab.

### 8.3 `oltp/` — generic CRUD over the OLTP tables

A thin, schema-aware CRUD layer the mobile app uses for anything that isn't
worth a custom endpoint:

```
GET    /oltp/{table}?store_id=…   → {rows: [...]}
POST   /oltp/{table}              → {row: {...}}
PATCH  /oltp/{table}?<pk>         → {row: {...}}
DELETE /oltp/{table}?<pk>         → {deleted: true}
GET    /oltp/schema               → list every table's columns + foreign keys
GET    /oltp/schema/{table}       → one table's metadata
```

The repository reflects table metadata via `sqlalchemy.MetaData(...).reflect()`
once per engine and caches it. Most behaviour is driven by `TableMeta`
records (`oltp/repository.py:TableMeta`):

- `read_scope` and `write_scope` (`global` / `store` / `admin`) are computed at startup based on `GLOBAL_READ_TABLES` / `ADMIN_ONLY_WRITE_TABLES` / `DIRECT_STORE_TABLES` / `INDIRECT_SCOPE_TABLES`.
- `column_map` (e.g. `inventory_batch.quantity → qty_in_stock`) translates frontend-friendly keys to the actual DB column.

### 8.4 `kpis/` — calculator + registry + ML inference

Three-layer design:

1. **`registry.py`** — `KPIDef` dataclass entries describing every KPI. Each entry either points at a calculator function (`status=STATUS_OK`) or declares its data is unavailable (`status=STATUS_DATA_UNAVAILABLE`, with a `missing_data` string explaining what's needed). The UI uses that flag to grey out the card and show a "needs setup" tile.
2. **`calculator.py`** — One function per KPI. Each returns a flat dict matching the corresponding Pydantic schema. All queries are CTE-style, no N+1. Trend comparison uses `_trend(current, previous, higher_is_better)`.
3. **`ml_inference.py`** — Wraps the trained models in `ml_models/kpi_models/artifacts/` for the KPIs that need predictive overlays.

The same registry powers two endpoints:

- `GET /kirana/kpis/registry` — returns the full 46-KPI catalogue (the mobile app's KPI subscription screen reads this).
- `GET /kirana/kpis/{slug}?store_id=…&days=…` — dispatches to `calc.{calc_function}` based on the registry.

### 8.5 `whatsapp/` — Meta Cloud API + Mistral NLU

| File | Role |
| --- | --- |
| `routes.py` | Webhook (`GET` verify + `POST` receive), manual send endpoints, session management. |
| `client.py` | `WhatsAppClient` — wrapper around the Meta Cloud API (graph.facebook.com). |
| `session_store.py` | Per-phone session persisted in the public schema of `lit_db`. |
| `conversation_handler.py` | The state machine — `NEW → LANG_PENDING → MAIN_MENU → SALES_MENU / ANALYTICS_MENU → IDLE`. |
| `intelligence.py` | `WhatsAppIntelligence` — Mistral chat completion for unstructured Q&A. |
| `templates.py` | Hard-coded WhatsApp templates + button definitions. |

The webhook receiver runs synchronously per message (with `loop.run_in_executor` to keep the event loop unblocked) and returns 200 immediately — the response payload doesn't matter for Meta, only the HTTP status.

### 8.6 `kirana/intelligence/` — scheduled push notifications

`IntelligenceEngine` wraps an `AsyncIOScheduler` (timezone `Asia/Kolkata`). Started in the FastAPI lifespan, stopped on shutdown.

Jobs registered in `engine.py:_setup_jobs()`:

| Job | Schedule | Trigger function |
| --- | --- | --- |
| `morning_greeting` | 08:00 daily | Greeting + AI tip |
| `evening_summary` | 21:00 daily | Daily sales summary |
| `distributor_due` | 09:00 daily | Distributor payment reminders |
| `expiry_alert` | 09:15 daily | Items expiring soon |
| `low_stock_alert` | 09:30 daily | Below-threshold items |
| `overdue_udhaar` | 10:00 daily | Customers with overdue credit |
| `weekly_report` | Mon 09:00 | Last-week summary |
| `inactive_customer` | Wed 10:00 | Re-engagement nudges |
| `feature_discovery` | Fri 11:00 | Surface unused features |
| `abandoned_cart` | every 5 min | POS cart pings → re-engagement |
| `snapshot_refresh` | 02:00 daily | Refresh inventory_snapshots for ML |
| `ml_refresh` | every 6 h | Reload ML CSVs |

Each job goes through `_dispatch(trigger_name, trigger_fn, dedupe)`:

1. Fetch active stores from `IntelligenceRepository.get_active_stores()`.
2. For each store, skip if `was_sent_today` / `was_sent_this_week` returns true.
3. Call the trigger function — returns `None` to skip or a `(title, body, data)` tuple.
4. `send_to_token(token, …)` → on `UNREGISTERED`, prune the token from `user_fcm_tokens`.
5. Record the send in `intelligence_sent` for deduplication.

### 8.7 `ai/` — Gemini proxy for the mobile app's AI entry features

Three endpoints under `/kirana/ai/`:

- `POST /kirana/ai/voice` — voice → cart items
- `POST /kirana/ai/handwrite` — image of a handwritten list → cart items
- `POST /kirana/ai/invoice` — image/PDF of a supplier invoice → purchase order line items

All three forward to Gemini via a **shared, reused** `httpx.AsyncClient`
(`get_gemini_client()`) with HTTP/2 + a warm TLS connection — the docstring
says this saves ~200 ms per call vs. opening a fresh connection each time.

The Gemini API key never leaves the server.

---

## 9. ML stack

| Where | What |
| --- | --- |
| `ml_models/artifacts/*.pkl` | Trained scikit-learn / XGBoost models. Gitignored — store in cloud storage; deploy via a build step. |
| `ml_models/results/*.csv` | Prediction outputs for the last training run. **These** are what the recommendations API actually reads. |
| `ml_models/kpi_models/` | KPI-specific models + the training script `train_kpi_models.py`. |
| `kirana/ml_adapter.py` | Loads the five prediction CSVs (`stockout_predictions`, `velocity_predictions`, `margin_predictions`, `reorder_recommendations`, `deadstock_predictions`) and merges them into a single per-(store,product) frame. |

The adapter applies two guardrails:

- `MIN_VELOCITY_FOR_STOCKOUT = 0.3` — a stockout risk on a SKU that sells <0.3 units/day is filtered out as noise.
- `MAX_VELOCITY_FOR_DEADSTOCK = 0.3` — dead-stock only flags when both the model says so AND recent sales confirm.

`KiranaService.bootstrap()` calls `MLAdapter(...)` once. The scheduled `ml_refresh` job re-loads the CSVs every 6 hours so a retraining run becomes visible without a restart.

---

## 10. Conventions

### File and module layout

- Every module has a `routes.py` (one `APIRouter`) and at least one of `service.py` / `repository.py` / `crud.py`.
- Pydantic schemas live in a single `schemas.py` per module — do not split.
- Module `__init__.py` files are empty (single newline). Don't put anything in them.

### Naming

- Modules and files: `snake_case`.
- Classes: `UpperCamelCase` (`KiranaService`, `WhatsAppClient`, `IntelligenceEngine`).
- Functions and variables: `snake_case`.
- Pydantic request models: `<Action>Request` (e.g. `LoginRequest`, `UdhaarAddRequest`).
- Pydantic response models: `<Resource>Response` (e.g. `RegisterStoreOwnerResponse`).
- Database tables and columns: `snake_case` with singular nouns (`store`, `customer`, `order_item`). The `orders` table is the only plural exception (it predates the convention).

### SQL access

- For raw SQL use `from sqlalchemy import text` and `engine.connect()` as a context manager — never leak connections.
- Always bind parameters with `:name`; never f-string into SQL.
- For batch operations prefer one CTE-driven query over N+1.
- `engine.connect()` is autocommit-off by default — call `conn.commit()` after a write or use `engine.begin()` if you want auto-commit semantics.

### Pydantic + types

- Pydantic v2 syntax everywhere (`model_dump()`, not `.dict()`).
- Use `from __future__ import annotations` at the top of new files so forward references work without `TYPE_CHECKING` blocks.
- Optional fields are `Optional[T] = None`; defaults belong in the schema, not the route.

### Dependency injection

- Inject collaborators via `request.app.state.<thing>` — that's where the lifespan attached them.
- Don't import the engine module-globally; always pull it off `request.app.state.engine`.

### Logging

- One named logger per module: `logger = logging.getLogger("kpis.routes")`.
- `INFO` for happy-path events the operator should see.
- `WARNING` for recoverable misconfigurations (e.g. FCM credentials missing).
- `exception("…")` for anything that hit the global error handler.

---

## 11. Adding a new endpoint — checklist

1. **Decide the prefix.** Auth / customer / KPI → `kirana/`. POS-only → `pos/`. Generic table CRUD → just add the table name to `oltp/routes.py:_ALLOWED_TABLES` and you're done.
2. **Schema first.** Add Pydantic request/response models to the module's `schemas.py`.
3. **Repository / service.** Add the SQL query in `repository.py` (or `crud.py` for POS). Pure SQL, parameter-bound.
4. **Route handler.** Copy the `_auth` pattern from `kirana/routes.py` if you're outside `kirana/`. Use `Depends(_auth)` or `Depends(_require_admin)` / `Depends(_require_store)` as needed.
5. **Wire it up.** New routes inside an existing router are auto-mounted. New modules need an `app.include_router(...)` line in `main.py:create_app()`.
6. **Document.** Update [`../API_REFERENCE.md`](../API_REFERENCE.md) with the new endpoint.
7. **Test from the mobile client.** The Flutter app's `ApiClient` (in the `kirana_ai` repo) is the only real integration test; hitting Swagger UI at `/docs` covers the contract.

---

## 12. Known gotchas

- **Two uvicorn workers can race on schema bootstrap.** That's why `_ensure_schema()` takes a PG advisory lock (`pg_advisory_xact_lock(1919191919)`). Don't remove it.
- **`KiranaRepository` is cheap to instantiate** because the schema bootstrap is gated by `_schema_initialized`. Don't try to "cache" the repo — just `KiranaRepository(engine)` every time.
- **`SELECT 1` on startup will fail loudly** if `DATABASE_URL` is wrong. There is no quiet retry — fix the URL.
- **FCM is silently disabled** if `FIREBASE_CREDENTIALS_JSON` is missing or points at a non-existent file. Check `kirana.fcm` logs to confirm initialisation succeeded.
- **`firebase_admin.initialize_app()` is called once** — the second call would raise. `_ensure_init()` checks `firebase_admin._apps` to guard against re-initialisation.
- **Mistral / Gemini / WhatsApp are all best-effort.** Missing credentials produce a 503 from the relevant endpoint but the rest of the service runs.
- **`/payment/mock-confirm` is a backdoor for testing.** It is automatically blocked when both `GOOGLE_PLAY_PACKAGE_NAME` and `GOOGLE_PLAY_CREDENTIALS_JSON` are set. Do not unblock it in production.
- **The legacy `kirana_app_users` / `public.*` tables.** `KiranaRepository._ensure_schema()` migrates any data found there into `kirana_oltp.*`. Do not write new code against the legacy names.
- **Generic CRUD only on `_ALLOWED_TABLES`.** Add new tables explicitly when needed — otherwise `/oltp/<new_table>` returns 404 by design.
- **APScheduler is timezone-aware** (`Asia/Kolkata`). Don't mix `datetime.utcnow()` and the scheduler's "now" — use `datetime.now(timezone.utc)` and let the scheduler convert.

---

## 13. Where to look first when something breaks

| Symptom | Open first |
| --- | --- |
| `/health` returns DB error on startup | `main.py:lifespan` — the `SELECT 1` failed |
| Schema bootstrap deadlocks | `kirana/repository.py:_ensure_schema` — the advisory lock |
| Login returns 401 even with correct password | `kirana/repository.py:authenticate_user` |
| POS token works for one endpoint but not another | `pos/routes.py:_resolve_store_scope` |
| `/kirana/stores/{id}/recommendations` returns empty | `kirana/ml_adapter.py` — check `ml_models/results/*.csv` exist |
| KPI returns `status="data_unavailable"` | `kpis/registry.py` — the entry has a `missing_data` reason |
| WhatsApp webhook returns 200 but no reply | `whatsapp/conversation_handler.py` — state machine + log |
| Push notifications not arriving | `kirana/fcm_sender.py` logs at startup — credentials path issue |
| Scheduler not firing | `kirana/intelligence/engine.py` — `start()` was not called or process is single-worker reload mode |
| Gemini endpoints time out | `ai/routes.py:get_gemini_client` — the shared client might be in a bad state; restart |

---

## 14. Related documents

- [`../README.md`](../README.md) — high-level overview
- [`../API_REFERENCE.md`](../API_REFERENCE.md) — exhaustive HTTP contract (every endpoint, every payload)
- [`../.env.example`](../.env.example) — full list of environment variables

This document is the **mental map**. The two above are the **reference manuals**.
