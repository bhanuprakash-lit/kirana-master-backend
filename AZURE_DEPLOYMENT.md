# Kirana Backend — Azure Container App Deployment Guide

> Last updated: 2026-05-28  
> Infra: Azure Container Apps + Azure Container Registry + Azure Database for PostgreSQL Flexible Server

---

## 1. Azure Resources Inventory

| Resource type | Name | Resource Group |
|---|---|---|
| Container Registry | `crlohiyakirana` | `rg-lohiya-outlet-dev` |
| Container App Environment | `cae-lohiya-outlet` | `rg-lohiya-outlet-dev` |
| Container App | `ca-lohiya-outlet` | `rg-lohiya-outlet-dev` |
| PostgreSQL Flexible Server | `psql-lohiya-kirana` | `rg-lohiya-outlet-dev` |
| Database | `db-kirana-dev` | — |

**ACR Login Server:** `crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io`  
**DB Host:** `psql-lohiya-kirana.postgres.database.azure.com`  
**DB User / Pass:** `psqladmin` / `Lohiya@2026`  
**App Port:** `9000` (uvicorn, also what the Dockerfile exposes)

> The target-port mismatch was a past bug — the history shows `--target-port 8000` was tried first, then corrected. Always use **9000**.

---

## 2. One-Time Setup (already done — skip on re-deploy)

### 2.1 Create Resource Group
```cmd
az group create --name rg-lohiya-outlet-dev --location eastus
```

### 2.2 Create Container Registry
```cmd
az acr create --resource-group rg-lohiya-outlet-dev --name crlohiyakirana --sku Basic
```

### 2.3 Create Container App Environment
```cmd
az containerapp env create --name cae-lohiya-outlet --resource-group rg-lohiya-outlet-dev --location eastus
```

### 2.4 Create PostgreSQL Flexible Server
```cmd
az postgres flexible-server create ^
  --resource-group rg-lohiya-outlet-dev ^
  --name psql-lohiya-kirana ^
  --admin-user psqladmin ^
  --admin-password "Lohiya@2026" ^
  --sku-name Standard_B1ms ^
  --tier Burstable ^
  --public-access 0.0.0.0
```

### 2.5 Create the Database
```cmd
az postgres flexible-server db create ^
  --resource-group rg-lohiya-outlet-dev ^
  --server-name psql-lohiya-kirana ^
  --database-name db-kirana-dev
```

---

## 3. Database Bootstrap (IMPORTANT — must run once on a fresh Azure DB)

The app does **not** auto-create the full schema on startup. `KiranaRepository.__init__` only runs the *auth extension* migrations (adds columns to existing tables). The base schema, OLAP schema, triggers, and v6 KPI tables must be applied manually.

### Step-by-step order

1. **Base schema + OLAP schema** (creates all core tables)
2. **upgrade_lit_db.py** (adds columns, partitions, triggers, intelligence_log, etc.)
3. **v6_schema_extensions.py** (KPI support tables: footfall, khata, opex, subscription, etc.)

### Running against Azure DB

All three scripts have hard-coded `localhost` credentials. Override them before running:

```cmd
set DB_HOST=psql-lohiya-kirana.postgres.database.azure.com
set DB_USER=psqladmin
set DB_PASSWORD=Lohiya@2026
set DB_NAME=db-kirana-dev
```

Then temporarily edit the top of each script (or pass as env vars if the script supports it):

```python
DB_NAME     = os.environ.get("DB_NAME",     "db-kirana-dev")
DB_USER     = os.environ.get("DB_USER",     "psqladmin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Lohiya@2026")
DB_HOST     = os.environ.get("DB_HOST",     "psql-lohiya-kirana.postgres.database.azure.com")
DB_PORT     = os.environ.get("DB_PORT",     "5432")
```

#### Run in order:
```cmd
python db_generation\master_db_generation_script.py
python db_generation\upgrade_lit_db.py
python db_generation\v6_schema_extensions.py
```

> `db_cleanup_and_upgrade.py` is a **data-cleanup script** targeting old local dummy data (stores 1-21, users 1-7 & 39-51). Do **not** run this on the fresh Azure DB — it deletes specific rows by ID and assumes the local seeded dataset.

