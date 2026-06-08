# Kirana AI — QA & UAT (Pre-Prod) Environments

> **Purpose:** How the QA and UAT environments are built, how they work, what they can and
> can't do, and the exact commands used to operate them.
> **Audience:** Engineering, architect, manager, and anyone asking *"how do QA/UAT work and
> how are they deployed?"*
> **Model:** Hybrid (see `AZURE_ENV_STRATEGY.md`). Region: **Central India**.
> **Contains no credentials** — safe to share. Secrets live in `.env` (DEV/QA) and Key Vault (UAT).

---

## 0. Current Status (as deployed)

| Env | Status | URL |
|---|---|---|
| **QA** | ✅ **Live** (2026-06-07) | `https://ca-lohiya-outlet-qa.purpleglacier-c71fadea.centralindia.azurecontainerapps.io` |
| **UAT** | ✅ **Live** (2026-06-08) | `https://ca-lohiya-outlet-uat.ambitiouspond-d8177a23.centralindia.azurecontainerapps.io` |

**UAT as-built:** dedicated resources in resource group **`rg-lohiya-outlet-UAT`** (an Owner
created the RG after the earlier RBAC block; note the actual name follows the dev pattern
`rg-lohiya-outlet-<env>`, not `rg-lohiya-<env>`). Server `psql-lohiya-uat` — **scaled to
GeneralPurpose `Standard_D2ds_v5`** (2 vCore) on 2026-06-08 per manager request; can scale
higher for stress windows. DB `db-kirana-uat` (schema applied, 144 OK), dedicated env
`cae-lohiya-uat`, app `ca-lohiya-outlet-uat` running the **fixed** image `v20260607-0438`
(`WORKERS=1`, leader-election), scale-to-zero (min 0 / max 2). Verified `/health` = ok, single
scheduler leader.

**External integrations (QA + UAT):** sandbox WhatsApp (test number), Mistral, and Gemini are
wired as env vars; Razorpay is dummy (no account yet). Verified `wa_send=true`, `mistral=true`.
The WhatsApp access token is a Meta token and **will expire** — refresh it when WhatsApp calls
start returning auth errors.

> **Key Vault — ALL 3 ENVS DONE (2026-06-08):** every sensitive value now lives in a per-env
> **access-policy** vault, wired into the app via a **system-assigned managed identity**
> (`secretref:` env var → `keyvaultref:` app secret, resolved at container start). **No code
> changed** — KV references inject as ordinary env-var values, so `os.getenv()` is identical.
> Verified each env boots and connects from its vault (DEV also confirmed `FCM initialized` from
> the KV-backed Firebase JSON).
>
> | Vault | RG | Secrets |
> |---|---|---|
> | `kv-lohiya-dev` | dev | 9 — adds `firebase-credentials-json`, `db-password` |
> | `kv-lohiya-qa` | dev | 7 |
> | `kv-lohiya-uat` | UAT | 7 |
>
> Common 7: `database-url`, `whatsapp-access-token`, `whatsapp-verify-token`, `mistral-api-key`,
> `gemini-api-key`, `pos-secret-key`, `kirana-api-key`. Non-secrets (ports, IDs, prices, model,
> ML dirs, CORS) stay plain env vars. The Owner's `kv-lohiya-outlet` (RBAC-mode) was **not** used
> — RBAC grants need a role assignment the RG-Contributor lacks; per-env access-policy vaults are
> self-serviceable.
>
> **To add/rotate a secret:** `az keyvault secret set --vault-name <kv> --name <n> --value <v>`
> (use `--file <path>` for multi-line/space values like the Firebase JSON), then restart the
> app's latest revision so it re-reads. Firebase is now rotated via the vault, **not**
> `_set_firebase_env.py`.

