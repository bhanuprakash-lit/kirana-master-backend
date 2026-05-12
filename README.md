# Kirana Master Backend

**LohiyaAI** · Unified FastAPI backend powering the Kirana AI mobile app.

---

## Overview

Single-database FastAPI service that serves the complete Kirana AI platform — authentication, AI recommendations, POS billing, inventory, procurement, WhatsApp intelligence, and 24+ production KPIs — all backed by a single PostgreSQL database (`lit_db`) with the `kirana_oltp` schema.

---

## Architecture

```
kirana-master-backend/
├── main.py                  # App factory, lifespan, middleware, router mounts
├── config.py                # Settings (pydantic-settings, .env)
├── requirements.txt
│
├── kirana/                  # AI + Auth module  (/kirana)
│   ├── routes.py            # Auth, stores, finance, preferences, support
│   ├── service.py           # Business logic
│   ├── repository.py        # DB queries (kirana_oltp schema)
│   ├── schemas.py           # Pydantic request/response models
│   ├── ml_adapter.py        # ML CSV loader + inference adapter
│   └── agents/              # Mistral AI agents (query + explainer)
│
├── pos/                     # POS module  (/pos)
│   ├── routes.py            # Orders, products, payments, reports, token exchange
│   ├── crud.py              # SQLAlchemy CRUD helpers
│   ├── models.py            # SQLAlchemy ORM models
│   ├── schemas.py           # Pydantic schemas
│   └── auth.py              # POS JWT creation + validation
│
├── oltp/                    # Generic CRUD module  (/oltp)
│   ├── routes.py            # GET/POST/PATCH/DELETE any table
│   └── repository.py        # Dynamic query builder
│
├── kpis/                    # KPI module  (/kirana/kpis)
│   ├── routes.py            # 24 KPI endpoints + registry
│   ├── calculator.py        # SQL-based KPI calculations
│   ├── registry.py          # 46 KPI metadata definitions
│   ├── ml_inference.py      # XGBoost/Scikit-Learn inference
│   └── schemas.py
│
├── whatsapp/                # WhatsApp intelligence  (/whatsapp)
│   ├── routes.py            # Meta webhook + send endpoints
│   ├── conversation_handler.py  # State machine (NEW→LANG→MENU→…)
│   ├── intelligence.py      # Mistral AI NLU layer
│   ├── client.py            # WhatsApp Cloud API client
│   ├── session_store.py     # Session persistence (PostgreSQL)
│   └── templates.py        # Message templates
│
├── ml_models/               # Trained ML model artifacts (.pkl/.json)
├── db_generation/           # Schema setup + seed scripts
├── static/                  # Dashboard HTML
└── logs/                    # master.log
```

---

## Modules & Endpoints

### `/kirana` — Auth & AI
| Method | Path | Description |
|---|---|---|
| POST | `/kirana/auth/login` | Email/password login |
| POST | `/kirana/auth/register` | Register store + owner |
| POST | `/kirana/auth/phone-login` | Firebase-verified phone login |
| POST | `/kirana/auth/fcm-token` | Upload FCM push token |
| GET | `/kirana/auth/check-username/{u}` | Username availability |
| GET | `/kirana/stores/{id}/recommendations` | ML-powered recommendations |
| GET | `/kirana/stores/{id}/snapshot` | Store snapshot |
| GET/POST | `/kirana/finance/*` | Udhaar, distributor finance |
| GET/PATCH | `/kirana/preferences` | User preferences |
| PATCH | `/kirana/stores/{id}` | Update store details |

### `/pos` — Point of Sale
| Method | Path | Description |
|---|---|---|
| POST | `/pos/token` | POS JWT (username + password) |
| POST | `/pos/token-from-kirana` | POS JWT exchange (phone-auth users) |
| GET/POST | `/pos/orders` | List / place orders |
| GET | `/pos/products` | Product catalogue |
| POST | `/pos/payments` | Record payment |
| GET | `/pos/reports/daily-sales` | Daily sales report |

### `/oltp` — Generic CRUD
| Method | Path | Description |
|---|---|---|
| GET | `/oltp/{table}` | Query any table with filters |
| POST | `/oltp/{table}` | Insert row |
| PATCH | `/oltp/{table}` | Update row |
| DELETE | `/oltp/{table}` | Delete row |

### `/kirana/kpis` — KPIs (24 production metrics)
`GET /kirana/kpis/{slug}?store_id=&days=`

Key slugs: `daily-revenue`, `gross-profit-margin`, `stockout-rate`, `dead-stock`, `repeat-customer-frequency`, `category-mix`, `inventory-turnover`, `perishable-waste`, `avg-basket-value`, `cash-leakage` …

`GET /kirana/kpis/registry` — 46 KPI metadata definitions

### `/whatsapp` — WhatsApp Intelligence
| Method | Path | Description |
|---|---|---|
| GET/POST | `/whatsapp/webhook` | Meta verification + incoming messages |
| POST | `/whatsapp/send/text` | Send text message |
| POST | `/whatsapp/send/template` | Send template message |

---

## Database

**PostgreSQL** (`lit_db`) · **Schema**: `kirana_oltp` · **31 tables**

Core tables: `store`, `users`, `customer`, `category`, `product`, `supplier`, `orders`, `order_item`, `payments`, `purchases`, `purchase_items`, `inventory`, `pricing`, `user_sessions`

Schema is bootstrapped automatically on startup via `KiranaRepository.__init__` (adds columns with `ADD COLUMN IF NOT EXISTS`, creates indexes).

---

## ML Stack

| Model | Purpose |
|---|---|
| XGBoost | Stockout risk, demand forecasting |
| Scikit-Learn | Price optimisation, margin analysis |
| Pandas | Dead stock detection, category mix |
| Mistral AI | Natural-language KPI queries, WhatsApp NLU |

ML CSVs live in `ml_models/` and are loaded at startup via `KiranaService.bootstrap()`.

---

## Getting Started

### Prerequisites
- Python 3.11+
- PostgreSQL (database named `lit_db`)

### Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your DB URL, secrets, and API keys
```

### Environment Variables (`.env`)

```env
DATABASE_URL=postgresql://user:password@localhost:5432/lit_db
POS_SECRET_KEY=your-pos-jwt-secret
POS_ALGORITHM=HS256
POS_TOKEN_EXPIRE_MINUTES=43200

MISTRAL_API_KEY=your-mistral-key
MISTRAL_MODEL=mistral-large-latest

WHATSAPP_ACCESS_TOKEN=your-meta-access-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_API_BASE_URL=https://graph.facebook.com/v19.0

HOST=0.0.0.0
PORT=9000
DEBUG=false
```

### Run

```bash
# Development
uvicorn main:app --host 0.0.0.0 --port 9000 --reload

# Production (2 workers)
uvicorn main:app --host 0.0.0.0 --port 9000 --workers 2
```

API docs available at `http://localhost:9000/docs`

---

## Built by

**LohiyaAI** — AI-powered tools for Indian retail.