### What `KiranaRepository` auto-runs on startup (no action needed)

When the app boots, `main.py → KiranaRepository(engine)` automatically:
- Adds auth columns to `kirana_oltp.users` (full_name, password_salt, etc.)
- Creates `user_sessions`, `issue_report`, `user_fcm_tokens` tables
- Creates `user_prefs`, `store_defaults` tables
- Migrates any legacy public-schema tables to `kirana_oltp`

These are idempotent — safe to run repeatedly.

---

## 4. Build & Push Docker Image

```cmd
:: Login to ACR
az acr login --name crlohiyakirana

:: Build the image (from repo root)
docker build -t crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v1 .

:: Push
docker push crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v1
```

For subsequent releases, bump the tag (`v2`, `v3`, or use the build ID):
```cmd
docker build -t crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v2 .
docker push crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v2
```

---

## 5. Deploy / Create the Container App

### First-time create
```cmd
az containerapp create ^
  --name ca-lohiya-outlet ^
  --resource-group rg-lohiya-outlet-dev ^
  --environment cae-lohiya-outlet ^
  --image crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v1 ^
  --ingress external ^
  --target-port 9000 ^
  --registry-server crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io ^
  --registry-username crlohiyakirana ^
  --registry-password "<ACR_PASSWORD>" ^
  --env-vars ^
    DATABASE_URL="postgresql+psycopg2://psqladmin:Lohiya@2026@psql-lohiya-kirana.postgres.database.azure.com:5432/db-kirana-dev" ^
    MASTER_PORT=9000 ^
    WORKERS=2
```

> **Bug from history:** `--target-port 8000` was used initially, which caused the health check to fail because uvicorn binds to 9000. Always use `--target-port 9000`.

---

## 6. Update Env Vars (after deploy or to fix broken deploy)

```cmd
az containerapp update ^
  --name ca-lohiya-outlet ^
  --resource-group rg-lohiya-outlet-dev ^
  --set-env-vars ^
    "DATABASE_URL=postgresql+psycopg2://psqladmin:Lohiya@2026@psql-lohiya-kirana.postgres.database.azure.com:5432/db-kirana-dev" ^
    MASTER_PORT=9000 ^
    WORKERS=2 ^
    KIRANA_API_KEY=<your-key> ^
    GEMINI_API_KEY=<your-key> ^
    MISTRAL_API_KEY=<your-key> ^
    WHATSAPP_ACCESS_TOKEN=<token> ^
    WHATSAPP_PHONE_NUMBER_ID=<id> ^
    WHATSAPP_BUSINESS_ACCOUNT_ID=<id> ^
    POS_SECRET_KEY=<random-secret>
```

> Note: `DATABASE_URL` must be quoted because `@` in the value confuses the CLI parser if left unquoted. The history shows this was a problem: the URL was initially passed unquoted and rejected.

---

## 7. Update Image (re-deploy after code change)

```cmd
:: Push new image first
docker build -t crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v2 .
docker push crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v2

:: Update container app to use new image
az containerapp update ^
  --name ca-lohiya-outlet ^
  --resource-group rg-lohiya-outlet-dev ^
  --image crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:v2
```

---

## 8. Get the App URL

```cmd
az containerapp show ^
  --name ca-lohiya-outlet ^
  --resource-group rg-lohiya-outlet-dev ^
  --query properties.configuration.ingress.fqdn
```

The FQDN will look like:  
`ca-lohiya-outlet.<random>.eastus.azurecontainerapps.io`

Verify it's up:
```
https://<fqdn>/health
https://<fqdn>/docs
```

---

## 9. View Logs

```cmd
:: Tail recent logs
az containerapp logs show -n ca-lohiya-outlet -g rg-lohiya-outlet-dev

:: Stream live logs
az containerapp logs show -n ca-lohiya-outlet -g rg-lohiya-outlet-dev --follow
```

---

## 10. All Required Environment Variables