**QA as-built:** app **`ca-lohiya-outlet-qa`** (renamed from `ca-lohiya-qa` on 2026-06-08 —
Container Apps can't be renamed, so recreated + old deleted), running the **fixed** image
`v20260607-0438` (`WORKERS=1`, leader-election), scale-to-zero (min 0 / max 2), DB `db-kirana-qa`
on the shared server with full schema (`ensure_full_schema.py`, 144 OK). Sandbox WhatsApp +
Mistral + Gemini wired as env vars (Razorpay dummy). Verified `/health` = ok, single scheduler
leader, `wa_send=true`.

---

## 1. TL;DR — Environment Matrix

| | **DEV** (existing) | **QA** | **UAT (Pre-Prod)** |
|---|---|---|---|
| **Purpose** | Coding & sandbox | Functional / integration testing | Perf/stress + pre-prod sign-off |
| **Container App** | `ca-lohiya-outlet` | `ca-lohiya-outlet-qa` | `ca-lohiya-outlet-uat` |
| **Container App Env (CAE)** | `cae-lohiya-outlet` | *shares* `cae-lohiya-outlet` | `cae-lohiya-uat` (dedicated) |
| **Postgres server** | `psql-lohiya-kirana` (D4ds_v5 GP) | *shares* `psql-lohiya-kirana` | `psql-lohiya-uat` (dedicated, elastic) |
| **Database** | `db-kirana-dev` | `db-kirana-qa` | `db-kirana-uat` |
| **Resource group** | `rg-lohiya-outlet-dev` | *shares* `rg-lohiya-outlet-dev` | `rg-lohiya-uat` (dedicated) |
| **Container Registry** | `crlohiyakirana` (shared by all envs) | ← | ← |
| **Replicas** | min 1 / max 3 | min 0 / max 2 (scale-to-zero) | **min 1** / max 4 (always-on) |
| **Secrets** | **Key Vault** `kv-lohiya-dev` | **Key Vault** `kv-lohiya-qa` | **Key Vault** `kv-lohiya-uat` |
| **External APIs** | sandbox | **sandbox** | **sandbox** |
| **Data** | dev/synthetic | dev/synthetic | scrubbed-from-prod (volume-realistic) |

> **Why the asymmetry:** QA is the *cost-optimized* tier — it reuses DEV's DB server and
> CAE and scales to zero when idle. UAT is *performance-isolated* — its own server and CAE so a
> stress test can't disturb anyone, and always-on so there are no cold-start skews in load tests.

---

## 2. Naming Convention

All new resources follow **`<type>-lohiya-<env>`** where `env ∈ {dev, qa, uat, prod}`:

| Resource type | Pattern | QA | UAT |
|---|---|---|---|
| Container App | `ca-lohiya-<env>` | `ca-lohiya-outlet-qa` | `ca-lohiya-outlet-uat` |
| Container App Env | `cae-lohiya-<env>` | *(shares dev)* | `cae-lohiya-uat` |
| Postgres server | `psql-lohiya-<env>` | *(shares dev)* | `psql-lohiya-uat` |
| Database | `db-kirana-<env>` | `db-kirana-qa` | `db-kirana-uat` |
| Resource group | `rg-lohiya-<env>` | *(shares dev)* | `rg-lohiya-uat` |
| Key Vault | `kv-lohiya-<env>` | — | `kv-lohiya-uat` |

> **Grandfathered names:** DEV predates this convention, so the DEV server stays
> `psql-lohiya-kirana`, the DEV RG stays `rg-lohiya-outlet-dev`, and the DEV CAE stays
> `cae-lohiya-outlet`. QA shares those DEV resources, so it inherits the older names for the
> *server/CAE/RG* but uses the new convention for its own *Container App* and *database*.
> The shared ACR `crlohiyakirana` is intentionally one-per-everything.

---

## 3. How Each Environment Works

The application is identical across all environments — **one image, promoted between envs**
(build once in DEV, deploy the same tag to QA then UAT). What differs is *configuration*:
the database it points at, its secrets, and its scale rules. There is no per-env code branch.

```
                         crlohiyakirana  (single ACR — one image, all envs)
                                  │  same image tag promoted →
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                          ▼
   ca-lohiya-outlet         ca-lohiya-outlet-qa                ca-lohiya-outlet-uat
   (DEV, min 1)             (QA, scale-to-zero)         (UAT, min 1, always-on)
   cae-lohiya-outlet  ◄── shares ──┘                    cae-lohiya-uat (dedicated)
        │                         │                          │
        ▼                         ▼                          ▼
   psql-lohiya-kirana ◄── shares ─┘                    psql-lohiya-uat (dedicated, elastic)
   db-kirana-dev             db-kirana-qa                          db-kirana-uat
```

- **DEV & QA share one Postgres server** (`psql-lohiya-kirana`) but use **separate databases**
  (`db-kirana-dev`, `db-kirana-qa`). Separate databases give isolated data *and* isolated
  scheduler advisory locks (the leader-election lock in `kirana/intelligence/engine.py` is
  scoped per-database, so DEV's and QA's schedulers never collide).
- **UAT runs its own server** so it can be scaled up to 8+ vCores for a stress window and back
  down afterward, with zero impact on DEV/QA.
- **The intelligence scheduler** (background notifications + nightly ML retrain) runs in-process
  and is **leader-elected** — only one replica per database runs jobs. See §8.4.

---

## 4. Capabilities & Limitations

### QA — `ca-lohiya-outlet-qa`
**Capabilities**
- Full functional/integration testing of every API, the admin panel, and WhatsApp flows.
- Independent database — destructive test data never touches DEV.
- Cheapest tier: scales to zero when idle (pay only when in use).

**Limitations**
- **Not for performance testing.** Shares DEV's server (`psql-lohiya-kirana`, D4ds_v5
  GeneralPurpose) — a load test here consumes the same CPU/IOPS DEV relies on (noisy neighbor).
  Connections are *not* a concern (server `max_connections` = 1718), but throughput is shared.