| Variable | Example / Default | Required |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://psqladmin:Lohiya@2026@psql-lohiya-kirana.postgres.database.azure.com:5432/db-kirana-dev` | **YES** |
| `MASTER_PORT` | `9000` | YES |
| `WORKERS` | `2` | no (default 2) |
| `KIRANA_API_KEY` | `kirana-dev-key` | no (has default) |
| `GEMINI_API_KEY` | from Google AI Studio | YES for AI features |
| `MISTRAL_API_KEY` | from Mistral | YES for WhatsApp AI |
| `MISTRAL_MODEL` | `mistral-small-latest` | no |
| `WHATSAPP_ACCESS_TOKEN` | from Meta | YES for WhatsApp |
| `WHATSAPP_PHONE_NUMBER_ID` | from Meta | YES for WhatsApp |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | from Meta | YES for WhatsApp |
| `WHATSAPP_VERIFY_TOKEN` | `kirana_verify_token` | no (has default) |
| `POS_SECRET_KEY` | random long string | YES (use a real secret in prod) |
| `POS_TOKEN_EXPIRE_MINUTES` | `43200` (30 days) | no |
| `RAZORPAY_KEY_ID` | from Razorpay dashboard | for payments |
| `RAZORPAY_KEY_SECRET` | from Razorpay dashboard | for payments |
| `TRIAL_DAYS` | `14` | no |
| `BASIC_PRICE_INR` | `200` | no |
| `PRO_PRICE_INR` | `500` | no |
| `GOOGLE_PLAY_PACKAGE_NAME` | `com.yourcompany.kirana_ai` | for IAP |
| `GOOGLE_PLAY_CREDENTIALS_JSON` | path to service account JSON | for IAP |

> `serviceAccountKey.json` (Firebase admin SDK) is mounted as a volume in docker-compose. In Azure Container Apps, either bake it into the image or store it in Azure Key Vault and mount it as a secret.

---

## 11. Known Errors Encountered (and fixes)

### Health check fails / container restarts immediately
**Cause:** `--target-port` was set to `8000` but the app listens on `9000`.  
**Fix:** `az containerapp update --target-port 9000`

### `DATABASE_URL` env var not picked up
**Cause 1:** Passed unquoted — the `@` sign in the URL was misinterpreted by the CLI.  
**Fix:** Always quote the value: `"DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db"`

**Cause 2:** Both `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` (individual vars) and `DATABASE_URL` were set. The app only reads `DATABASE_URL`.  
**Fix:** Set `DATABASE_URL` as a single variable. The individual `DB_*` vars are not used by the app (they were a leftover pattern from an earlier version).

### App starts but KPIs return `data_unavailable`
**Cause:** v6 schema extension tables (footfall, khata, opex, etc.) were never created on the Azure DB.  
**Fix:** Run `python db_generation/v6_schema_extensions.py` against the Azure DB (see Section 3).

### `relation "kirana_oltp.X" does not exist` on startup
**Cause:** The base schema was never seeded. The app's `KiranaRepository` only adds *extra* columns to existing tables — it does not create the core schema from scratch.  
**Fix:** Run scripts in order: `master_db_generation_script.py` → `upgrade_lit_db.py` → `v6_schema_extensions.py`.

---

## 12. CI/CD via Azure DevOps (azure-pipelines.yml)

The pipeline is defined in `azure-pipelines.yml` but has placeholder values that need filling:

```yaml
dockerRegistryServiceConnection: 'YOUR_ACR_SERVICE_CONNECTION_NAME'
azureSubscription: 'YOUR_AZURE_SUBSCRIPTION_CONNECTION'
webAppName: 'YOUR_APP_SERVICE_NAME'
```

**To wire it up:**
1. In Azure DevOps → Project Settings → Service Connections:
   - Create a Docker Registry connection to `crlohiyakirana.azurecr.io` → name it, then paste that name into `dockerRegistryServiceConnection`
   - Create an Azure Resource Manager connection → paste into `azureSubscription`
2. Set `webAppName` to `ca-lohiya-outlet` (or your App Service name if using App Service for Containers instead of Container Apps — note: the pipeline uses `AzureWebAppContainer@1` which targets App Service, not Container Apps)
3. Env vars / secrets are not injected by the pipeline — set them manually via `az containerapp update --set-env-vars` or via Azure Portal.

> The current pipeline deploys to **App Service for Containers** (`AzureWebAppContainer@1`). If you want to target Container Apps instead, replace that task with `AzureCLI@2` running `az containerapp update --image ...`.

---