- **Cold starts.** First request after idle takes a few seconds while a replica spins up.
- **Scheduler is off when scaled to zero.** With min-replicas 0 there is no always-on
  process, so scheduled notifications and the 2/3 AM ML jobs **do not fire**. If you need to
  test scheduled behavior, temporarily set `--min-replicas 1` (see §9.3).

### UAT — `ca-lohiya-outlet-uat`
**Capabilities**
- **Accurate perf/stress testing** — dedicated server + dedicated CAE, no cross-impact.
- **Elastic** — scale the DB up for a test window, down afterward (§9.4).
- Always-on (min 1 replica): no cold starts, scheduler runs, prod-like latency baseline.
- **Pre-prod config validation** — secrets in Key Vault, prod-like scale rules; this proves
  the *configuration* path, not just the load path.

**Limitations**
- Higher cost (dedicated server always provisioned; scale DB down between tests to save).
- HA / private networking / Front Door are **not** included by default (those are PROD-only;
  add to UAT only if you want a true prod mirror — see §10 open items).
- Uses sandbox external APIs, so it does not exercise real WhatsApp/Razorpay delivery paths.

---

## 5. Prerequisites (one-time, per operator)

```powershell
# Azure CLI logged in
az account show          # if it errors → az login

# Docker Desktop running on the default context (for image builds)
docker context use default

# containerapp extension present
az extension add --name containerapp --upgrade
```

---

## 6. Build QA (cost-optimized tier)

QA reuses DEV's resource group, CAE, and Postgres server. You are only creating a **new
database** and a **new Container App**.

```powershell
# ── 6.1 Create the QA database on the shared DEV server ──────────────────────
az postgres flexible-server db create `
  --resource-group rg-lohiya-outlet-dev `
  --server-name   psql-lohiya-kirana `
  --database-name db-kirana-qa

# ── 6.2 Create the QA Container App (shares the DEV CAE) ─────────────────────
# Image: reuse the latest tag already in ACR (build once, promote).
$ACR = "crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io"
$TAG = "v-latest"   # replace with the actual tag you are promoting

az containerapp create `
  --name ca-lohiya-outlet-qa `
  --resource-group rg-lohiya-outlet-dev `
  --environment    cae-lohiya-outlet `
  --image          "$ACR/kirana-backend:$TAG" `
  --target-port    9000 `
  --ingress        external `
  --registry-server $ACR `
  --min-replicas   0 `
  --max-replicas   2

# ── 6.3 Set QA configuration (env vars + secrets) ────────────────────────────
# WORKERS=1 is required (one process per container — see Dockerfile rationale).
# Point at db-kirana-qa, and use SANDBOX external credentials.
az containerapp update `
  --name ca-lohiya-outlet-qa `
  --resource-group rg-lohiya-outlet-dev `
  --set-env-vars `
    "WORKERS=1" `
    "DATABASE_URL=postgresql+psycopg2://psqladmin:<QA_DB_PASSWORD>@psql-lohiya-kirana.postgres.database.azure.com:5432/db-kirana-qa?sslmode=require" `
    "CORS_ORIGINS=*" `
    "WHATSAPP_PHONE_NUMBER_ID=<SANDBOX_WA_PHONE_ID>" `
    "WHATSAPP_ACCESS_TOKEN=<SANDBOX_WA_TOKEN>" `
    "RAZORPAY_KEY_ID=<TEST_RAZORPAY_KEY>" `
    "RAZORPAY_KEY_SECRET=<TEST_RAZORPAY_SECRET>"
```

Then **bootstrap the schema** and (optionally) **seed data** — see §8.

---

## 7. Build UAT (performance-isolated, pre-prod tier)

UAT is fully dedicated. You create a resource group, a Postgres server + database, a CAE, a
Key Vault for secrets, and the Container App.

```powershell
# ── 7.1 Resource group ───────────────────────────────────────────────────────
az group create --name rg-lohiya-uat --location centralindia

# ── 7.2 Dedicated Postgres (General Purpose for consistent IOPS) ─────────────
az postgres flexible-server create `
  --resource-group rg-lohiya-uat `
  --name           psql-lohiya-uat `
  --admin-user     psqladmin `
  --admin-password <UAT_DB_PASSWORD> `
  --sku-name       Standard_D2ds_v4 `
  --tier           GeneralPurpose `
  --storage-size   64 `
  --version        16 `
  --public-access  0.0.0.0

az postgres flexible-server db create `
  --resource-group rg-lohiya-uat `
  --server-name    psql-lohiya-uat `
  --database-name  db-kirana-uat

# ── 7.3 Dedicated Container App Environment ──────────────────────────────────
az containerapp env create `
  --name           cae-lohiya-uat `
  --resource-group rg-lohiya-uat `
  --location       centralindia

# ── 7.4 Key Vault for secrets (pre-prod hardening) ───────────────────────────
az keyvault create --name kv-lohiya-uat --resource-group rg-lohiya-uat --location centralindia

az keyvault secret set --vault-name kv-lohiya-uat --name database-url `
  --value "postgresql+psycopg2://psqladmin:<UAT_DB_PASSWORD>@psql-lohiya-uat.postgres.database.azure.com:5432/db-kirana-uat?sslmode=require"
# Repeat for: whatsapp-access-token, razorpay-key-secret, mistral-api-key, gemini-api-key, etc.

# ── 7.5 Create the Container App (always-on) ─────────────────────────────────
$ACR = "crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io"
$TAG = "v-latest"   # same tag promoted from DEV/QA

az containerapp create `
  --name ca-lohiya-outlet-uat `
  --resource-group rg-lohiya-uat `
  --environment    cae-lohiya-uat `
  --image          "$ACR/kirana-backend:$TAG" `
  --target-port    9000 `
  --ingress        external `
  --registry-server $ACR `
  --min-replicas   1 `
  --max-replicas   4 `
  --system-assigned

# ── 7.6 Grant the app's managed identity read access to Key Vault ────────────
$PRINCIPAL = az containerapp show -n ca-lohiya-outlet-uat -g rg-lohiya-uat --query identity.principalId -o tsv
az keyvault set-policy --name kv-lohiya-uat --object-id $PRINCIPAL --secret-permissions get list

# ── 7.7 Wire secrets as Key Vault references + non-secret env vars ───────────
az containerapp secret set `
  --name ca-lohiya-outlet-uat --resource-group rg-lohiya-uat `
  --secrets "database-url=keyvaultref:https://kv-lohiya-uat.vault.azure.net/secrets/database-url,identityref:system"

az containerapp update `
  --name ca-lohiya-outlet-uat --resource-group rg-lohiya-uat `
  --set-env-vars "WORKERS=1" "DATABASE_URL=secretref:database-url" "CORS_ORIGINS=*"
```