## 13. Quick Re-Deploy Checklist

When pushing new code:

- [ ] `docker build -t <acr>/kirana-backend:vN .`
- [ ] `docker push <acr>/kirana-backend:vN`
- [ ] `az containerapp update --name ca-lohiya-outlet --resource-group rg-lohiya-outlet-dev --image <acr>/kirana-backend:vN`
- [ ] `az containerapp logs show -n ca-lohiya-outlet -g rg-lohiya-outlet-dev` — confirm no startup errors
- [ ] Hit `/health` endpoint and verify `"status": "ok"`

---

## 14. Database Schema Status (as of 2026-05-28)

### What actually happened during the first deploy

The Gemini session (`implement_logs`) shows the following sequence:

1. App crashed → `psycopg2.OperationalError: connection to server at "localhost"` — `DATABASE_URL` was not set
2. `DATABASE_URL` was set → app crashed again → `ProgrammingError: schema "kirana_oltp" does not exist` trying to `ALTER TABLE`
3. Gemini created a **temporary partial bootstrap script** (`db_generation/init_azure_db.py`) and ran it. This created the `kirana_oltp` / `kirana_olap` schemas and ~12 core tables (store, users, category, product, inventory, orders, order_item, pricing, customer, khata, inventory_snapshots, subscription)
4. App started successfully → `"All services ready — http://0.0.0.0:9000"` logged at 15:50:17 UTC on 2026-05-27
5. `kirana/repository.py._ensure_schema()` ran and added auth columns + more tables on top

> **`init_azure_db.py` no longer exists** in the repo — it was a temporary file created by Gemini and not committed.

### Current Azure DB state: PARTIAL

The Azure DB `db-kirana-dev` was bootstrapped with a partial script. The following are **missing**:

| Missing tables | From which script |
|---|---|
| `supplier`, `product_supplier`, `promotion`, `payments`, `purchases`, `purchase_items`, `inventory_movements` | `master_db_generation_script.py` |
| Inventory trigger, OLAP partitioned table + materialized view, `kpi_tier_config`, `store_association` | `upgrade_lit_db.py` |
| `footfall`, `scheme`, `scheme_claim`, `calendar`, `inventory_batch`, `shelf_planogram`, `opex`, `return_to_vendor`, `crm_deals`, `marketing_spend`, `ap_ar_aging`, `process_events` | `v6_schema_extensions.py` |

### Action needed

Run these **three scripts** against the Azure DB to complete the schema (all are idempotent — safe to re-run):

```cmd
:: Set env vars so the scripts connect to Azure instead of localhost
set DATABASE_URL=postgresql://psqladmin:Lohiya@2026@psql-lohiya-kirana.postgres.database.azure.com:5432/db-kirana-dev

python db_generation\master_db_generation_script.py
python db_generation\upgrade_lit_db.py
python db_generation\v6_schema_extensions.py
```

> The scripts have hard-coded `localhost` credentials at the top. Either temporarily edit those constants to point at Azure before running, or set `DATABASE_URL` env var and update the scripts to read from it (see Section 3 for the env var approach).

### Full script → table coverage

| Script | What it creates | Azure DB status |
|---|---|---|
| `db_generation/master_db_generation_script.py` | store, users, customer, category, product, supplier, product_supplier, pricing, promotion, orders, order_item, payments, purchases, purchase_items, inventory, inventory_movements, inventory_snapshots + OLAP base tables | **Partially applied** (via init_azure_db.py — missing ~7 tables) |
| `db_generation/upgrade_lit_db.py` | product columns, inventory trigger, OLAP partitions + materialized view, intelligence_log, cart_session, kpi_tier_config, store_association | **NOT applied** |
| `db_generation/v6_schema_extensions.py` | 12 KPI support tables (footfall, khata extended, opex, etc.) | **NOT applied** |
| `kirana/repository.py` (auto on boot) | users auth columns, user_sessions, issue_report, user_fcm_tokens, app_activity, khata_payments, basket, basket_item, user_prefs, cashflow_requests, cart_session | **Applied** (runs on every startup) |

> `db_generation/db_cleanup_and_upgrade.py` — **Do NOT run on Azure DB.** This deletes specific local test data rows by hard-coded IDs.