Then **bootstrap the schema** and **load scrubbed data** — see §8.

---

## 8. Schema Bootstrap, Data, Firewall & Scheduler

### 8.1 Whitelist your IP on the target server (to run scripts locally)
```powershell
$ip = (Invoke-WebRequest -Uri 'https://api.ipify.org').Content

# QA (shared DEV server):
az postgres flexible-server firewall-rule create --name psql-lohiya-kirana `
  --resource-group rg-lohiya-outlet-dev --rule-name AllowLocalDev `
  --start-ip-address $ip --end-ip-address $ip

# UAT (dedicated server):
az postgres flexible-server firewall-rule create --name psql-lohiya-uat `
  --resource-group rg-lohiya-uat --rule-name AllowLocalDev `
  --start-ip-address $ip --end-ip-address $ip
```

### 8.2 Create the full schema (idempotent — required on every new DB)
The core schema is **not** auto-created at app startup (`_ensure_schema` only *adds columns*
to existing tables). Run the bootstrap script against each new database:

```powershell
# Example: QA
$env:DB_HOST = "psql-lohiya-kirana.postgres.database.azure.com"   # UAT: psql-lohiya-uat.postgres.database.azure.com
$env:DB_USER = "psqladmin"
$env:DB_PASSWORD = "<DB_PASSWORD>"
$env:DB_NAME = "db-kirana-qa"                                            # UAT: db-kirana-uat
$env:DB_PORT = "5432"

python db_generation\ensure_full_schema.py
# Expected: "Done: NNN OK, 0 skipped/errors."
```

### 8.3 Seed data
- **QA:** dev/synthetic data — `python db_generation\migrate_to_azure.py` (⚠ truncates target
  first; point `DATABASE_URL` at `db-kirana-qa` before running).
- **UAT:** **scrubbed-from-prod** data. PII must be removed before sync (DPDP Act), and the
  scrub must **preserve row volume and distribution** so perf numbers are realistic. The
  scrub-and-sync script is a pending deliverable (§10).

### 8.4 Scheduler behaviour per env
The intelligence engine is leader-elected — only one replica per database runs jobs:
- **QA at min-replicas 0:** scheduler never runs (no always-on process). Expected.
- **QA temporarily at min-replicas 1 / UAT (always min 1):** exactly one replica acquires the
  scheduler advisory lock and runs jobs; the rest stand by. No duplicate notifications.

---

## 9. Day-to-Day Operations

### 9.1 Deploy / promote an image to QA or UAT
Build once (DEV via `.\deploy.ps1`), then promote the **same tag** to the other envs:

```powershell
# QA
az containerapp update --name ca-lohiya-outlet-qa  --resource-group rg-lohiya-outlet-dev `
  --image "crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:<TAG>"

# UAT
az containerapp update --name ca-lohiya-outlet-uat --resource-group rg-lohiya-uat `
  --image "crlohiyakirana-c8eye2huc9g2b6h6.azurecr.io/kirana-backend:<TAG>"
```

### 9.2 View logs / get the URL
```powershell
az containerapp logs show -n ca-lohiya-outlet-qa  -g rg-lohiya-outlet-dev --tail 50      # or --follow
az containerapp logs show -n ca-lohiya-outlet-uat -g rg-lohiya-uat        --follow

az containerapp show -n ca-lohiya-outlet-uat -g rg-lohiya-uat `
  --query properties.configuration.ingress.fqdn -o tsv
```
Look for `All services ready` and exactly one `Intelligence engine started (scheduler leader)`.

### 9.3 Turn the QA scheduler on/off
```powershell
az containerapp update -n ca-lohiya-outlet-qa -g rg-lohiya-outlet-dev --min-replicas 1   # on
az containerapp update -n ca-lohiya-outlet-qa -g rg-lohiya-outlet-dev --min-replicas 0   # back to scale-to-zero
```

### 9.4 Scale the UAT DB up for a stress test, then back down
```powershell
# Up before the test window
az postgres flexible-server update -g rg-lohiya-uat -n psql-lohiya-uat --sku-name Standard_D8ds_v4
# Down afterward to save cost
az postgres flexible-server update -g rg-lohiya-uat -n psql-lohiya-uat --sku-name Standard_D2ds_v4
```

---

## 10. Operational Guardrails (must-follow)

| # | Guardrail | Why |
|---|---|---|
| 1 | **`WORKERS=1`** on every Container App | Each worker is a separate process running the full app (scheduler + ML in memory). >1 worker = duplicate notifications, duplicate logs, 2× RAM. Scale with replicas, not workers. |
| 2 | **Don't perf-test on the shared DEV/QA server** | `psql-lohiya-kirana` is D4ds_v5 GeneralPurpose (`max_connections` = 1718), so connections are fine for functional use. But a *stress test* on QA still loads the same CPU/IOPS DEV uses — run perf/stress on UAT's dedicated server only. |
| 3 | **Sandbox/test external creds in QA & UAT** | Otherwise functional/stress tests send **real** WhatsApp messages and hit the **real** Razorpay gateway. |
| 4 | **Separate database per env** | Data isolation + isolated scheduler advisory locks. Never point two envs at the same database. |
| 5 | **Run `ensure_full_schema.py` on every new DB** | Schema is not auto-created; the app only patches columns on existing tables. |

---

## 11. Cost (approximate, per `AZURE_ENV_STRATEGY.md`)

| Tier | Est. monthly | Notes |
|---|---|---|
| Fully shared | ~$80–120 | Inaccurate perf results |
| **Hybrid (this doc)** | **~$180–250** | UAT matches prod for perf |
| Fully isolated | ~$350–500 | Gold standard |

Biggest UAT lever: keep the dedicated DB at a small SKU between tests (§9.4) and only scale up
for the test window.

---

## 12. CI/CD Mapping (target)

Branch-based promotion (to be implemented — see `AZURE_ENV_STRATEGY.md` §6):

```
develop  → DEV   (ca-lohiya-outlet)
release  → QA    (ca-lohiya-outlet-qa)   → UAT (ca-lohiya-outlet-uat)
main     → PROD  (ca-lohiya-prod)
```
> **Note:** `ARCHITECTURE.md` references Azure DevOps while the strategy doc references GitHub
> Actions — pick one before automating. Until then, promotion is manual via §9.1.

---

## 13. Open Items / Pending Decisions

1. **Data scrub-and-sync script** (PROD → UAT, PII-removed, volume-preserving) — not yet built.
2. **CI/CD tool decision** — Azure DevOps vs GitHub Actions (docs disagree).
3. **UAT prod-mirroring depth** — add HA / private networking / Front Door to UAT, or keep
   those PROD-only? Affects how much "pre-prod" really proves.
4. **IaC** — these commands should become Bicep/Terraform so envs are reproducible.
5. **Connection-pool sizing** — make the app's pool size env-driven so non-prod can run leaner
   on the shared Burstable server (currently hardcoded `pool_size=15, max_overflow=30`).

---

*Last updated: 2026-06-07. Maintained alongside `AZURE_ENV_STRATEGY.md`, `RUNBOOK.md` (DEV ops,
credentials — not committed), and `docs/AZURE_COMMANDS.md`.*
